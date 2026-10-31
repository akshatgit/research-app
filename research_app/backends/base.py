"""Shared backend contract and subprocess runner."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol
import json
import os
import signal
import subprocess
import threading
import time


@dataclass
class RunRequest:
    prompt: str
    workdir: Path
    session_id: str | None = None
    model: str | None = None
    timeout: float = 900.0


@dataclass
class Event:
    kind: str
    text: str = ''


@dataclass
class RunResult:
    ok: bool
    text: str = ''
    session_id: str | None = None
    model: str | None = None
    usage: dict = field(default_factory=dict)
    cost_usd: float | None = None
    error_kind: str | None = None
    error: str = ''
    exit_code: int | None = None
    command: list[str] = field(default_factory=list)
    duration_s: float = 0.0


class Backend(Protocol):
    name: str
    def run(self, req: RunRequest, on_event: Callable[[Event], None] | None = None,
            cancel: threading.Event | None = None) -> RunResult: ...


def classify_error(text):
    t = text.lower()
    if any(s in t for s in ('429', 'rate_limit', 'rate limit', 'usage limit')):
        return 'rate_limit'
    if any(s in t for s in ('401', 'unauthorized', 'login', 'not logged in', 'invalid api key', 'authentication')):
        return 'auth'
    return 'failed'


def run_stream(command, req, parse, on_event=None, cancel=None):
    started = time.monotonic()
    result = RunResult(ok=False, command=command)
    try:
        proc = subprocess.Popen(command, cwd=req.workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
    except FileNotFoundError as exc:
        result.error_kind, result.error = 'not_installed', str(exc)
        return result
    # Drain both pipes while the parent polls for cancellation and timeout.
    lines = []
    stderr = []
    def read_stdout():
        for line in proc.stdout:
            lines.append(line)
            try:
                event = json.loads(line)
                parse(event, result, on_event)
            except (ValueError, TypeError, KeyError):
                pass
    reader = threading.Thread(target=read_stdout, daemon=True)
    err_reader = threading.Thread(target=lambda: stderr.append(proc.stderr.read()), daemon=True)
    reader.start(); err_reader.start()
    try:
        proc.stdin.write(req.prompt)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    reason = None
    while proc.poll() is None:
        if cancel and cancel.is_set():
            reason = 'cancelled'
            break
        if time.monotonic() - started > req.timeout:
            reason = 'timeout'
            break
        time.sleep(0.02)
    if reason:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=1)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    proc.wait()
    reader.join(timeout=2); err_reader.join(timeout=2)
    result.exit_code = proc.returncode
    result.duration_s = time.monotonic() - started
    if reason:
        result.ok = False
        result.error_kind = reason
        result.error = reason
    elif proc.returncode != 0 or result.error:
        result.ok = False
        result.error = result.error or ''.join(stderr).strip() or f'CLI exited with {proc.returncode}'
        result.error_kind = classify_error(result.error + ' ' + ''.join(stderr))
    else:
        result.ok = True
    return result
