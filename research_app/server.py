"""Loopback HTTP API and static frontend."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs
import json
import mimetypes
import shutil
from .backends import get_backends
from .docmodel import load_table, parse_viz_blocks, validate_viz
from .fsutil import safe_join
from .service import Service
from .store import Store
from .tracker import build_overview

WEB = Path(__file__).resolve().parent.parent / 'web'
MAX_BODY = 2 * 1024 * 1024


def create_server(data_root, backends=None, host='127.0.0.1', port=8765, allowed_hosts=None):
    # Loopback by default. Binding another interface is an explicit choice; requests must still
    # name this server (loopback or allowed_hosts) in Host/Origin, which blocks DNS rebinding.
    names = {'127.0.0.1', 'localhost', *(allowed_hosts or [])}
    service = Service(Store(data_root), backends or get_backends())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body=None, content_type='application/json', sandbox=False):
            if body is None:
                data = b''
            elif isinstance(body, bytes):
                data = body
            else:
                data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            # Revalidate every time, so a deploy is picked up without a hard refresh.
            self.send_header('Cache-Control', 'no-cache')
            if sandbox:
                self.send_header('Content-Security-Policy', 'sandbox')
            self.end_headers()
            if data:
                self.wfile.write(data)

        def _check(self):
            host_value = self.headers.get('Host', '')
            allowed = {f'{name}:{self.server.server_port}' for name in names}
            if host_value not in allowed:
                self._send(403, {'error': 'invalid Host'})
                return False
            if self.command in ('POST', 'DELETE'):
                origin = self.headers.get('Origin')
                if origin and origin not in {f'http://{h}' for h in allowed}:
                    self._send(403, {'error': 'invalid Origin'})
                    return False
            return True

        def _body(self):
            if not self.headers.get('Content-Type', '').lower().startswith('application/json'):
                self._send(415, {'error': 'JSON required'})
                return None
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = 0
            if length > MAX_BODY:
                self._send(413, {'error': 'body too large'})
                return None
            try:
                body = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(body, dict):
                    raise ValueError('JSON object required')
                return body
            except (ValueError, UnicodeDecodeError):
                self._send(400, {'error': 'invalid JSON'})
                return None

        def _route(self):
            if not self._check():
                return
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            parts = [p for p in path.split('/') if p]
            try:
                if self.command == 'GET' and path == '/':
                    return self._static('index.html')
                if self.command == 'GET' and parts and parts[0] == 'web':
                    return self._static('/'.join(parts[1:]))
                if parts == ['api', 'backends'] and self.command == 'GET':
                    return self._send(200, [{'name': n, 'available': bool(shutil.which(b.binary)) if hasattr(b, 'binary') else True} for n,b in service.backends.items()])
                if parts == ['api', 'topics']:
                    if self.command == 'GET':
                        return self._send(200, service.store.list_topics())
                    if self.command == 'POST':
                        body = self._body()
                        if body is not None:
                            return self._send(201, service.create_topic(body.get('title'), body.get('focus', ''), body.get('backend', 'claude'), body.get('research', True)))
                if len(parts) >= 3 and parts[:2] == ['api', 'runs']:
                    rid = parts[2]
                    if len(parts) == 3 and self.command == 'GET':
                        return self._send(200, service.get_run(rid))
                    if len(parts) == 4 and parts[3] == 'cancel' and self.command == 'POST':
                        if self._body() is not None:
                            return self._send(200, {'cancelled': service.cancel_run(rid)})
                if len(parts) >= 3 and parts[:2] == ['api', 'topics']:
                    tid = parts[2]
                    topic = service.store.get_topic(tid)
                    d = service.store.topic_dir(tid)
                    tail = parts[3:]
                    if not tail:
                        if self.command == 'GET':
                            return self._send(200, {'topic': topic, 'doc': service.store.read_doc(tid), 'doc_version': service.store.doc_version(tid)})
                        if self.command == 'DELETE':
                            service.store.delete_topic(tid)
                            return self._send(204)
                    if tail == ['version'] and self.command == 'GET':
                        return self._send(200, {'doc_version': service.store.doc_version(tid), 'active_runs': service.active_runs(tid)})
                    if tail == ['overview'] and self.command == 'GET':
                        return self._send(200, build_overview(service.store, tid))
                    if tail == ['instruct'] and self.command == 'POST':
                        body = self._body()
                        if body is not None:
                            return self._send(202, service.instruct(tid, body.get('instruction'), body.get('backend')))
                    if tail == ['runs'] and self.command == 'GET':
                        return self._send(200, service.store.list_runs(tid))
                    if tail == ['assets'] and self.command == 'GET':
                        return self._send(200, service.store.list_assets(tid))
                    if tail and tail[0] == 'files' and self.command == 'GET':
                        file = safe_join(d, '/'.join(tail[1:]))
                        if not file.is_file():
                            return self._send(404, {'error': 'file not found'})
                        mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
                        return self._send(200, file.read_bytes(), mime, sandbox=mime not in ('text/plain', 'text/csv', 'application/json'))
                    if tail == ['table'] and self.command == 'GET':
                        rel = parse_qs(parsed.query).get('path', [''])[0]
                        return self._send(200, load_table(safe_join(d, rel)))
                    if tail == ['viz'] and self.command == 'GET':
                        blocks = parse_viz_blocks(service.store.read_doc(tid))
                        return self._send(200, [{'index': b['index'], 'spec': b['spec'], 'errors':
                            [b['error']] if b['error'] else validate_viz(b['spec'], d)} for b in blocks])
                    if tail == ['threads']:
                        if self.command == 'GET':
                            return self._send(200, service.store.list_threads(tid))
                        if self.command == 'POST':
                            body = self._body()
                            if body is not None:
                                return self._send(201, service.ask(tid, body.get('quote', ''), body.get('prefix', ''), body.get('suffix', ''), body.get('start', 0), body.get('question', ''), body.get('backend'), body.get('section_id'), body.get('intent', 'explain')))
                    if len(tail) >= 2 and tail[0] == 'threads':
                        thread_id = tail[1]
                        if len(tail) == 2 and self.command == 'DELETE':
                            service.store.delete_thread(tid, thread_id)
                            return self._send(204)
                        if len(tail) == 3 and self.command == 'POST':
                            body = self._body()
                            if body is None:
                                return
                            if tail[2] == 'turns':
                                return self._send(202, service.follow_up(tid, thread_id, body.get('question', ''), body.get('backend'), body.get('intent', 'explain')))
                            if tail[2] == 'retry':
                                return self._send(202, service.retry(tid, thread_id))
                            if tail[2] == 'resolve':
                                return self._send(200, service.store.set_thread_resolved(tid, thread_id, body.get('resolved')))
                        if (len(tail) == 5 and tail[2] == 'turns' and tail[4] == 'undo'
                                and self.command == 'POST'):
                            if self._body() is not None:
                                try:
                                    number = int(tail[3])
                                except ValueError:
                                    raise KeyError(tail[3])
                                return self._send(200, service.undo_edit(tid, thread_id, number))
                self._send(404, {'error': 'not found'})
            except KeyError as exc:
                self._send(404, {'error': str(exc)})
            except RuntimeError as exc:
                self._send(409, {'error': str(exc)})
            except (ValueError, TypeError) as exc:
                self._send(400, {'error': str(exc)})

        def _static(self, rel):
            try:
                file = safe_join(WEB, rel)
                if not file.is_file():
                    return self._send(404, {'error': 'not found'})
                mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
                return self._send(200, file.read_bytes(), mime)
            except ValueError:
                self._send(404, {'error': 'not found'})

        do_GET = do_POST = do_DELETE = _route

    server = ThreadingHTTPServer((host, port), Handler)
    server.service = service
    return server
