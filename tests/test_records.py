"""R1-R7: every record of a topic lives in its folder.

    threads/<tid>/thread.json
    threads/<tid>/turns/001-user.md
    threads/<tid>/turns/002-claude.md            the answer
    threads/<tid>/turns/002-claude.prompt.md     the exact prompt sent
    threads/<tid>/turns/002-claude.events.jsonl  raw CLI stdout, verbatim
    runs/<rid>/run.json  prompt.md  events.jsonl  stderr.log
    sessions/<backend>/<session id>.jsonl        copy of the CLI's own transcript
"""
import json
import os

import pytest

from conftest import FIXTURES
from research_app.backends.base import RunRequest, normalize_usage
from research_app.backends.claude_cli import ClaudeCLI
from research_app.backends.codex_cli import CodexCLI
from research_app.fsutil import diff_snapshots, snapshot
from research_app.prompts import answer_prompt, research_prompt

ANCHOR = dict(quote="burn rate cancels out", prefix="Budget spent = ",
              suffix=". So the 1h page", start=0)
DOC = "# SLO alerts\n\nBudget spent = burn rate cancels out. So the 1h page fires at 2%.\n"


def topic_with_doc(service):
    t = service.create_topic("SLO alerts", research=False)
    service.store.write_doc(t["id"], DOC)
    return t, service.store.topic_dir(t["id"])


# ---------- R1: thread folders and turn files ----------

class TestThreadRecords:
    def test_thread_folder_and_turn_files(self, service, backends):
        t, d = topic_with_doc(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        service.follow_up(t["id"], th["id"], "And the 6h page?", backend="codex")
        assert service.wait_idle(10)

        tdir = d / "threads" / th["id"]
        assert (tdir / "thread.json").is_file()
        assert not (d / "threads" / f"{th['id']}.json").exists()
        turns = tdir / "turns"
        names = sorted(p.name for p in turns.iterdir())
        assert names == ["001-user.md", "002-claude.events.jsonl", "002-claude.md",
                         "002-claude.prompt.md", "003-user.md", "004-codex.events.jsonl",
                         "004-codex.md", "004-codex.prompt.md"]
        assert (turns / "001-user.md").read_text().strip() == "Why?"
        assert (turns / "002-claude.md").read_text().strip() == "answer from claude"
        assert (turns / "002-claude.prompt.md").read_text() == backends["claude"].requests[0].prompt
        events = (turns / "002-claude.events.jsonl").read_text().splitlines()
        assert json.loads(events[0])["type"] == "fake.start"
        assert (turns / "003-user.md").read_text().strip() == "And the 6h page?"

    def test_thread_api_shape_unchanged(self, service):
        """The UI keeps reading turns from the thread; files are an additional record."""
        t, _ = topic_with_doc(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        got = service.store.get_thread(t["id"], th["id"])
        assert [x["role"] for x in got["turns"]] == ["user", "assistant"]
        assert got["turns"][-1]["text"] == "answer from claude"
        assert [x["id"] for x in service.store.list_threads(t["id"])] == [th["id"]]

    def test_failed_answer_keeps_its_events(self, service, backends):
        backends["claude"].script = lambda req: {"ok": False, "error_kind": "rate_limit"}
        t, d = topic_with_doc(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        turns = d / "threads" / th["id"] / "turns"
        assert (turns / "002-claude.events.jsonl").is_file()
        assert (turns / "002-claude.prompt.md").is_file()


# ---------- R2: run folders ----------

class TestRunRecords:
    def test_run_folder(self, service, backends):
        backends["claude"].script = lambda req: {"write": {"assets/a.csv": "x\n1\n"}}
        t, d = topic_with_doc(service)
        run = service.instruct(t["id"], "Add data")
        assert service.wait_idle(10)
        rdir = d / "runs" / run["id"]
        assert json.loads((rdir / "run.json").read_text())["id"] == run["id"]
        assert (rdir / "prompt.md").read_text() == backends["claude"].requests[-1].prompt
        assert (rdir / "events.jsonl").read_text().splitlines()[0].startswith('{"type": "fake.start"')
        assert (rdir / "stderr.log").is_file()
        assert not (d / "runs" / f"{run['id']}.json").exists()
        assert [r["id"] for r in service.store.list_runs(t["id"])] == [run["id"]]

    def test_failed_run_keeps_stderr(self, service, backends):
        backends["claude"].script = lambda req: {"ok": False, "error_kind": "failed"}
        t, d = topic_with_doc(service)
        run = service.instruct(t["id"], "go")
        assert service.wait_idle(10)
        assert (d / "runs" / run["id"] / "stderr.log").read_text() == "fake failure\n"


# ---------- R3: session transcripts ----------

class TestTranscripts:
    def test_copied_after_each_run(self, service, backends):
        t, d = topic_with_doc(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        sid = service.store.get_thread(t["id"], th["id"])["turns"][-1]["session_id"]
        copy = d / "sessions" / "claude" / f"{sid}.jsonl"
        assert copy.is_file() and "answer from claude" in copy.read_text()
        # a follow-up resumes the same session; the copy is refreshed, not duplicated
        service.follow_up(t["id"], th["id"], "More?")
        assert service.wait_idle(10)
        assert len(copy.read_text().splitlines()) == 2
        assert sorted(p.name for p in (d / "sessions" / "claude").iterdir()) == [f"{sid}.jsonl"]

    def test_missing_transcript_is_noted_not_an_error(self, service, backends):
        backends["claude"].home = None
        t, d = topic_with_doc(service)
        run = service.instruct(t["id"], "go")
        assert service.wait_idle(10)
        r = service.get_run(run["id"])
        assert r["status"] == "done" and r["transcript"] is None

    def test_claude_transcript_location(self, tmp_path, workdir):
        home = tmp_path / "claude-home"
        proj = home / "projects" / "-some-escaped-path"
        proj.mkdir(parents=True)
        (proj / "abc-123.jsonl").write_text("{}\n")
        b = ClaudeCLI(binary="claude", home=home)
        assert b.transcript_path("abc-123", workdir) == proj / "abc-123.jsonl"
        assert b.transcript_path("nope", workdir) is None

    def test_codex_transcript_location(self, tmp_path, workdir):
        home = tmp_path / "codex-home"
        day = home / "sessions" / "2026" / "10" / "31"
        day.mkdir(parents=True)
        f = day / "rollout-2026-10-31T14-40-28-01a193e8-27ef-7b32-8df0-7244a752cefb.jsonl"
        f.write_text("{}\n")
        b = CodexCLI(binary="codex", home=home)
        assert b.transcript_path("01a193e8-27ef-7b32-8df0-7244a752cefb", workdir) == f
        assert b.transcript_path("01a193e8-0000-0000-0000-000000000000", workdir) is None

    def test_homes_follow_cli_env_vars(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "c"))
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "x"))
        assert ClaudeCLI().home == tmp_path / "c"
        assert CodexCLI().home == tmp_path / "x"


# ---------- raw stream capture in the CLI backends ----------

class TestRawCapture:
    @pytest.mark.parametrize("cls,name,fixture", [(ClaudeCLI, "claude", "claude_tools.jsonl"),
                                                  (CodexCLI, "codex", "codex_tools.jsonl")])
    def test_raw_stdout_and_stderr(self, fakecli, workdir, cls, name, fixture):
        fakecli.configure(fixture, stderr="warning: something\n")
        res = cls(binary=fakecli.path(name)).run(RunRequest(prompt="p", workdir=workdir))
        assert res.ok
        assert res.raw == (FIXTURES / fixture).read_text().splitlines()
        assert res.stderr == "warning: something\n"


# ---------- R4: v0.4 layout still loads and is migrated ----------

class TestMigration:
    def test_legacy_thread_and_run_files(self, store):
        t = store.create_topic("Old")
        d = store.topic_dir(t["id"])
        legacy = {"id": "th1", "topic_id": t["id"], "quote": "q", "prefix": "", "suffix": "",
                  "start": 0, "created": 1.0, "resolved": False,
                  "turns": [{"role": "user", "text": "Why?", "backend": "claude", "at": 1.0},
                            {"role": "assistant", "text": "Because.", "status": "done",
                             "backend": "claude"}]}
        (d / "threads" / "th1.json").write_text(json.dumps(legacy))
        (d / "runs" / "r1.json").write_text(json.dumps({"id": "r1", "topic_id": t["id"],
                                                         "started": 1.0, "status": "done",
                                                         "files_changed": {}}))
        assert [x["id"] for x in store.list_threads(t["id"])] == ["th1"]
        assert store.get_thread(t["id"], "th1")["turns"][1]["text"] == "Because."
        assert [r["id"] for r in store.list_runs(t["id"])] == ["r1"]
        store.append_turn(t["id"], "th1", {"role": "user", "text": "More?", "backend": "claude"})
        assert not (d / "threads" / "th1.json").exists()
        assert len(json.loads((d / "threads" / "th1" / "thread.json").read_text())["turns"]) == 3


# ---------- R5: prompt order for caching ----------

def common_prefix(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n


class TestPromptOrder:
    TOPIC = {"title": "SLO alerts", "focus": "talk"}
    BIG = DOC + "\n".join(f"Paragraph {i} about burn rates." for i in range(200))

    def test_research_prompt_doc_before_instruction(self):
        a = research_prompt(self.TOPIC, "Add a chart of talks", self.BIG)
        b = research_prompt(self.TOPIC, "Check claim 3", self.BIG)
        assert a.index(self.BIG) < a.index("Add a chart of talks")
        assert common_prefix(a, b) >= a.index(self.BIG) + len(self.BIG)

    def test_answer_prompt_doc_before_passage_and_question(self):
        th1 = {**ANCHOR}
        th2 = {"quote": "fires at 2%", "prefix": "1h page ", "suffix": ".", "start": 60}
        a = answer_prompt(th1, "Why?", self.BIG, "")
        b = answer_prompt(th2, "Where does 2% come from?", self.BIG, "")
        assert a.index(self.BIG) < a.index("Why?")
        assert common_prefix(a, b) >= a.index(self.BIG) + len(self.BIG)


# ---------- R6: normalised usage ----------

class TestUsage:
    def test_normalize_claude(self):
        assert normalize_usage("claude", {"input_tokens": 2, "output_tokens": 4,
                                          "cache_read_input_tokens": 11267,
                                          "cache_creation_input_tokens": 12271}) == \
            {"input": 2, "cached": 11267, "cache_write": 12271, "output": 4}

    def test_normalize_codex(self):
        assert normalize_usage("codex", {"input_tokens": 14572, "cached_input_tokens": 11776,
                                         "cache_write_input_tokens": 0, "output_tokens": 5,
                                         "reasoning_output_tokens": 0}) == \
            {"input": 2796, "cached": 11776, "cache_write": 0, "output": 5}

    def test_normalize_missing_fields(self):
        assert normalize_usage("claude", {}) == {"input": 0, "cached": 0, "cache_write": 0,
                                                 "output": 0}

    def test_answer_turn_and_run_carry_usage(self, service):
        t, _ = topic_with_doc(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        a = service.store.get_thread(t["id"], th["id"])["turns"][-1]
        assert a["usage"] == {"input": 10, "cached": 0, "cache_write": 0, "output": 5}
        assert a["cost_usd"] == pytest.approx(0.01)
        assert service.store.list_runs(t["id"])[0]["usage"] == a["usage"]


# ---------- R7: bookkeeping is not an asset ----------

def test_records_are_not_assets_or_changes(service, backends):
    backends["claude"].script = lambda req: {"write": {"assets/a.csv": "x\n1\n"}}
    t, d = topic_with_doc(service)
    before = snapshot(d)
    service.instruct(t["id"], "go")
    service.ask(t["id"], question="Why?", **ANCHOR)
    assert service.wait_idle(10)
    changed = diff_snapshots(before, snapshot(d))
    assert all(not p.startswith(("threads/", "runs/", "sessions/")) for k in changed
               for p in changed[k])
    paths = [a["path"] for a in service.store.list_assets(t["id"])]
    assert paths == ["assets/a.csv"]


def test_records_not_served_by_files_api(server, backends):
    c = server.client
    _, t, _ = c.post("/api/topics", {"title": "T", "research": False})
    _, th, _ = c.post(f"/api/topics/{t['id']}/threads",
                      {"quote": "x", "prefix": "", "suffix": "", "start": 0})
    assert server.service.wait_idle(10)
    for path in (f"threads/{th['id']}/thread.json", f"threads/{th['id']}/turns/001-user.md",
                 "sessions/claude/x.jsonl"):
        assert c.get(f"/api/topics/{t['id']}/files/{path}", raw=True)[0] in (400, 403, 404)


def test_resumed_session_cost_is_per_turn(service, backends):
    """Claude Code reports total_cost_usd for the whole session, so a resumed follow-up
    reports the running total. Seen live: $0.166 for the first answer, then $0.206 for a
    follow-up that cost $0.040. Each turn must show what it cost."""
    totals = iter([0.166, 0.206])
    backends["claude"].script = lambda req: {"cost": next(totals)}
    t, _ = topic_with_doc(service)
    th = service.ask(t["id"], question="Why?", **ANCHOR)
    assert service.wait_idle(10)
    service.follow_up(t["id"], th["id"], "And the 6h page?")
    assert service.wait_idle(10)
    a1, a2 = [x for x in service.store.get_thread(t["id"], th["id"])["turns"]
              if x["role"] == "assistant"]
    assert a1["cost_usd"] == pytest.approx(0.166)
    assert a2["cost_usd"] == pytest.approx(0.040)
    assert a2["session_cost_usd"] == pytest.approx(0.206)
    assert service.store.list_runs(t["id"])[0]["cost_usd"] == pytest.approx(0.040)
