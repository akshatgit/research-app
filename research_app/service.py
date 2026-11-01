"""Background run orchestration and thread sessions."""
from concurrent.futures import ThreadPoolExecutor, wait
import difflib
import re
import subprocess
import shutil
import threading
import time
import uuid
from .backends.base import RunRequest, normalize_usage
from .fsutil import atomic_write_text
from .fsutil import snapshot, diff_snapshots
from .prompts import research_prompt, answer_prompt, update_prompt, task_prompt, resumed_prompt

INTENTS = {'explain': 'answer', 'update': 'edit', 'task': 'task'}


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

    def ask(self, topic_id, quote, prefix, suffix, start, question, backend=None, section_id=None, intent='explain'):
        if intent not in INTENTS:
            raise ValueError('unknown intent')
        name = self._backend(topic_id, backend)
        th = self.store.create_thread(topic_id, quote, prefix, suffix, start, question, name, section_id=section_id, intent=intent)
        self._answer(topic_id, th['id'], th['turns'][0]['text'], name, intent=intent)
        return th

    def follow_up(self, topic_id, thread_id, question, backend=None, intent='explain'):
        if intent not in INTENTS:
            raise ValueError('unknown intent')
        with self.lock:
            th = self.store.get_thread(topic_id, thread_id)
            if th['turns'][-1].get('status') in ('queued', 'running'):
                raise RuntimeError('thread is answering')
            if not isinstance(question, str) or not question.strip():
                raise ValueError('empty question')
            name = backend or th['turns'][-1]['backend']
            if name not in self.backends:
                raise ValueError('unknown backend')
            self.store.append_turn(topic_id, thread_id, {'role': 'user', 'text': question, 'backend': name, 'intent': intent})
            self._answer(topic_id, thread_id, question, name, intent=intent)
            return self.store.get_thread(topic_id, thread_id)

    def retry(self, topic_id, thread_id):
        with self.lock:
            th = self.store.get_thread(topic_id, thread_id)
            last = th['turns'][-1]
            if last.get('status') not in ('error', 'cancelled'):
                raise RuntimeError('nothing to retry')
            name = last['backend']
            question = th['turns'][-2]['text']
            intent = last.get('intent', th['turns'][-2].get('intent', 'explain'))
            self.store.replace_last_turn(topic_id, thread_id, {'role': 'assistant', 'text': '', 'backend': name, 'status': 'queued', 'intent': intent})
            self._answer(topic_id, thread_id, question, name, replace=True, intent=intent)
            return self.store.get_thread(topic_id, thread_id)

    def _answer(self, topic_id, thread_id, question, name, replace=False, intent='explain'):
        th = self.store.get_thread(topic_id, thread_id)
        session = th.get('sessions', {}).get(name)
        history = '' if session else '\n'.join(f"{t['role']} ({t['backend']}): {t['text']}" for t in th['turns'][:-1])
        builders = {'explain': answer_prompt, 'update': update_prompt, 'task': task_prompt}
        prompt = resumed_prompt(intent, th, question) if session else builders[intent](th, question, self.store.read_doc(topic_id), history)
        if not replace:
            self.store.append_turn(topic_id, thread_id, {'role': 'assistant', 'text': '', 'backend': name, 'status': 'queued', 'intent': intent})
        self._enqueue(topic_id, INTENTS[intent], name, prompt, thread_id=thread_id, session_id=session,
                      question=question, intent=intent)

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
            self.store.save_run(topic_id, run, prompt=prompt, raw=[], stderr='')
            if thread_id := run.get('thread_id'):
                fields = {'run_id': run_id}
                if kind == 'task':
                    fields['task_run_id'] = run_id
                self.store.update_last_turn(topic_id, thread_id, **fields)
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
            if run.get('thread_id'):
                self.store.update_last_turn(topic_id, run['thread_id'], status='running')
        before = snapshot(d)
        old_doc = self.store.read_doc(topic_id)
        def event(ev):
            with self.lock:
                run['progress'].append({'kind': ev.kind, 'text': ev.text, 'at': time.time()})
                self.store.save_run(topic_id, run)
        try:
            req = RunRequest(prompt, d, session_id=run.get('session_id'))
            result = self.backends[run['backend']].run(req, event, cancel)
            transcript = None
            if result.session_id:
                finder = getattr(self.backends[run['backend']], 'transcript_path', None)
                source = finder(result.session_id, d) if finder else None
                if source and source.is_file():
                    target = d / 'sessions' / run['backend'] / f'{result.session_id}.jsonl'
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    transcript = target.relative_to(d).as_posix()
            if run['kind'] == 'answer' and self.store.read_doc(topic_id) != old_doc:
                self.store.write_doc(topic_id, old_doc)
            new_doc = self.store.read_doc(topic_id)
            doc_diff = ''.join(difflib.unified_diff(old_doc.splitlines(keepends=True),
                new_doc.splitlines(keepends=True), fromfile='doc.md before', tofile='doc.md after'))
            changed = diff_snapshots(before, snapshot(d))
            # Claude Code reports cost for the whole session, so a resumed run reports the
            # running total. Record that total, and charge this run only what it added.
            session_cost = result.cost_usd
            cost = session_cost
            if run.get('session_id') and session_cost is not None and run.get('thread_id'):
                earlier = [turn.get('session_cost_usd') for turn in
                           self.store.get_thread(topic_id, run['thread_id'])['turns']
                           if turn.get('session_id') == run['session_id']
                           and turn.get('session_cost_usd') is not None]
                if earlier and session_cost >= max(earlier):
                    cost = session_cost - max(earlier)
            with self.lock:
                run.update(status='done' if result.ok else ('cancelled' if result.error_kind == 'cancelled' else 'error'),
                           model=result.model, text=result.text, error_kind=result.error_kind, error=result.error,
                           command=result.command, exit_code=result.exit_code,
                           usage=normalize_usage(run['backend'], result.usage), transcript=transcript,
                           cost_usd=cost, session_cost_usd=session_cost, duration_s=result.duration_s,
                           finished=time.time(), files_changed=changed, doc_diff=doc_diff)
                self.store.save_run(topic_id, run, prompt=prompt, raw=result.raw, stderr=result.stderr)
                if run.get('thread_id'):
                    th = self.store.get_thread(topic_id, run['thread_id'])
                    passage_changed = bool(th['quote'].strip()) and re.sub(r'\s+', ' ', th['quote']).strip() not in re.sub(r'\s+', ' ', new_doc)
                    self.store.update_last_turn(topic_id, run['thread_id'], text=result.text,
                        status=run['status'], backend=run['backend'], model=result.model,
                        session_id=result.session_id, run_id=run_id, files_changed=changed,
                        error_kind=result.error_kind, error=result.error, usage=run['usage'],
                        cost_usd=cost, session_cost_usd=session_cost, intent=run['intent'],
                        doc_diff=doc_diff, passage_changed=passage_changed,
                        **({'undone': False} if run['kind'] == 'edit' else {}),
                        **({'task_run_id': run_id} if run['kind'] == 'task' else {}))
                    number = len(self.store.get_thread(topic_id, run['thread_id'])['turns'])
                    self.store.save_turn_record(topic_id, run['thread_id'], number, run['backend'], prompt, result.raw)
                    if run['kind'] == 'edit':
                        self.store.save_turn_doc_versions(topic_id, run['thread_id'], number, run['backend'], old_doc, new_doc)
            with self.git_locks[topic_id]:
                subprocess.run(['git', '-C', str(d), 'add', '-A'], capture_output=True)
                subprocess.run(['git', '-C', str(d), '-c', 'user.name=Research App',
                                '-c', 'user.email=local@research.invalid', 'commit', '--allow-empty',
                                '-qm', f'Run {run_id}'], capture_output=True)
        except Exception as exc:
            with self.lock:
                run.update(status='error', error_kind='failed', error=str(exc), finished=time.time())
                self.store.save_run(topic_id, run)

    def undo_edit(self, topic_id, thread_id, turn_number):
        with self.lock:
            th = self.store.get_thread(topic_id, thread_id)
            if not isinstance(turn_number, int) or turn_number < 1 or turn_number > len(th['turns']):
                raise KeyError(turn_number)
            turn = th['turns'][turn_number - 1]
            if turn.get('role') != 'assistant' or turn.get('intent') != 'update' or turn.get('undone'):
                raise ValueError('not an undoable update turn')
            before, after = self.store.read_turn_doc_versions(topic_id, thread_id, turn_number, turn['backend'])
            if self.store.read_doc(topic_id) != after:
                raise RuntimeError('doc.md changed since this edit')
            self.store.write_doc(topic_id, before)
            th = self.store.update_turn(topic_id, thread_id, turn_number, undone=True)
            d = self.store.topic_dir(topic_id)
            with self.git_locks.setdefault(topic_id, threading.Lock()):
                subprocess.run(['git', '-C', str(d), 'add', '-A'], capture_output=True)
                subprocess.run(['git', '-C', str(d), '-c', 'user.name=Research App',
                                '-c', 'user.email=local@research.invalid', 'commit', '--allow-empty',
                                '-qm', f'Undo edit {turn.get("run_id", thread_id)}'], capture_output=True)
            return th

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
