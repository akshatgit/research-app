"""Codex CLI JSON event adapter."""
from .base import Event, _short, run_stream
import os
from pathlib import Path


class CodexCLI:
    name = 'codex'

    def __init__(self, binary='codex', home=None):
        self.binary = binary
        self.home = Path(home).expanduser() if home is not None else Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser()

    def transcript_path(self, session_id, workdir):
        if not session_id or not self.home:
            return None
        return next((p for p in (self.home / 'sessions').rglob(f'rollout-*-{session_id}.jsonl') if p.is_file()), None)

    def run(self, req, on_event=None, cancel=None):
        if req.session_id:
            # `exec resume` takes neither -s nor -C, so the sandbox goes in as config and the
            # workspace is the process cwd. It still refuses untrusted dirs without the flag.
            command = [self.binary, 'exec', '--json', '-c', 'sandbox_mode="workspace-write"',
                       '-c', 'web_search="live"', '--skip-git-repo-check']
            if req.model:
                command += ['-m', req.model]
            command += ['resume', req.session_id, '-']
        else:
            command = [self.binary, 'exec', '--json', '-s', 'workspace-write', '-C', str(req.workdir),
                       '--skip-git-repo-check', '-c', 'web_search="live"']
            if req.model:
                command += ['-m', req.model]
            command.append('-')

        def parse(obj, result, callback):
            kind = obj.get('type')
            if kind == 'thread.started':
                result.session_id = obj.get('thread_id')
            elif kind in ('item.started', 'item.completed'):
                item = obj.get('item') or {}
                typ = item.get('type')
                if typ == 'agent_message' and kind == 'item.completed':
                    result.text = item.get('text', '')
                    if callback:
                        callback(Event('text', result.text))
                elif typ in ('web_search', 'command_execution', 'file_change') and callback:
                    # Readable progress lines, matching the Claude backend's wording.
                    if typ == 'web_search':
                        label = f"Searching the web: {item.get('query', '')}"
                    elif typ == 'command_execution':
                        label = f"Running: {item.get('command', '')}"
                    else:
                        label = 'Editing ' + ', '.join(str(c.get('path', '')) for c in item.get('changes', []))
                    if kind == 'item.started' or typ == 'file_change':
                        callback(Event('tool', _short(label)))
            elif kind == 'turn.completed':
                result.usage = obj.get('usage') or {}
            elif kind in ('error', 'turn.failed'):
                error = obj.get('error') or {}
                result.error = error.get('message', '') if isinstance(error, dict) else str(error)
        return run_stream(command, req, parse, on_event, cancel)
