"""T1-T3, T6, W2, W4, W5, D2, H3, H5, H8, H10, K3, K6, K7: orchestration of backend runs.

Uses FakeBackend (conftest), so these tests check what the app asks for and records, not
what a model says."""
import subprocess
import threading
import time

import pytest

from conftest import wait_for
from research_app.backends.base import Event

BRIEF = """# Formal verification for SRE

Summary [1].

## Sources

1. [AWS](https://example.com/aws)
"""


def research_writes_brief(req):
    return {"write": {"doc.md": BRIEF, "assets/talks.csv": "year,talks\n2024,3\n"},
            "text": "Wrote the brief."}


def last_run(service, topic_id):
    return service.store.list_runs(topic_id)[0]


# ---------- research runs ----------

class TestResearch:
    def test_create_topic_starts_research_on_default_backend(self, service, backends):
        backends["claude"].script = research_writes_brief
        t = service.create_topic("Formal verification for SRE", focus="for SREs")
        assert service.wait_idle(10)
        assert backends["claude"].calls == 1 and backends["codex"].calls == 0
        assert service.store.read_doc(t["id"]) == BRIEF
        run = last_run(service, t["id"])
        assert run["kind"] == "research" and run["status"] == "done"
        assert run["backend"] == "claude" and run["model"] == "claude-opus-5-5"

    def test_create_topic_without_research(self, service, backends):
        service.create_topic("X", research=False)
        assert service.wait_idle(5)
        assert backends["claude"].calls == 0

    def test_research_prompt_contents(self, service, backends):
        t = service.create_topic("Formal verification for SRE", focus="audience: SREs",
                                 research=False)
        service.store.write_doc(t["id"], "# Existing doc\n\nKeep me.")
        service.instruct(t["id"], "Add a chart of SREcon talks per year")
        assert service.wait_idle(10)
        req = backends["claude"].requests[-1]
        p = req.prompt
        for needle in ("Formal verification for SRE", "audience: SREs",
                       "Add a chart of SREcon talks per year", "Keep me.",
                       "doc.md", "assets/", "```viz", "## Sources", "[1]"):
            assert needle in p, needle
        assert str(req.workdir) == str(service.store.topic_dir(t["id"]))

    def test_instruct_can_override_backend(self, service, backends):
        t = service.create_topic("X", research=False)
        run = service.instruct(t["id"], "go", backend="codex")
        assert run["backend"] == "codex" and run["status"] in ("queued", "running", "done")
        assert service.wait_idle(10)
        assert backends["codex"].calls == 1 and backends["claude"].calls == 0

    def test_unknown_backend_rejected(self, service):
        t = service.create_topic("X", research=False)
        with pytest.raises(ValueError):
            service.instruct(t["id"], "go", backend="gemini")

    def test_run_record_has_provenance(self, service, backends):  # W4, K7
        backends["claude"].script = research_writes_brief
        t = service.create_topic("X", research=False)
        service.instruct(t["id"], "go")
        assert service.wait_idle(10)
        run = last_run(service, t["id"])
        for key in ("id", "topic_id", "kind", "backend", "model", "status", "started",
                    "finished", "duration_s", "command", "exit_code", "usage", "cost_usd",
                    "files_changed", "instruction"):
            assert key in run, key
        assert run["files_changed"]["created"] == ["assets/talks.csv"]
        assert run["files_changed"]["modified"] == ["doc.md"]
        assert run["instruction"] == "go"
        assert run["cost_usd"] == pytest.approx(0.01)

    def test_doc_version_bumps_after_run(self, service, backends):  # D1
        backends["claude"].script = research_writes_brief
        t = service.create_topic("X", research=False)
        v0 = service.store.doc_version(t["id"])
        service.instruct(t["id"], "go")
        assert service.wait_idle(10)
        assert service.store.doc_version(t["id"]) > v0

    def test_git_commit_per_run(self, service, backends):  # W5
        backends["claude"].script = research_writes_brief
        t = service.create_topic("X", research=False)
        service.instruct(t["id"], "add chart")
        assert service.wait_idle(10)
        d = service.store.topic_dir(t["id"])
        log = subprocess.run(["git", "-C", str(d), "log", "--format=%s"], capture_output=True,
                             text=True).stdout
        run = last_run(service, t["id"])
        assert run["id"] in log

    def test_failed_run_is_recorded_not_raised(self, service, backends):  # K5
        backends["claude"].script = lambda req: {"ok": False, "error_kind": "auth",
                                                 "error": "Please run /login"}
        t = service.create_topic("X", research=False)
        service.instruct(t["id"], "go")
        assert service.wait_idle(10)
        run = last_run(service, t["id"])
        assert run["status"] == "error" and run["error_kind"] == "auth"
        assert "login" in run["error"]

    def test_progress_events_are_visible_while_running(self, service, backends):  # T3
        gate = threading.Event()

        def slow(req):
            gate.wait(5)
            return {"events": [Event(kind="tool", text="WebSearch: formal methods")]}
        backends["claude"].script = slow
        t = service.create_topic("X", research=False)
        run = service.instruct(t["id"], "go")
        wait_for(lambda: service.get_run(run["id"])["status"] == "running")
        gate.set()
        assert service.wait_idle(10)
        progress = service.get_run(run["id"])["progress"]
        assert any("WebSearch" in p["text"] for p in progress)

    def test_cancel(self, service, backends):  # T3
        backends["claude"].script = lambda req: {"sleep": 30}
        t = service.create_topic("X", research=False)
        run = service.instruct(t["id"], "go")
        wait_for(lambda: service.get_run(run["id"])["status"] == "running")
        assert service.cancel_run(run["id"]) is True
        assert service.wait_idle(10)
        assert service.get_run(run["id"])["status"] == "cancelled"

    def test_concurrency_cap(self, service, backends):  # K6
        active, peak, lock = [0], [0], threading.Lock()

        def tracked(req):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.3)
            with lock:
                active[0] -= 1
            return {}
        backends["claude"].script = tracked
        backends["codex"].script = tracked
        t = service.create_topic("X", research=False)
        for i in range(5):
            service.instruct(t["id"], f"go {i}", backend="claude" if i % 2 else "codex")
        assert service.wait_idle(15)
        assert peak[0] == 2
        assert len(service.store.list_runs(t["id"])) == 5


# ---------- questions and threads ----------

ANCHOR = dict(quote="burn rate cancels out", prefix="Budget spent = ",
              suffix=". So the 1h page", start=420)


class TestAsk:
    def topic(self, service):
        t = service.create_topic("SLO alerts", research=False)
        service.store.write_doc(t["id"], "# SLO alerts\n\nBudget spent = burn rate cancels out. "
                                         "So the 1h page fires at 2%.")
        return t

    def test_ask_creates_thread_and_answers(self, service, backends):
        t = self.topic(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert th["quote"] == ANCHOR["quote"]
        assert service.wait_idle(10)
        th = service.store.get_thread(t["id"], th["id"])
        assert [x["role"] for x in th["turns"]] == ["user", "assistant"]
        a = th["turns"][-1]
        assert a["status"] == "done" and a["text"] == "answer from claude"
        assert a["backend"] == "claude" and a["model"] == "claude-opus-5-5"
        assert a["run_id"] and a["session_id"]

    def test_answer_prompt_has_passage_context_question_and_doc(self, service, backends):  # H5
        t = self.topic(service)
        service.ask(t["id"], question="Why does it cancel?", **ANCHOR)
        assert service.wait_idle(10)
        p = backends["claude"].requests[-1].prompt
        assert "burn rate cancels out" in p
        assert "Budget spent = burn rate cancels out. So the 1h page" in p
        assert "Why does it cancel?" in p
        assert "fires at 2%" in p  # the document itself

    def test_default_question(self, service, backends):  # H1
        t = self.topic(service)
        th = service.ask(t["id"], question="", **ANCHOR)
        assert th["turns"][0]["text"] == "What does this mean?"

    def test_ask_on_other_backend(self, service, backends):  # H8
        t = self.topic(service)
        th = service.ask(t["id"], question="Why?", backend="codex", **ANCHOR)
        assert service.wait_idle(10)
        a = service.store.get_thread(t["id"], th["id"])["turns"][-1]
        assert a["backend"] == "codex" and a["text"] == "answer from codex"

    def test_follow_up_same_backend_resumes_session(self, service, backends):  # K3
        t = self.topic(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        first_session = service.store.get_thread(t["id"], th["id"])["turns"][-1]["session_id"]
        service.follow_up(t["id"], th["id"], "And for the 6h page?")
        assert service.wait_idle(10)
        req = backends["claude"].requests[-1]
        assert req.session_id == first_session
        assert "And for the 6h page?" in req.prompt

    def test_follow_up_switching_backend_resends_thread(self, service, backends):  # K3, H8
        t = self.topic(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        service.follow_up(t["id"], th["id"], "Check this on the other model", backend="codex")
        assert service.wait_idle(10)
        req = backends["codex"].requests[-1]
        assert req.session_id is None
        for needle in ("burn rate cancels out", "Why?", "answer from claude",
                       "Check this on the other model"):
            assert needle in req.prompt, needle
        turns = service.store.get_thread(t["id"], th["id"])["turns"]
        assert [x["backend"] for x in turns] == ["claude", "claude", "codex", "codex"]

    def test_follow_up_while_answering_is_rejected(self, service, backends):  # K6
        backends["claude"].script = lambda req: {"sleep": 2}
        t = self.topic(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        with pytest.raises(RuntimeError):
            service.follow_up(t["id"], th["id"], "too soon")
        assert service.wait_idle(10)

    def test_failed_answer_then_retry(self, service, backends):  # H10
        backends["claude"].script = lambda req: {"ok": False, "error_kind": "rate_limit",
                                                 "error": "429"}
        t = self.topic(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        a = service.store.get_thread(t["id"], th["id"])["turns"][-1]
        assert a["status"] == "error" and a["error_kind"] == "rate_limit"
        backends["claude"].script = lambda req: {}
        service.retry(t["id"], th["id"])
        assert service.wait_idle(10)
        turns = service.store.get_thread(t["id"], th["id"])["turns"]
        assert [x["role"] for x in turns] == ["user", "assistant"]  # failed answer replaced
        assert turns[-1]["status"] == "done"

    def test_answers_cannot_rewrite_doc_silently(self, service, backends):
        """An answer run may create assets, but must not drop sections of doc.md (D2)."""
        t = self.topic(service)
        before = service.store.read_doc(t["id"])
        backends["claude"].script = lambda req: {"write": {"doc.md": "# wiped\n",
                                                           "assets/plot.csv": "a\n1\n"}}
        th = service.ask(t["id"], question="plot it", **ANCHOR)
        assert service.wait_idle(10)
        assert service.store.read_doc(t["id"]) == before
        a = service.store.get_thread(t["id"], th["id"])["turns"][-1]
        assert "assets/plot.csv" in a["files_changed"]["created"]
