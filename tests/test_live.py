"""Opt-in checks against the REAL CLIs. They spend a little of your Claude/Codex quota.

    RESEARCH_APP_LIVE=1 pytest tests/test_live.py -v

They confirm the event shapes the parsers rely on still match the installed CLIs, and that
the sandbox really keeps writes inside the workspace."""
import os
from pathlib import Path
import shutil

import pytest

from research_app.backends.base import RunRequest
from research_app.backends.claude_cli import ClaudeCLI
from research_app.backends.codex_cli import CodexCLI

pytestmark = pytest.mark.skipif(os.environ.get("RESEARCH_APP_LIVE") != "1",
                                reason="set RESEARCH_APP_LIVE=1 to run against real CLIs")

BACKENDS = [pytest.param(ClaudeCLI, "claude", id="claude"),
            pytest.param(CodexCLI, "codex", id="codex")]


@pytest.mark.parametrize("cls,binary", BACKENDS)
def test_trivial_reply(cls, binary, workdir):
    if not shutil.which(binary):
        pytest.skip(f"{binary} not installed")
    res = cls().run(RunRequest(prompt="Reply with exactly: OK", workdir=workdir, timeout=180))
    assert res.ok, res.error
    assert res.text.strip() == "OK"
    assert res.session_id


@pytest.mark.parametrize("cls,binary", BACKENDS)
def test_resume_keeps_context(cls, binary, workdir):
    if not shutil.which(binary):
        pytest.skip(f"{binary} not installed")
    b = cls()
    first = b.run(RunRequest(prompt="Remember the word PLUM. Reply with exactly: OK",
                             workdir=workdir, timeout=180))
    assert first.ok, first.error
    second = b.run(RunRequest(prompt="Which word did I ask you to remember? One word.",
                              workdir=workdir, session_id=first.session_id, timeout=180))
    assert second.ok, second.error
    assert "PLUM" in second.text.upper()


@pytest.mark.parametrize("cls,binary", BACKENDS)
@pytest.mark.parametrize("how", ["file tool", "python3 via the shell"])
def test_writes_inside_workspace_only(cls, binary, how, workdir):
    if not shutil.which(binary):
        pytest.skip(f"{binary} not installed")
    # Not a temp dir: sandboxes allow temp dirs on purpose, which would hide a leak.
    outside = Path.home() / f".research-app-probe-{os.getpid()}-{binary}.txt"
    outside.unlink(missing_ok=True)
    try:
        res = cls().run(RunRequest(
            prompt=(f"Using {how}, create the file assets/inside.txt containing 'in'. Then, "
                    f"using {how} again, try to create {outside} containing 'out'. If that "
                    "is not permitted, just say so."),
            workdir=workdir, timeout=300))
        assert (workdir / "assets" / "inside.txt").exists(), res.text
        assert not outside.exists(), "backend wrote outside its workspace"
    finally:
        outside.unlink(missing_ok=True)
