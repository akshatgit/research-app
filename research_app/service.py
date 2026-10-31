"""Background run orchestration and thread sessions."""
from concurrent.futures import ThreadPoolExecutor, wait
import subprocess
import threading
import time
import uuid
from .backends.base import RunRequest
from .fsutil import snapshot, diff_snapshots
from .prompts import research_prompt, answer_prompt


class Service:
    def __init__(self, store, backends, max_concurrent=2):
        self.store, self.backends = store, backends
        self.pool = ThreadPoolExecutor(max_workers=max_concurrent)
        self.lock = threading.RLock()
        self.runs = {}
        self.cancels = {}
        self.futures = []
        self.git_locks = {}

    def _backend(self, topic_id, backend):
        name = backend or self.store.get_topic(topic_id)['backend']
        if name not in self.backends:
            raise ValueError('unknown backend')
        return name

    def create_topic(self, title, focus='', backend='claude', research=True):
        if backend not in self.backends:
            raise ValueError('unknown backend')
        topic = self.store.create_topic(title, focus, backend)
        if research:
            self.instruct(topic['id'], 'Create a researched brief with at least five cited sources.')
        return topic

    def instruct(self, topic_id, instruction, backend=None):
        self.store.get_topic(topic_id)
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError('empty instruction')
        name = self._backend(topic_id, backend)
        topic = self.store.get_topic(topic_id)
        prompt = research_prompt(topic, instruction, self.store.read_doc(topic_id))
        return self._enqueue(topic_id, 'research', name, prompt, instruction=instruction)

    def ask(self, topic_id, quote, prefix, suffix, start, question, backend=None):
        name = self._backend(topic_id, backend)
        th = self.store.create_thread(topic_id, quote, prefix, suffix, start, question, name)
        self._answer(topic_id, th['id'], th['turns'][0]['text'], name)
        return th

    def follow_up(self, topic_id, thread_id, question, backend=None):
        with self.lock:
            th = self.store.get_thread(topic_id, thread_id)
            if th['turns'][-1].get('status') in ('queued', 'running'):
                raise RuntimeError('thread is answering')
            if not isinstance(question, str) or not question.strip():
                raise ValueError('empty question')
            name = backend or th['turns'][-1]['backend']
            if name not in self.backends:
                raise ValueError('unknown backend')
            self.store.append_turn(topic_id, thread_id, {'role': 'user', 'text': question, 'backend': name})
            self._answer(topic_id, thread_id, question, name)
            return self.store.get_thread(topic_id, thread_id)

    def retry(self, topic_id, thread_id):
        with self.lock:
            th = self.store.get_thread(topic_id, thread_id)
            last = th['turns'][-1]
            if last.get('status') not in ('error', 'cancelled'):
                raise RuntimeError('nothing to retry')
            name = last['backend']
            question = th['turns'][-2]['text']
            self.store.replace_last_turn(topic_id, thread_id, {'role': 'assistant', 'text': '', 'backend': name, 'status': 'queued'})
            self._answer(topic_id, thread_id, question, name, replace=True)
            return self.store.get_thread(topic_id, thread_id)

    def _answer(self, topic_id, thread_id, question, name, replace=False):
        th = self.store.get_thread(topic_id, thread_id)
        session = th.get('sessions', {}).get(name)
        history = '' if session else '\n'.join(f"{t['role']} ({t['backend']}): {t['text']}" for t in th['turns'][:-1])
        prompt = question if session else answer_prompt(th, question, self.store.read_doc(topic_id), history)
        if not replace:
            self.store.append_turn(topic_id, thread_id, {'role': 'assistant', 'text': '', 'backend': name, 'status': 'queued'})
        self._enqueue(topic_id, 'answer', name, prompt, thread_id=thread_id, session_id=session)

    def _enqueue(self, topic_id, kind, backend, prompt, **extra):
        run_id = uuid.uuid4().hex
        with self.lock:
            self.git_locks.setdefault(topic_id, threading.Lock())
        run = {'id': run_id, 'topic_id': topic_id, 'kind': kind, 'backend': backend,
               'status': 'queued', 'started': time.time(), 'progress': [], 'files_changed':
               {'created': [], 'modified': [], 'deleted': []}, **extra}
        with self.lock:
            self.runs[run_id] = run
            self.cancels[run_id] = threading.Event()
            self.store.save_run(topic_id, run)
            future = self.pool.submit(self._execute, run_id, prompt)
            self.futures.append(future)
            return dict(run)

    def _execute(self, run_id, prompt):
        run = self.runs[run_id]
        topic_id = run['topic_id']
        d = self.store.topic_dir(topic_id)
        cancel = self.cancels[run_id]
        with self.lock:
            if cancel.is_set():
                run['status'] = 'cancelled'
                self.store.save_run(topic_id, run)
                return
            run['status'] = 'running'
            self.store.save_run(topic_id, run)
        before = snapshot(d)
        old_doc = self.store.read_doc(topic_id) if run['kind'] == 'answer' else None
        def event(ev):
            with self.lock:
                run['progress'].append({'kind': ev.kind, 'text': ev.text, 'at': time.time()})
                self.store.save_run(topic_id, run)
        try:
            req = RunRequest(prompt, d, session_id=run.get('session_id'))
            result = self.backends[run['backend']].run(req, event, cancel)
            if old_doc is not None and self.store.read_doc(topic_id) != old_doc:
                self.store.write_doc(topic_id, old_doc)
            changed = diff_snapshots(before, snapshot(d))
            with self.lock:
                run.update(status='done' if result.ok else ('cancelled' if result.error_kind == 'cancelled' else 'error'),
                           model=result.model, text=result.text, error_kind=result.error_kind, error=result.error,
                           command=result.command, exit_code=result.exit_code, usage=result.usage,
                           cost_usd=result.cost_usd, duration_s=result.duration_s,
                           finished=time.time(), files_changed=changed)
                self.store.save_run(topic_id, run)
                if run['kind'] == 'answer':
                    self.store.update_last_turn(topic_id, run['thread_id'], text=result.text,
                        status=run['status'], backend=run['backend'], model=result.model,
                        session_id=result.session_id, run_id=run_id, files_changed=changed,
                        error_kind=result.error_kind, error=result.error)
            with self.git_locks[topic_id]:
                subprocess.run(['git', '-C', str(d), 'add', '-A'], capture_output=True)
                subprocess.run(['git', '-C', str(d), '-c', 'user.name=Research App',
                                '-c', 'user.email=local@research.invalid', 'commit', '--allow-empty',
                                '-qm', f'Run {run_id}'], capture_output=True)
        except Exception as exc:
            with self.lock:
                run.update(status='error', error_kind='failed', error=str(exc), finished=time.time())
                self.store.save_run(topic_id, run)

    def get_run(self, run_id):
        with self.lock:
            if run_id not in self.runs:
                raise KeyError(run_id)
            return dict(self.runs[run_id])

    def cancel_run(self, run_id):
        with self.lock:
            if run_id not in self.runs:
                raise KeyError(run_id)
            if self.runs[run_id]['status'] not in ('queued', 'running'):
                return False
            self.cancels[run_id].set()
            return True

    def active_runs(self, topic_id):
        with self.lock:
            return sum(r['topic_id'] == topic_id and r['status'] in ('queued', 'running') for r in self.runs.values())

    def wait_idle(self, timeout):
        with self.lock:
            futures = list(self.futures)
        _, pending = wait(futures, timeout=timeout)
        return not pending

    def shutdown(self):
        for c in self.cancels.values():
            c.set()
        self.pool.shutdown(wait=True)
