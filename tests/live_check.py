"""Opt-in live validation: python -B tests/live_check.py. No Steam writes."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request
import argparse
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from steam_roll.library import account_list, collections, discover, read_vdf, at, image_mime
from steam_roll.licenses import decode_licenses, licensed_apps, packageinfo

parser=argparse.ArgumentParser(description='Read-only validation against the local Steam cache')
parser.add_argument('--exclude-app',type=int,action='append',default=[],help='Assert a reported stale app is absent; IDs are not stored in reports')
args=parser.parse_args()

root=discover()
files={root/'appcache/appinfo.vdf',root/'appcache/packageinfo.vdf',root/'config/loginusers.vdf',root/'steamapps/libraryfolders.vdf'}
for account in account_list(root):
    user=root/'userdata'/account['id']
    for relative in ['config/licensecache','config/localconfig.vdf','7/remote/sharedconfig.vdf','config/sharedconfig.vdf','config/cloudstorage/cloud-storage-namespace-1.json','config/cloudstorage/cloud-storage-namespace-1.modified.json']:
        path=user/relative
        if path.is_file():files.add(path)
libraries={root}
for item in at(read_vdf(root/'steamapps/libraryfolders.vdf'),'libraryfolders').values():
    if isinstance(item,dict) and item.get('path'):libraries.add(Path(item['path']))
for library in libraries:files.update((library/'steamapps').glob('appmanifest_*.acf'))
def hashes():return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
before=hashes()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
base='http://127.0.0.1:17863'
with opener.open(base+'/api/library?refresh=1',timeout=60) as response:data=json.load(response)
hidden,_,_,_=collections(root/'userdata'/data['account'])
ids={game['id'] for game in data['games']}
assert not ids.intersection(hidden),'Hidden game in public library'
licenses=decode_licenses((root/'userdata'/data['account']/'config/licensecache').read_bytes(),data['account'])
packages=packageinfo((root/'appcache/packageinfo.vdf').read_bytes(),{item.package_id for item in licenses})
eligible,_,_=licensed_apps(licenses,packages,int(time.time()))
assert ids<=eligible,'Unlicensed/expired game in public library'
assert not ids.intersection(args.exclude_app),'Reported removed/expired app is still visible'
assert all(group['id']!='hidden' for group in data['groups'])
for game in [g for g in data['games'] if g['cover']][:12]:
    with opener.open(base+game['cover']) as response:assert image_mime(response.read(16))
if hidden:
    try:opener.open(base+f'/api/cover/{min(hidden)}?account={data["account"]}')
    except urllib.error.HTTPError as error:assert error.code==404
    else:raise AssertionError('Hidden cover was served')
for aid in args.exclude_app:
    try:opener.open(base+f'/api/cover/{aid}?account={data["account"]}')
    except urllib.error.HTTPError as error:assert error.code==404
    else:raise AssertionError('Reported removed/expired cover was served')
after=hashes()
assert before==after,'Steam data changed during validation; investigate before claiming no writes'
result={'status':'LIVE_READ_ONLY_PASS','games':len(ids),'installed':sum(g['installed'] for g in data['games']),
        'localCovers':sum(bool(g['cover']) for g in data['games']),'hiddenExcluded':len(hidden),
        'filesHashed':len(before),'sourceHashesUnchanged':True,
        'allGamesHaveValidLocalLicense':True,'excludedRegressionExamples':len(args.exclude_app)}
Path('artifacts').mkdir(exist_ok=True)
Path('artifacts/live-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
