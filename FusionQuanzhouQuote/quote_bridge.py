"""Offer an explicitly exported STEP to the browser on this computer."""

import json
import re
import secrets
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from time import time


ACTIVITY_URL = 'https://www.gy2025.com/#/home/proofing'
MAX_BYTES = 7 * 1024 * 1024
TTL_SECONDS = 30 * 60
ROUTE = re.compile(r'/qz-fusion/([A-Za-z0-9_-]{32})(?:/(info|step|copy\.js))?\Z')
COPY_SCRIPT = '''
document.querySelector('#copy').addEventListener('click', async () => {
  const path = document.querySelector('#path');
  path.select();
  const help = document.querySelector('#status');
  try {
    await navigator.clipboard.writeText(path.value);
    help.textContent = '已复制。点击官网上传控件，把路径粘贴到文件名栏。';
  } catch {
    help.textContent = '路径已选中。按 Ctrl+C 复制。';
  }
});
'''


def _landing(path, size, token):
    size_note = (
        '此文件超过活动页规定的 7 MiB STEP 上限。'
        if size > MAX_BYTES else
        '已加载浏览器扩展时，活动页会自动打开。也可以在官网手动选取以下 STEP。'
    )
    return f'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="qz-fusion-bridge" content="1">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>铨洲 6.8 活动报价</title>
<style>
  body {{ font: 16px/1.65 system-ui, sans-serif; color: #16233d; background: #f3f6fb;
          max-width: 680px; margin: 12vh auto 0; padding: 0 20px; }}
  main {{ background: white; padding: 28px; border: 1px solid #dbe3ef; border-radius: 16px; }}
  h1 {{ font-size: 25px; margin: 0; }}
  input {{ box-sizing: border-box; width: 100%; font: inherit; padding: 8px; margin: 5px 0 14px; }}
  a, button {{ display: inline-block; color: #15479a; background: #eff5ff; border: 1px solid #bbcbee;
               border-radius: 8px; font: inherit; padding: 6px 12px; margin: 0 6px 5px 0; cursor: pointer; }}
  p {{ overflow-wrap: anywhere; }}
</style>
<main><h1>铨洲 6.8 活动报价</h1>
<p><strong>{escape(path.name)}</strong> 已导出（{size / 1024:.0f} KiB）。{size_note}</p>
<label for="path">STEP 文件路径</label>
<input id="path" readonly value="{escape(str(path), quote=True)}">
<button type="button" id="copy">复制文件路径</button>
<a href="{ACTIVITY_URL}" target="_blank" rel="noopener noreferrer">打开 6.8 起打样</a>
<p id="status" role="status"></p></main><script src="/qz-fusion/{token}/copy.js"></script></html>'''


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def log_message(self, format, *args):
        pass

    def _reply(self, status, media_type, data):
        self.send_response(status)
        self.send_header('Content-Type', media_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy',
                         "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (ConnectionError, TimeoutError):
            pass

    def do_GET(self):
        route = ROUTE.fullmatch(self.path)
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}' or not route:
            return self._reply(404, 'text/plain', b'Not found')
        token, action = route.groups()
        if action in {'info', 'step'} and self.headers.get('X-Fusion-Bridge') != '1':
            return self._reply(403, 'text/plain', b'Forbidden')
        bridge = self.server.bridge
        with bridge.lock:
            bridge.prune()
            job = bridge.jobs.get(token)
            if not job:
                response = (410, 'text/plain; charset=utf-8',
                            '这次导入已过期。STEP 仍保存在本机，可从 Fusion 重新发起。'.encode())
            else:
                response = self._response_for(action, token, job)
        self._reply(*response)

    def _response_for(self, action, token, job):
        path, size = job['path'], job['size']
        if action == 'copy.js':
            return 200, 'text/javascript; charset=utf-8', COPY_SCRIPT.encode()
        if not action:
            page = _landing(path, size, token)
            return 200, 'text/html; charset=utf-8', page.encode()
        if action == 'info':
            info = {
                'activity': ACTIVITY_URL, 'filename': path.name, 'size': size,
                'expires': int(job['expires'] * 1000), 'claimed': job['claimed'],
                'autoConfirm': job['auto_confirm'],
            }
            return 200, 'application/json', json.dumps(info).encode()
        if job['claimed']:
            return 409, 'text/plain', b'Already handed off'
        if size > MAX_BYTES:
            return 413, 'text/plain', b'STEP exceeds automatic transfer limit'
        try:
            with path.open('rb') as step:
                data = step.read(MAX_BYTES + 1)
        except OSError:
            return 410, 'text/plain', b'STEP file unavailable'
        if len(data) != size or not data.lstrip()[:64].startswith(b'ISO-10303-21;'):
            return 409, 'text/plain', b'STEP has changed'
        job['claimed'] = True
        return 200, 'application/octet-stream', data


class QuoteBridge:
    def __init__(self):
        self.jobs = {}
        self.lock = Lock()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        self.server.bridge = self
        self.thread = Thread(target=self.server.serve_forever,
                             kwargs={'poll_interval': 0.15}, daemon=True)
        self.thread.start()

    def prune(self):
        now = time()
        expired = [token for token, job in self.jobs.items() if job['expires'] <= now]
        for token in expired:
            del self.jobs[token]

    def add(self, path, auto_confirm):
        token = secrets.token_urlsafe(24)
        size = path.stat().st_size
        with self.lock:
            self.prune()
            self.jobs[token] = {
                'path': path, 'size': size, 'expires': time() + TTL_SECONDS,
                'claimed': False, 'auto_confirm': auto_confirm,
            }
        return f'http://127.0.0.1:{self.server.server_port}/qz-fusion/{token}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
