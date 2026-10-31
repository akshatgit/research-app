"""Atomic, plain-file topic storage."""
from pathlib import Path
import json
import mimetypes
import os
import shutil
import subprocess
import threading
import time
import uuid
from .fsutil import atomic_write_text, safe_join


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        (self.root / 'topics').mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def topic_dir(self, topic_id):
        if not isinstance(topic_id, str) or not topic_id or not all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in topic_id):
            raise KeyError(topic_id)
        path = self.root / 'topics' / topic_id
        if not (path / 'topic.json').is_file():
            raise KeyError(topic_id)
        return path

    def _write_json(self, path, data):
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))

    def create_topic(self, title, focus='', backend='claude'):
        if not isinstance(title, str) or not title.strip() or backend not in ('claude', 'codex'):
            raise ValueError('invalid topic')
        topic_id = uuid.uuid4().hex
        d = self.root / 'topics' / topic_id
        for sub in ('assets', 'threads', 'runs'):
            (d / sub).mkdir(parents=True)
        topic = {'id': topic_id, 'title': title.strip(), 'focus': focus, 'backend': backend, 'created': time.time()}
        self._write_json(d / 'topic.json', topic)
        atomic_write_text(d / 'doc.md', f'# {title.strip()}\n')
        subprocess.run(['git', 'init', '-q', str(d)], capture_output=True)
        return topic

    def get_topic(self, topic_id):
        return json.loads((self.topic_dir(topic_id) / 'topic.json').read_text())

    def list_topics(self):
        topics = []
        for path in (self.root / 'topics').iterdir():
            try:
                t = self.get_topic(path.name)
                t['note_count'] = len(self.list_threads(t['id']))
                topics.append(t)
            except (KeyError, ValueError):
                continue
        return sorted(topics, key=lambda t: t['created'], reverse=True)

    def delete_topic(self, topic_id):
        shutil.rmtree(self.topic_dir(topic_id))

    def read_doc(self, topic_id):
        return (self.topic_dir(topic_id) / 'doc.md').read_text()

    def doc_version(self, topic_id):
        return (self.topic_dir(topic_id) / 'doc.md').stat().st_mtime_ns

    def write_doc(self, topic_id, text):
        p = self.topic_dir(topic_id) / 'doc.md'
        old = p.stat().st_mtime_ns
        atomic_write_text(p, text)
        if p.stat().st_mtime_ns <= old:
            os.utime(p, ns=(old + 1, old + 1))
        return self.doc_version(topic_id)

    def _thread_path(self, topic_id, thread_id):
        if not isinstance(thread_id, str) or not thread_id.isalnum():
            raise KeyError(thread_id)
        p = self.topic_dir(topic_id) / 'threads' / f'{thread_id}.json'
        if not p.is_file():
            raise KeyError(thread_id)
        return p

    def create_thread(self, topic_id, quote, prefix, suffix, start, question, backend, section_id=None):
        d = self.topic_dir(topic_id)
        if not isinstance(quote, str) or not quote.strip() or backend not in ('claude', 'codex'):
            raise ValueError('invalid thread')
        tid = uuid.uuid4().hex
        th = {'id': tid, 'topic_id': topic_id, 'quote': quote, 'prefix': prefix, 'suffix': suffix,
              'start': start, 'section_id': section_id, 'created': time.time(), 'sessions': {}, 'resolved': False, 'resolved_at': None,
              'turns': [{'role': 'user', 'text': question or 'What does this mean?', 'backend': backend, 'at': time.time()}]}
        self._write_json(d / 'threads' / f'{tid}.json', th)
        return th

    def get_thread(self, topic_id, thread_id):
        th = json.loads(self._thread_path(topic_id, thread_id).read_text())
        th.setdefault('resolved', False)
        th.setdefault('resolved_at', None)
        return th

    def list_threads(self, topic_id):
        d = self.topic_dir(topic_id)
        return sorted((self.get_thread(topic_id, p.stem) for p in (d / 'threads').glob('*.json')), key=lambda t: t['created'])

    def set_thread_resolved(self, topic_id, thread_id, resolved):
        if not isinstance(resolved, bool):
            raise ValueError('resolved must be a boolean')
        with self.lock:
            th = self.get_thread(topic_id, thread_id)
            th['resolved'] = resolved
            th['resolved_at'] = time.time() if resolved else None
            self._write_json(self._thread_path(topic_id, thread_id), th)
            return th

    def append_turn(self, topic_id, thread_id, turn):
        with self.lock:
            th = self.get_thread(topic_id, thread_id)
            th['turns'].append({'at': time.time(), **turn})
            self._write_json(self._thread_path(topic_id, thread_id), th)
            return th

    def update_last_turn(self, topic_id, thread_id, **fields):
        with self.lock:
            th = self.get_thread(topic_id, thread_id)
            th['turns'][-1].update(fields)
            if fields.get('session_id') and th['turns'][-1].get('backend'):
                th['sessions'][th['turns'][-1]['backend']] = fields['session_id']
            self._write_json(self._thread_path(topic_id, thread_id), th)
            return th

    def replace_last_turn(self, topic_id, thread_id, turn):
        with self.lock:
            th = self.get_thread(topic_id, thread_id)
            th['turns'][-1] = {'at': time.time(), **turn}
            self._write_json(self._thread_path(topic_id, thread_id), th)

    def delete_thread(self, topic_id, thread_id):
        self._thread_path(topic_id, thread_id).unlink()

    def save_run(self, topic_id, run):
        d = self.topic_dir(topic_id)
        self._write_json(d / 'runs' / f"{run['id']}.json", run)

    def list_runs(self, topic_id):
        d = self.topic_dir(topic_id)
        return sorted((json.loads(p.read_text()) for p in (d / 'runs').glob('*.json')),
                      key=lambda r: r.get('started', 0), reverse=True)

    def list_assets(self, topic_id):
        d = self.topic_dir(topic_id)
        provenance = {}
        for run in reversed(self.list_runs(topic_id)):
            for path in run.get('files_changed', {}).get('created', []):
                provenance[path] = run['id']
        assets = []
        for p in d.rglob('*'):
            if not p.is_file() or p.is_symlink():
                continue
            rel = p.relative_to(d)
            if any(x.startswith('.') for x in rel.parts) or rel.parts[0] in ('threads', 'runs') or rel.name in ('topic.json', 'doc.md'):
                continue
            name = rel.as_posix()
            assets.append({'path': name, 'size': p.stat().st_size,
                           'type': mimetypes.guess_type(name)[0] or 'application/octet-stream',
                           'run_id': provenance.get(name)})
        return sorted(assets, key=lambda a: a['path'])
