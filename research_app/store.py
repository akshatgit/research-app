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

    def create_topic(self, title, focus='', backend='claude', title_source='user'):
        if not isinstance(title, str) or not title.strip() or backend not in ('claude', 'codex'):
            raise ValueError('invalid topic')
        topic_id = uuid.uuid4().hex
        d = self.root / 'topics' / topic_id
        for sub in ('assets', 'threads', 'runs'):
            (d / sub).mkdir(parents=True)
        topic = {'id': topic_id, 'title': title.strip(), 'focus': focus, 'backend': backend,
                 'title_source': title_source, 'created': time.time()}
        self._write_json(d / 'topic.json', topic)
        atomic_write_text(d / 'doc.md', f'# {title.strip()}\n')
        subprocess.run(['git', 'init', '-q', str(d)], capture_output=True)
        return topic

    def get_topic(self, topic_id):
        return json.loads((self.topic_dir(topic_id) / 'topic.json').read_text())

    def update_topic_title(self, topic_id, title, source='doc'):
        with self.lock:
            topic = self.get_topic(topic_id)
            if topic.get('title_source', 'user') != 'user':
                topic.update(title=title, title_source=source)
                self._write_json(self.topic_dir(topic_id) / 'topic.json', topic)
            return topic

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
        base = self.topic_dir(topic_id) / 'threads'
        p = base / thread_id / 'thread.json'
        if not p.is_file():
            p = base / f'{thread_id}.json'
        if not p.is_file():
            raise KeyError(thread_id)
        return p

    def _save_thread(self, topic_id, th):
        base = self.topic_dir(topic_id) / 'threads'
        folder = base / th['id']
        turns = folder / 'turns'
        turns.mkdir(parents=True, exist_ok=True)
        for n, turn in enumerate(th['turns'], 1):
            label = 'user' if turn['role'] == 'user' else turn.get('backend', 'assistant')
            atomic_write_text(turns / f'{n:03d}-{label}.md', turn.get('text', ''))
        self._write_json(folder / 'thread.json', th)
        (base / f"{th['id']}.json").unlink(missing_ok=True)

    def save_turn_record(self, topic_id, thread_id, number, backend, prompt, raw):
        turns = self.topic_dir(topic_id) / 'threads' / thread_id / 'turns'
        turns.mkdir(parents=True, exist_ok=True)
        stem = f'{number:03d}-{backend}'
        atomic_write_text(turns / f'{stem}.prompt.md', prompt)
        atomic_write_text(turns / f'{stem}.events.jsonl', ''.join(line + '\n' for line in raw))

    def save_turn_doc_versions(self, topic_id, thread_id, number, backend, before, after):
        turns = self.topic_dir(topic_id) / 'threads' / thread_id / 'turns'
        turns.mkdir(parents=True, exist_ok=True)
        stem = f'{number:03d}-{backend}'
        atomic_write_text(turns / f'{stem}.doc-before.md', before)
        atomic_write_text(turns / f'{stem}.doc-after.md', after)

    def read_turn_doc_versions(self, topic_id, thread_id, number, backend):
        turns = self.topic_dir(topic_id) / 'threads' / thread_id / 'turns'
        stem = f'{number:03d}-{backend}'
        return ((turns / f'{stem}.doc-before.md').read_text(),
                (turns / f'{stem}.doc-after.md').read_text())

    def update_turn(self, topic_id, thread_id, number, **fields):
        with self.lock:
            th = self.get_thread(topic_id, thread_id)
            if not isinstance(number, int) or number < 1 or number > len(th['turns']):
                raise KeyError(number)
            th['turns'][number - 1].update(fields)
            self._save_thread(topic_id, th)
            return th

    def create_thread(self, topic_id, quote, prefix, suffix, start, question, backend, section_id=None, intent='explain'):
        d = self.topic_dir(topic_id)
        if not isinstance(quote, str) or not quote.strip() or backend not in ('claude', 'codex'):
            raise ValueError('invalid thread')
        tid = uuid.uuid4().hex
        th = {'id': tid, 'topic_id': topic_id, 'quote': quote, 'prefix': prefix, 'suffix': suffix,
              'start': start, 'section_id': section_id, 'created': time.time(), 'sessions': {}, 'resolved': False, 'resolved_at': None,
              'turns': [{'role': 'user', 'text': question or 'What does this mean?', 'backend': backend, 'intent': intent, 'at': time.time()}]}
        self._save_thread(topic_id, th)
        return th

    def get_thread(self, topic_id, thread_id):
        th = json.loads(self._thread_path(topic_id, thread_id).read_text())
        from .answers import parse_answer
        for turn in th.get('turns', []):
            if turn.get('role') == 'assistant' and 'tldr' not in turn:
                turn['tldr'] = parse_answer(turn.get('text', ''))['tldr']
        th.setdefault('sessions', {})
        th.setdefault('resolved', False)
        th.setdefault('resolved_at', None)
        return th

    def list_threads(self, topic_id):
        d = self.topic_dir(topic_id)
        ids = {p.stem for p in (d / 'threads').glob('*.json')}
        ids.update(p.name for p in (d / 'threads').iterdir() if (p / 'thread.json').is_file())
        return sorted((self.get_thread(topic_id, tid) for tid in ids), key=lambda t: t['created'])

    def set_thread_resolved(self, topic_id, thread_id, resolved):
        if not isinstance(resolved, bool):
            raise ValueError('resolved must be a boolean')
        with self.lock:
            th = self.get_thread(topic_id, thread_id)
            th['resolved'] = resolved
            th['resolved_at'] = time.time() if resolved else None
            self._save_thread(topic_id, th)
            return th

    def append_turn(self, topic_id, thread_id, turn):
        with self.lock:
            th = self.get_thread(topic_id, thread_id)
            th['turns'].append({'at': time.time(), **turn})
            self._save_thread(topic_id, th)
            return th

    def update_last_turn(self, topic_id, thread_id, **fields):
        with self.lock:
            th = self.get_thread(topic_id, thread_id)
            th['turns'][-1].update(fields)
            if fields.get('session_id') and th['turns'][-1].get('backend'):
                th['sessions'][th['turns'][-1]['backend']] = fields['session_id']
            self._save_thread(topic_id, th)
            return th

    def replace_last_turn(self, topic_id, thread_id, turn):
        with self.lock:
            th = self.get_thread(topic_id, thread_id)
            th['turns'][-1] = {'at': time.time(), **turn}
            self._save_thread(topic_id, th)

    def delete_thread(self, topic_id, thread_id):
        path = self._thread_path(topic_id, thread_id)
        if path.name == 'thread.json':
            shutil.rmtree(path.parent)
        else:
            path.unlink()

    def save_run(self, topic_id, run, prompt=None, raw=None, stderr=None):
        d = self.topic_dir(topic_id)
        folder = d / 'runs' / run['id']
        folder.mkdir(parents=True, exist_ok=True)
        self._write_json(folder / 'run.json', run)
        if prompt is not None:
            atomic_write_text(folder / 'prompt.md', prompt)
        if raw is not None:
            atomic_write_text(folder / 'events.jsonl', ''.join(line + '\n' for line in raw))
        if stderr is not None:
            atomic_write_text(folder / 'stderr.log', stderr)
        (d / 'runs' / f"{run['id']}.json").unlink(missing_ok=True)

    def list_runs(self, topic_id):
        d = self.topic_dir(topic_id)
        paths = list((d / 'runs').glob('*.json')) + list((d / 'runs').glob('*/run.json'))
        return sorted((json.loads(p.read_text()) for p in paths),
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
            if any(x.startswith('.') for x in rel.parts) or rel.parts[0] in ('threads', 'runs', 'sessions') or rel.name in ('topic.json', 'doc.md'):
                continue
            name = rel.as_posix()
            assets.append({'path': name, 'size': p.stat().st_size,
                           'type': mimetypes.guess_type(name)[0] or 'application/octet-stream',
                           'run_id': provenance.get(name)})
        return sorted(assets, key=lambda a: a['path'])
