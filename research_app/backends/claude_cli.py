"""Claude Code stream JSON adapter."""
import json
import os
from pathlib import Path

from .base import Event, run_stream

# Claude Code confines its file tools to the working directory, but Bash commands are not
# covered by that. Its OS-level sandbox (macOS Seatbelt) is what stops `python3 -c ...`
# from writing elsewhere, e.g. when a fetched web page tries to steer the model.
# Verified with Claude Code 2.1.311: without this, a write to ~/Documents succeeded; with
# it, the write was blocked.
SANDBOX_SETTINGS = json.dumps({"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True,
                                           "allowUnsandboxedCommands": False}})


class ClaudeCLI:
    name = 'claude'

    def __init__(self, binary='claude', home=None):
        self.binary = binary
        self.home = Path(home).expanduser() if home is not None else Path(os.environ.get('CLAUDE_CONFIG_DIR', '~/.claude')).expanduser()

    def transcript_path(self, session_id, workdir):
        if not session_id or not self.home:
            return None
        return next((p for p in (self.home / 'projects').glob(f'*/{session_id}.jsonl') if p.is_file()), None)

    def run(self, req, on_event=None, cancel=None):
        # dontAsk: anything not on the allow-list is denied, never auto-approved. Without it,
        # the user's own default mode applies (e.g. "auto", where a classifier can approve
        # writes outside the workspace). File rules are scoped to the workspace (`./**`).
        command = [self.binary, '-p', '--output-format', 'stream-json', '--verbose',
                   '--permission-mode', 'dontAsk',
                   '--settings', SANDBOX_SETTINGS,
                   '--allowedTools', 'WebSearch', 'WebFetch', 'Read(./**)', 'Edit(./**)',
                   'Write(./**)', 'Bash(python3:*)']
        if req.session_id:
            command += ['--resume', req.session_id]
        if req.model:
            command += ['--model', req.model]

        def parse(obj, result, callback):
            kind = obj.get('type')
            if kind == 'system' and obj.get('subtype') == 'init':
                result.session_id = obj.get('session_id')
                result.model = obj.get('model')
            elif kind == 'assistant':
                for item in obj.get('message', {}).get('content', []):
                    if item.get('type') == 'text' and callback:
                        callback(Event('text', item.get('text', '')))
                    elif item.get('type') == 'tool_use' and callback:
                        callback(Event('tool', f"{item.get('name', '')}: {item.get('input', '')}"))
            elif kind == 'result':
                result.text = obj.get('result', '')
                result.session_id = obj.get('session_id') or result.session_id
                result.usage = obj.get('usage') or {}
                result.cost_usd = obj.get('total_cost_usd')
                if obj.get('is_error'):
                    result.error = result.text or obj.get('subtype', 'CLI error')
        return run_stream(command, req, parse, on_event, cancel)
