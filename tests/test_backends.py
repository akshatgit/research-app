"""K1, K2, K3, K4, K5: the Claude and Codex CLI backends.

Each test runs the backend against tests/fakecli.py, which replays a fixture and records how
it was invoked (argv, cwd, stdin)."""
import json
import os
import threading
import time

import pytest

from conftest import FIXTURES

from research_app.backends import get_backends
from research_app.backends.base import Event, RunRequest, RunResult, classify_error
from research_app.backends.claude_cli import ClaudeCLI
from research_app.backends.codex_cli import CodexCLI

PROMPT = "Research the topic. Write doc.md."


def claude(fakecli):
    return ClaudeCLI(binary=fakecli.path("claude"))


def codex(fakecli):
    return CodexCLI(binary=fakecli.path("codex"))


def run(backend, workdir, **kw):
    events = []
    req = RunRequest(prompt=kw.pop("prompt", PROMPT), workdir=workdir, **kw)
    res = backend.run(req, on_event=events.append)
    return res, events


def arg_after(argv, *flags):
    for i, a in enumerate(argv):
        for f in flags:
            if a == f and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(f + "="):
                return a.split("=", 1)[1]
    return None


def prompt_delivered(rec, prompt):
    return prompt in rec["stdin"] or any(prompt in a for a in rec["argv"])


# ---------- registry and shared helpers ----------

def test_registry_has_both_backends():
    b = get_backends()
    assert set(b) == {"claude", "codex"}
    assert b["claude"].name == "claude" and b["codex"].name == "codex"


@pytest.mark.parametrize("text,kind", [
    ("API Error: 429 rate_limit_error: Rate limited", "rate_limit"),
    ("You've hit your usage limit. Try again later", "rate_limit"),
    ("Invalid API key · Please run /login", "auth"),
    ("unexpected status 401 Unauthorized: please run codex login", "auth"),
    ("Not logged in", "auth"),
    ("something else broke", "failed"),
])
def test_classify_error(text, kind):
    assert classify_error(text) == kind


# ---------- Claude ----------

class TestClaude:
    def test_success_parses_text_session_cost_usage(self, fakecli, workdir):
        fakecli.configure("claude_ok.jsonl")
        res, _ = run(claude(fakecli), workdir)
        assert isinstance(res, RunResult)
        assert res.ok and res.error_kind is None
        assert res.text == "OK"
        assert res.session_id == "f35db88c-81e3-4bc1-b493-3d0766801d1f"
        assert res.cost_usd == pytest.approx(0.1005094)
        assert res.usage["input_tokens"] >= 0 and "output_tokens" in res.usage
        assert res.model == "claude-opus-5-5"
        assert res.exit_code == 0
        assert res.command[0] == fakecli.path("claude")
        assert res.duration_s >= 0

    def test_command_is_noninteractive_stream_json(self, fakecli, workdir):
        fakecli.configure("claude_ok.jsonl")
        run(claude(fakecli), workdir)
        argv = fakecli.record()["argv"]
        assert "-p" in argv or "--print" in argv
        assert arg_after(argv, "--output-format") == "stream-json"
        assert "--verbose" in argv  # stream-json in print mode requires it

    def test_runs_in_workspace_and_delivers_prompt(self, fakecli, workdir):
        fakecli.configure("claude_ok.jsonl")
        run(claude(fakecli), workdir)
        rec = fakecli.record()
        assert os.path.realpath(rec["cwd"]) == os.path.realpath(workdir)
        assert prompt_delivered(rec, PROMPT)
        assert rec["stdin"] != "<OPEN-NO-DATA>"

    def test_large_prompt_goes_through_stdin(self, fakecli, workdir):
        # 1.5 MB exceeds macOS ARG_MAX, so it only works if the prompt is sent on stdin.
        big = "x" * 1_500_000
        fakecli.configure("claude_ok.jsonl")
        res, _ = run(claude(fakecli), workdir, prompt=big)
        assert res.ok
        assert big in fakecli.record()["stdin"]

    def test_resume_and_model_flags(self, fakecli, workdir):
        fakecli.configure("claude_ok.jsonl")
        run(claude(fakecli), workdir, session_id="abc-123", model="claude-sonnet-5-5")
        argv = fakecli.record()["argv"]
        assert arg_after(argv, "--resume", "-r") == "abc-123"
        assert arg_after(argv, "--model") == "claude-sonnet-5-5"

    def test_no_resume_or_model_flag_when_not_given(self, fakecli, workdir):
        fakecli.configure("claude_ok.jsonl")
        run(claude(fakecli), workdir)
        argv = fakecli.record()["argv"]
        assert "--resume" not in argv and "-r" not in argv and "--model" not in argv

    def test_permissions_are_scoped_not_bypassed(self, fakecli, workdir):
        """K4: web + file tools inside the workspace; never bypass permissions."""
        fakecli.configure("claude_ok.jsonl")
        run(claude(fakecli), workdir)
        argv = fakecli.record()["argv"]
        joined = " ".join(argv)
        assert "--dangerously-skip-permissions" not in argv
        assert "--allow-dangerously-skip-permissions" not in argv
        # dontAsk denies anything not allowed. Leaving it unset inherits the user's own mode,
        # e.g. "auto", which auto-approved writes outside the workspace in a live test.
        assert arg_after(argv, "--permission-mode") == "dontAsk"
        allowed = arg_after(argv, "--allowedTools", "--allowed-tools")
        assert allowed, "must pass an explicit tool allow-list"
        for tool in ("WebSearch", "WebFetch", "Read", "Write", "Edit"):
            assert tool in joined
        # file tools are scoped to the workspace; a bare rule allows any path
        for tool in ("Read", "Write", "Edit", "Glob", "Grep"):
            assert tool not in argv, f"unscoped {tool} rule"
        # no access granted to directories outside the workspace
        add_dir = arg_after(argv, "--add-dir")
        if add_dir:
            assert os.path.realpath(add_dir).startswith(os.path.realpath(workdir))

    def test_bash_runs_in_os_sandbox(self, fakecli, workdir):
        """K4: Claude Code's file-tool limits don't cover Bash. Its OS sandbox does, so it
        must be on whenever Bash is allowed, with no unsandboxed escape hatch."""
        fakecli.configure("claude_ok.jsonl")
        run(claude(fakecli), workdir)
        settings = json.loads(arg_after(fakecli.record()["argv"], "--settings"))
        assert settings["sandbox"]["enabled"] is True
        assert settings["sandbox"].get("allowUnsandboxedCommands") is False

    def test_streams_progress_events_for_tools_and_text(self, fakecli, workdir):
        fakecli.configure("claude_tools.jsonl")
        res, events = run(claude(fakecli), workdir)
        assert res.ok and res.text == "Done. Added a chart."
        assert all(isinstance(e, Event) for e in events)
        # progress lines are readable, not raw tool calls
        tools = [e.text for e in events if e.kind == "tool"]
        assert "Searching the web: formal verification SRE" in tools
        assert "Writing assets/data.csv" in tools
        assert any(e.kind == "text" and "Searching" in e.text for e in events)
        assert res.cost_usd == pytest.approx(0.42)

    def test_error_result_is_classified(self, fakecli, workdir):
        fakecli.configure("claude_error.jsonl", exit_code=1)
        res, _ = run(claude(fakecli), workdir)
        assert not res.ok
        assert res.error_kind == "rate_limit"
        assert "429" in res.error

    def test_auth_failure_from_stderr(self, fakecli, workdir):
        fakecli.configure(None, exit_code=1, stderr="Invalid API key · Please run /login\n")
        res, _ = run(claude(fakecli), workdir)
        assert not res.ok and res.error_kind == "auth"
        assert "login" in res.error.lower()

    def test_missing_binary(self, workdir, tmp_path):
        res, _ = run(ClaudeCLI(binary=str(tmp_path / "nope" / "claude")), workdir)
        assert not res.ok and res.error_kind == "not_installed"

    def test_nonzero_exit_without_output_is_failed(self, fakecli, workdir):
        fakecli.configure(None, exit_code=3, stderr="segfault-ish\n")
        res, _ = run(claude(fakecli), workdir)
        assert not res.ok and res.error_kind == "failed" and res.exit_code == 3

    def test_timeout_kills_process(self, fakecli, workdir):
        fakecli.configure("claude_ok.jsonl", sleep=30)
        t0 = time.time()
        res, _ = run(claude(fakecli), workdir, timeout=1.0)
        assert time.time() - t0 < 10
        assert not res.ok and res.error_kind == "timeout"
        pid = fakecli.record()["pid"]
        time.sleep(0.3)
        with pytest.raises(OSError):
            os.kill(pid, 0)

    def test_cancel(self, fakecli, workdir):
        fakecli.configure("claude_ok.jsonl", sleep=30)
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        t0 = time.time()
        res = claude(fakecli).run(RunRequest(prompt=PROMPT, workdir=workdir), cancel=cancel)
        assert time.time() - t0 < 10
        assert not res.ok and res.error_kind == "cancelled"

    def test_ignores_unknown_event_types_and_junk_lines(self, fakecli, workdir):
        # Real streams include events the app doesn't use (e.g. rate_limit_event); CLIs can
        # also print stray non-JSON lines. Neither may break parsing.
        noisy = fakecli.config_path.parent / "noisy.jsonl"
        ok = (FIXTURES / "claude_ok.jsonl").read_text()
        noisy.write_text('\n{"type": "rate_limit_event", "rate_limit_info": {}}\n'
                         "not json at all\n" + ok)
        fakecli.configure(None)
        cfg = json.loads(fakecli.config_path.read_text())
        cfg["fixture"] = str(noisy)
        fakecli.config_path.write_text(json.dumps(cfg))
        res, _ = run(claude(fakecli), workdir)
        assert res.ok and res.text == "OK"


# ---------- Codex ----------

class TestCodex:
    def test_success_parses_text_thread_usage(self, fakecli, workdir):
        fakecli.configure("codex_ok.jsonl")
        res, _ = run(codex(fakecli), workdir)
        assert res.ok and res.text == "OK"
        assert res.session_id == "01a193e8-27ef-7b32-8df0-7244a752cefb"
        assert res.usage["input_tokens"] == 14572 and res.usage["output_tokens"] == 5
        assert res.exit_code == 0

    def test_command_is_exec_json(self, fakecli, workdir):
        fakecli.configure("codex_ok.jsonl")
        run(codex(fakecli), workdir)
        argv = fakecli.record()["argv"]
        assert argv[1] == "exec"
        assert "--json" in argv

    def test_stdin_is_never_left_open(self, fakecli, workdir):
        """codex exec reads stdin when it is open; leaving it open hangs the run."""
        fakecli.configure("codex_ok.jsonl")
        run(codex(fakecli), workdir)
        rec = fakecli.record()
        assert rec["stdin"] != "<OPEN-NO-DATA>"
        assert prompt_delivered(rec, PROMPT)

    def test_large_prompt_goes_through_stdin(self, fakecli, workdir):
        big = "y" * 1_500_000
        fakecli.configure("codex_ok.jsonl")
        res, _ = run(codex(fakecli), workdir, prompt=big)
        assert res.ok
        rec = fakecli.record()
        assert big in rec["stdin"]
        assert rec["argv"][-1] == "-"  # `-` tells codex to read the prompt from stdin

    def test_workspace_sandbox_and_cwd(self, fakecli, workdir):
        fakecli.configure("codex_ok.jsonl")
        run(codex(fakecli), workdir)
        rec = fakecli.record()
        argv = rec["argv"]
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv
        sandbox = arg_after(argv, "-s", "--sandbox")
        configs = [argv[i + 1] for i, a in enumerate(argv) if a == "-c" and i + 1 < len(argv)]
        assert sandbox == "workspace-write" or \
            any(c.replace(" ", "") == 'sandbox_mode="workspace-write"' for c in configs)
        cd = arg_after(argv, "-C", "--cd")
        assert os.path.realpath(cd or rec["cwd"]) == os.path.realpath(workdir)

    def test_web_search_enabled(self, fakecli, workdir):
        fakecli.configure("codex_ok.jsonl")
        run(codex(fakecli), workdir)
        argv = fakecli.record()["argv"]
        assert any("web_search" in a for a in argv)

    def test_resume_form(self, fakecli, workdir):
        fakecli.configure("codex_ok.jsonl")
        run(codex(fakecli), workdir, session_id="0aaaaaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee")
        argv = fakecli.record()["argv"]
        i = argv.index("resume")
        assert argv[1] == "exec" and argv[i + 1] == "0aaaaaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
        assert "--json" in argv
        assert "--skip-git-repo-check" in argv  # resume refuses untrusted dirs without it
        # `exec resume` has no -s flag: the sandbox must still be workspace-write via config
        configs = [argv[j + 1] for j, a in enumerate(argv) if a == "-c" and j + 1 < len(argv)]
        assert any(c.replace(" ", "") == 'sandbox_mode="workspace-write"' for c in configs)

    def test_model_flag(self, fakecli, workdir):
        fakecli.configure("codex_ok.jsonl")
        run(codex(fakecli), workdir, model="gpt-5-codex")
        assert arg_after(fakecli.record()["argv"], "-m", "--model") == "gpt-5-codex"

    def test_streams_progress_events(self, fakecli, workdir):
        fakecli.configure("codex_tools.jsonl")
        res, events = run(codex(fakecli), workdir)
        assert res.ok and res.text == "Done. Added a chart."
        tools = [e.text for e in events if e.kind == "tool"]
        assert "Searching the web: formal verification SRE" in tools
        assert "Running: python3 make_chart.py" in tools
        assert "Editing doc.md" in tools

    def test_failed_turn_is_auth_error(self, fakecli, workdir):
        fakecli.configure("codex_failed.jsonl", exit_code=1)
        res, _ = run(codex(fakecli), workdir)
        assert not res.ok and res.error_kind == "auth"
        assert "401" in res.error

    def test_missing_binary(self, workdir, tmp_path):
        res, _ = run(CodexCLI(binary=str(tmp_path / "nope" / "codex")), workdir)
        assert not res.ok and res.error_kind == "not_installed"

    def test_timeout(self, fakecli, workdir):
        fakecli.configure("codex_ok.jsonl", sleep=30)
        res, _ = run(codex(fakecli), workdir, timeout=1.0)
        assert not res.ok and res.error_kind == "timeout"


@pytest.mark.parametrize("cls,name", [(ClaudeCLI, "claude"), (CodexCLI, "codex")])
def test_timeout_holds_when_cli_never_reads_a_big_prompt(fakecli, workdir, cls, name):
    """A 1 MB prompt fills the stdin pipe. If the CLI never reads it, writing must not block
    the run past its timeout (the prompt is written off the main path)."""
    fakecli.configure("claude_ok.jsonl", ignore_stdin=True, sleep=30)
    box = {}
    t = threading.Thread(target=lambda: box.update(res=cls(binary=fakecli.path(name)).run(
        RunRequest(prompt="z" * 1_000_000, workdir=workdir, timeout=1.0))), daemon=True)
    t0 = time.time()
    t.start()
    t.join(15)
    assert not t.is_alive(), "run() blocked past its timeout"
    assert box["res"].error_kind == "timeout" and time.time() - t0 < 15


@pytest.mark.parametrize("name,tool_input,label", [
    ("WebSearch", {"query": "SLO burn rate"}, "Searching the web: SLO burn rate"),
    ("WebFetch", {"url": "https://sre.google/workbook/"}, "Reading https://sre.google/workbook/"),
    ("Read", {"file_path": "/ws/source/lean/Core.lean"}, "Reading source/lean/Core.lean"),
    ("Write", {"file_path": "assets/a.csv", "content": "x"}, "Writing assets/a.csv"),
    ("Edit", {"file_path": "/ws/doc.md", "old_string": "a", "new_string": "b"}, "Editing doc.md"),
    ("Bash", {"command": "python3 plot.py --out assets/p.svg"}, "Running: python3 plot.py --out assets/p.svg"),
    ("Grep", {"pattern": "first_fire"}, "Searching files for first_fire"),
    ("Glob", {"pattern": "**/*.lean"}, "Finding files: **/*.lean"),
    ("TodoWrite", {"todos": []}, "Using TodoWrite"),
])
def test_describe_tool(name, tool_input, label):
    """Claude tool calls become readable progress lines; paths are shown relative to the
    workspace when they are inside it."""
    from research_app.backends.base import describe_tool
    assert describe_tool(name, tool_input, workdir="/ws") == label


def test_describe_tool_truncates_long_details():
    from research_app.backends.base import describe_tool
    label = describe_tool("Bash", {"command": "echo " + "x" * 500}, workdir="/ws")
    assert len(label) <= 120 and label.endswith("…")
