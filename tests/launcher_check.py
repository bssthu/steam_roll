"""Exercise concurrent cold launches and port collision without opening tabs."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler,HTTPServer
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]
def launch(port,stop=False):
    args=['powershell.exe','-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'launch.ps1'),'-Port',str(port),'-NoBrowser']
    if stop:args.append('-Stop')
    # A detached Windows child may inherit capture pipes and keep communicate()
    # waiting after PowerShell exits. File-backed capture avoids that ambiguity.
    output=ROOT/'artifacts'/f'launcher-{uuid.uuid4().hex}.log'
    output.parent.mkdir(exist_ok=True)
    with output.open('w',encoding='utf-8') as stream:
        result=subprocess.run(args,stdout=stream,stderr=subprocess.STDOUT,timeout=45,cwd=ROOT)
    result.stdout=output.read_text(encoding='utf-8',errors='replace')
    result.stderr=''
    return result
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
port=17864
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:results=list(executor.map(lambda _:launch(port),range(3)))
    assert all(r.returncode==0 for r in results),str([(r.stdout,r.stderr) for r in results])
    with opener.open(f'http://127.0.0.1:{port}/api/health') as response:health=json.load(response)
    assert sum('started.' in r.stdout for r in results)==1
    assert sum('Reusing' in r.stdout for r in results)==2
    assert all(str(health['pid']) in r.stdout for r in results)
finally:
    stopped=launch(port,True)
    assert stopped.returncode==0,stopped.stdout+stopped.stderr

class OtherApp(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"app":"other-app"}')
    def log_message(self,*args):pass
with HTTPServer(('127.0.0.1',0),OtherApp) as server:
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    result=launch(server.server_port)
    assert result.returncode==1,result.stdout
    with opener.open(f'http://127.0.0.1:{server.server_port}/api/health') as response:assert json.load(response)['app']=='other-app'
    server.shutdown();thread.join()
print('LAUNCHER_PASS: 3 concurrent launches, 1 service; graceful stop; unrelated port owner preserved')
