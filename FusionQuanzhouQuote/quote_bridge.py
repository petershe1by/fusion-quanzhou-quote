"""Pass quoted STEP batches to the companion extension on loopback."""

import json
import re
import secrets
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Condition, Event, Thread
from time import monotonic, time


ACTIVITY_URL = 'https://www.gy2025.com/#/home/proofing'
EXTENSION_ORIGIN = 'chrome-extension://aldbpejenommimpkooafimedojggnohl'
PORT = 49167
MAX_FILES = 100
MAX_BYTES = 7 * 1024 * 1024
TTL_SECONDS = 30 * 60
POLL_SECONDS = 18
CLIENT_PATTERN = re.compile(r'[a-z0-9-]{36}\Z')
ROUTES = {'/qz-batch/next', '/qz-batch/file', '/qz-batch/finish'}


@dataclass
class _QuoteBatch:
    files: tuple[tuple[Path, int], ...]
    auto_confirm: bool
    expires: float
    owner: str | None = None
    received: Event = field(default_factory=Event)


class _LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False


class _Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def log_message(self, format, *args):
        pass

    def _trusted(self):
        return (self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'
                and self.headers.get('Origin') == EXTENSION_ORIGIN and self.path in ROUTES)

    def _reply(self, status, body=b'', media_type='text/plain'):
        self.send_response(status)
        self.send_header('Content-Type', media_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if self._trusted():
            self.send_header('Access-Control-Allow-Origin', EXTENSION_ORIGIN)
            self.send_header('Access-Control-Allow-Methods', 'POST')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Fusion-Bridge')
            if self.headers.get('Access-Control-Request-Private-Network') == 'true':
                self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, TimeoutError):
            pass

    def do_OPTIONS(self):
        self._reply(204 if self._trusted() else 403)

    def do_POST(self):
        if not self._trusted() or self.headers.get('X-Fusion-Bridge') != '2':
            return self._reply(403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024:
                return self._reply(400)
            values = json.loads(self.rfile.read(length))
            client = values.get('client') if isinstance(values, dict) else None
            if not isinstance(client, str) or not CLIENT_PATTERN.fullmatch(client):
                return self._reply(400)
        except (ValueError, OSError):
            return self._reply(400)

        bridge = self.server.bridge
        if self.path == '/qz-batch/next':
            info = bridge.next_batch(client)
            if info is None:
                return self._reply(204)
            return self._reply(200, json.dumps(info).encode(), 'application/json')

        token = values.get('id')
        if not isinstance(token, str):
            return self._reply(400)
        if self.path == '/qz-batch/finish':
            return self._reply(204 if bridge.finish(client, token) else 404)

        index = values.get('index')
        if type(index) is not int:
            return self._reply(400)
        data = bridge.read_file(client, token, index)
        if data is None:
            return self._reply(404)
        return self._reply(200, data, 'application/octet-stream')


class QuoteBridge:
    def __init__(self, port=PORT):
        self.jobs = {}
        self.condition = Condition()
        self.closed = False
        self.server = _LocalServer(('127.0.0.1', port), _Handler)
        self.server.bridge = self
        self.thread = Thread(target=self.server.serve_forever,
                             kwargs={'poll_interval': 0.15}, daemon=True)
        self.thread.start()

    def _prune(self):
        now = time()
        for token in [token for token, job in self.jobs.items() if job.expires <= now]:
            del self.jobs[token]

    def add(self, paths, auto_confirm):
        files = tuple((path, path.stat().st_size) for path in paths)
        if not 1 <= len(files) <= MAX_FILES:
            raise ValueError(f'每批支持 1 至 {MAX_FILES} 个 STEP。')
        invalid = [path.name for path, size in files if not 0 < size <= MAX_BYTES]
        if invalid:
            raise ValueError('以下 STEP 不符合活动页每个文件 7 MiB 的上限：' + '、'.join(invalid))
        if len({path.name.casefold() for path, _ in files}) != len(files):
            raise ValueError('同一批次的 STEP 文件名必须唯一。')
        job = _QuoteBatch(files, auto_confirm, time() + TTL_SECONDS)
        with self.condition:
            self._prune()
            self.jobs[secrets.token_urlsafe(24)] = job
            self.condition.notify_all()
        return job.received

    def next_batch(self, client):
        deadline = monotonic() + POLL_SECONDS
        with self.condition:
            while not self.closed:
                self._prune()
                owned = next(((key, job) for key, job in self.jobs.items() if job.owner == client), None)
                batch = owned or next(((key, job) for key, job in self.jobs.items() if job.owner is None), None)
                if batch:
                    key, job = batch
                    job.owner = client
                    job.received.set()
                    return {
                        'id': key, 'files': [{'name': path.name, 'size': size} for path, size in job.files],
                        'expires': int(job.expires * 1000), 'autoConfirm': job.auto_confirm,
                    }
                seconds_left = deadline - monotonic()
                if seconds_left <= 0:
                    return None
                self.condition.wait(seconds_left)
        return None

    def read_file(self, client, token, index):
        with self.condition:
            self._prune()
            job = self.jobs.get(token)
            if not job or job.owner != client or not 0 <= index < len(job.files):
                return None
            path, size = job.files[index]
        try:
            with path.open('rb') as step:
                data = step.read(size + 1)
        except OSError:
            return None
        if len(data) == size and data[:64].lstrip().startswith(b'ISO-10303-21;'):
            return data
        return None

    def finish(self, client, token):
        with self.condition:
            job = self.jobs.get(token)
            if not job or job.owner != client:
                return False
            del self.jobs[token]
            return True

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
