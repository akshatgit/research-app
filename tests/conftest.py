"""Shared fixtures. Nothing here talks to a real model: CLIs are replaced by tests/fakecli.py
and orchestration tests use FakeBackend."""
import json
import os
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from research_app.backends.base import Event, RunRequest, RunResult

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures"


# ---------- fake CLI binaries ----------

class FakeCLI:
    """Controls tests/fakecli.py installed as `claude` and `codex` in a temp bin dir."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.config_path = tmp_path / "fakecli.json"
        self.record_path = tmp_path / "fakecli-record.json"
        src = (TESTS / "fakecli.py").read_text()
        for name in ("claude", "codex"):
            p = self.bin / name
            p.write_text(f"#!{sys.executable}\n{src}")
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        monkeypatch.setenv("FAKECLI_CONFIG", str(self.config_path))
        self.configure()

    def configure(self, fixture=None, **kw):
        cfg = {"record": str(self.record_path), **kw}
        if fixture:
            cfg["fixture"] = str(FIXTURES / fixture)
        self.config_path.write_text(json.dumps(cfg))

    def path(self, name):
        return str(self.bin / name)

    def record(self):
        return json.loads(self.record_path.read_text())


@pytest.fixture
def fakecli(tmp_path, monkeypatch):
    return FakeCLI(tmp_path, monkeypatch)


@pytest.fixture
def workdir(tmp_path):
    d = tmp_path / "workspace"
    d.mkdir()
    (d / "doc.md").write_text("# Topic\n")
    (d / "assets").mkdir()
    return d


# ---------- in-process fake backend ----------

class FakeBackend:
    """Implements the Backend interface without a subprocess.

    `script` is a function (req) -> dict with optional keys:
        write   {relative path: content} written into req.workdir
        text    final answer text (default "answer from <name>")
        events  list of Event to emit before finishing
        ok, error_kind, error, sleep, session_id
    """

    def __init__(self, name, model="fake-model"):
        self.name = name
        self.model = model
        self.requests: list[RunRequest] = []
        self.script = lambda req: {}
        self.calls = 0

    def run(self, req, on_event=None, cancel=None):
        self.requests.append(req)
        self.calls += 1
        s = self.script(req) or {}
        deadline = time.time() + s.get("sleep", 0)
        while time.time() < deadline:
            if cancel is not None and cancel.is_set():
                return RunResult(ok=False, error_kind="cancelled", error="cancelled",
                                 command=[self.name], session_id=req.session_id)
            time.sleep(0.02)
        for ev in s.get("events", []):
            if on_event:
                on_event(ev)
        for rel, content in (s.get("write") or {}).items():
            p = Path(req.workdir) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        if on_event:
            on_event(Event(kind="text", text=s.get("text", f"answer from {self.name}")))
        if s.get("ok", True) is False:
            return RunResult(ok=False, error_kind=s.get("error_kind", "failed"),
                             error=s.get("error", "boom"), command=[self.name], exit_code=1)
        return RunResult(ok=True, text=s.get("text", f"answer from {self.name}"),
                         session_id=s.get("session_id", f"{self.name}-session-{self.calls}"),
                         usage={"input_tokens": 10, "output_tokens": 5}, cost_usd=0.01,
                         command=[self.name, "--fake"], exit_code=0, duration_s=0.01,
                         model=self.model)


@pytest.fixture
def backends():
    return {"claude": FakeBackend("claude", "claude-opus-5-5"),
            "codex": FakeBackend("codex", "gpt-5-codex")}


@pytest.fixture
def store(tmp_path):
    from research_app.store import Store
    return Store(tmp_path / "data")


@pytest.fixture
def service(store, backends):
    from research_app.service import Service
    svc = Service(store, backends, max_concurrent=2)
    yield svc
    svc.shutdown()


# ---------- HTTP server ----------

class Client:
    def __init__(self, base, host_header=None):
        self.base = base
        self.host_header = host_header

    def request(self, method, path, body=None, headers=None, raw=False):
        data = None
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                payload = r.read()
                return r.status, (payload if raw else _maybe_json(payload)), dict(r.headers)
        except urllib.error.HTTPError as e:
            payload = e.read()
            return e.code, (payload if raw else _maybe_json(payload)), dict(e.headers)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body if body is not None else {}, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)


def _maybe_json(payload):
    try:
        return json.loads(payload) if payload else None
    except ValueError:
        return payload


@pytest.fixture
def server(tmp_path, backends):
    from research_app.server import create_server
    srv = create_server(tmp_path / "data", backends=backends, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    host, port = srv.server_address[:2]
    srv.client = Client(f"http://{host}:{port}")
    yield srv
    srv.shutdown()
    srv.server_close()
    if getattr(srv, "service", None) is not None:
        srv.service.shutdown()


def wait_for(pred, timeout=10.0, interval=0.05):
    """Poll until pred() is truthy; return its value or fail the test."""
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(interval)
    pytest.fail(f"condition not met within {timeout}s")
