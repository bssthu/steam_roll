#!/usr/bin/env python3
"""Steam Roll entry point. Runtime dependencies: Python 3.10+ standard library."""
import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from steam_roll.library import LibraryError, account_list, discover, image_mime, load_library, stable_read
from steam_roll.readonly import install

PROJECT = Path(__file__).resolve().parent
IDENTITY = hashlib.sha256(str(PROJECT).lower().encode()).hexdigest()[:20]


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, port, root):
        self.root = root
        self.lock = threading.Lock()
        self.cache = {}
        self.token = secrets.token_urlsafe(32)
        super().__init__(('127.0.0.1', port), Handler)

    def library(self, account, refresh=False):
        with self.lock:
            if refresh:
                self.cache.clear()
            if account in self.cache:
                result, stamp = self.cache[account]
                data = result[0]
                expired = data.get('nextLicenseExpiry') and time.time() >= data['nextLicenseExpiry']
                if expired or stamp != self.source_stamp(data['account']):
                    self.cache.clear()
            if account not in self.cache:
                accounts = account_list(self.root)
                target = account or accounts[0]['id']
                if target not in {item['id'] for item in accounts}:
                    raise LibraryError('无效的本地账号')
                for _ in range(3):
                    before = self.source_stamp(target)
                    result = load_library(self.root, target)
                    if before == self.source_stamp(target):
                        break
                else:
                    raise LibraryError('Steam 正在更新游戏库，请稍后刷新。')
                self.cache[account] = (result, before)
                self.cache[target] = (result, before)
            return self.cache[account][0]

    def source_stamp(self, account):
        user = self.root / 'userdata' / account
        paths = [self.root / name for name in ['config/loginusers.vdf', 'appcache/appinfo.vdf', 'appcache/packageinfo.vdf']]
        paths += [user / name for name in [
            'config/licensecache', 'config/localconfig.vdf', 'config/sharedconfig.vdf',
            '7/remote/sharedconfig.vdf', 'config/cloudstorage/cloud-storage-namespace-1.json',
            'config/cloudstorage/cloud-storage-namespace-1.modified.json',
        ]]
        stamp = []
        for path in paths:
            try:
                stat = path.stat()
                stamp.append((stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                stamp.append(None)
        return tuple(stamp)


class Handler(BaseHTTPRequestHandler):
    server_version = 'SteamRoll/1.0'

    def log_message(self, *_):
        pass

    def trusted(self):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            self.send_json({'error': '只允许本机访问'}, 403)
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{host}' for host in hosts}:
            self.send_json({'error': '拒绝跨站请求'}, 403)
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.send_json({'error': '拒绝跨站请求'}, 403)
            return False
        return True

    def send_data(self, body, mime, status=200, cache='no-store'):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: https://shared.fastly.steamstatic.com; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def send_json(self, payload, status=200):
        self.send_data(json.dumps(payload, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8', status)

    def do_GET(self):
        if not self.trusted():
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        try:
            if url.path == '/api/health':
                self.send_json({'app': 'steam-roll', 'version': 1, 'project': str(PROJECT),
                                'identity': IDENTITY, 'pid': os.getpid(), 'token': self.server.token})
            elif url.path == '/api/library':
                account = query.get('account', [None])[0]
                data, _ = self.server.library(account, query.get('refresh') == ['1'])
                self.send_json(data)
            elif url.path.startswith('/api/cover/'):
                raw = url.path.removeprefix('/api/cover/')
                if not raw.isdecimal():
                    self.send_json({'error': '封面不存在'}, 404)
                    return
                _, covers = self.server.library(query.get('account', [None])[0])
                path = covers.get(int(raw))
                if path is None:
                    self.send_json({'error': '封面不存在'}, 404)
                    return
                body = stable_read(path)
                mime = image_mime(body[:16])
                if mime is None:
                    self.send_json({'error': '本地封面无效'}, 404)
                    return
                self.send_data(body, mime)
            elif url.path in {'/', '/index.html', '/app.js', '/domain.js', '/style.css', '/icon.svg'}:
                name = 'index.html' if url.path == '/' else url.path[1:]
                mime = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml'}[Path(name).suffix]
                self.send_data(stable_read(PROJECT / 'web' / name), mime + '; charset=utf-8')
            else:
                self.send_json({'error': '页面不存在'}, 404)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            message = str(exc) if isinstance(exc, LibraryError) else '读取 Steam 缓存失败，请打开 Steam 游戏库完成同步后刷新。'
            self.send_json({'error': message}, 503)

    def do_POST(self):
        if not self.trusted():
            return
        if self.path != '/api/shutdown':
            self.send_json({'error': '只读服务不支持此操作'}, 405)
            return
        if not secrets.compare_digest(self.headers.get('X-Steam-Roll-Token', ''), self.server.token):
            self.send_json({'error': '无效的服务令牌'}, 403)
            return
        self.send_json({'stopped': True})
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description='Steam Roll 本机只读推荐服务')
    parser.add_argument('--port', type=int, default=17863)
    parser.add_argument('--steam-path', type=Path)
    args = parser.parse_args()
    install()
    root = args.steam_path.resolve() if args.steam_path else discover()
    with Server(args.port, root) as server:
        print(f'Steam Roll: http://127.0.0.1:{server.server_port}', flush=True)
        server.serve_forever(poll_interval=0.25)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        print(f'Steam Roll: {exc}', file=sys.stderr)
        sys.exit(1)
