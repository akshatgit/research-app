"""I1-I7: comment intents - explain, update (edit doc.md) and task.

Service API (extends v0.5):
    ask(topic_id, quote, prefix, suffix, start, question, backend=None, intent="explain")
    follow_up(topic_id, thread_id, question, backend=None, intent="explain")
    undo_edit(topic_id, thread_id, turn_number) -> thread
        turn_number is 1-based, as in the turns/NNN-* files. Raises KeyError (no such
        thread/turn), ValueError (not an update turn, or already undone), RuntimeError
        (doc.md changed since that edit).
Turn fields: user turns carry `intent`; answer turns carry `intent` and, by intent:
    update -> run kind "edit", `doc_diff` (unified diff, "" if no change), `undone`,
              `passage_changed`
    task   -> run kind "task", `task_run_id`, `passage_changed`
"""
import pytest

from conftest import wait_for

DOC = """# SLO alerts

Budget spent = burn rate cancels out. So the 1h page fires at 2%.

## Sources

1. [A](https://a.example)
"""
ANCHOR = dict(quote="burn rate cancels out", prefix="Budget spent = ",
              suffix=". So the 1h page", start=0)
EDITED = DOC.replace("So the 1h page fires at 2%.",
                     "So the 1h page fires at 2% of the 30-day budget.")


def topic(service):
    t = service.create_topic("SLO alerts", research=False)
    service.store.write_doc(t["id"], DOC)
    return t


def answers(service, t, th):
    return [x for x in service.store.get_thread(t["id"], th["id"])["turns"]
            if x["role"] == "assistant"]


# ---------- I1/I2: explain is the default and never edits ----------

def test_undeclared_answer_is_explain_and_doc_is_protected(service, backends):
    """v0.7: the default intent is auto. An answer that declares no intent counts as
    explain, and its edits to doc.md are reverted (A2)."""
    backends["claude"].script = lambda req: {"write": {"doc.md": "# wiped\n"}}
    t = topic(service)
    th = service.ask(t["id"], question="Why?", **ANCHOR)
    assert service.wait_idle(10)
    turns = service.store.get_thread(t["id"], th["id"])["turns"]
    assert turns[0]["intent"] == "auto" and turns[1]["intent"] == "explain"
    assert service.store.read_doc(t["id"]) == DOC
    assert service.store.list_runs(t["id"])[0]["kind"] == "answer"


def test_unknown_intent_rejected(service):
    t = topic(service)
    with pytest.raises(ValueError):
        service.ask(t["id"], question="?", intent="delete-everything", **ANCHOR)


# ---------- I3: update edits doc.md and records the diff ----------

class TestUpdate:
    def test_update_edits_doc_and_records_diff(self, service, backends):
        t = topic(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        backends["claude"].script = lambda req: {"write": {"doc.md": EDITED},
                                                 "text": "Clarified the 2%."}
        service.follow_up(t["id"], th["id"], "Say 2% of what", intent="update")
        assert service.wait_idle(10)
        assert service.store.read_doc(t["id"]) == EDITED
        a = answers(service, t, th)[-1]
        assert a["intent"] == "update" and a["status"] == "done"
        assert "-Budget spent = burn rate cancels out. So the 1h page fires at 2%." in a["doc_diff"]
        assert "+Budget spent = burn rate cancels out. So the 1h page fires at 2% of the 30-day budget." in a["doc_diff"]
        assert a["undone"] is False and a["passage_changed"] is False
        run = service.get_run(a["run_id"])
        assert run["kind"] == "edit" and "doc.md" in run["files_changed"]["modified"]

    def test_update_resumes_the_thread_session_with_edit_permission(self, service, backends):
        t = topic(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        session = answers(service, t, th)[-1]["session_id"]
        service.follow_up(t["id"], th["id"], "Add the 30-day detail", intent="update")
        assert service.wait_idle(10)
        req = backends["claude"].requests[-1]
        assert req.session_id == session                       # I6: knows the discussion
        assert "Add the 30-day detail" in req.prompt
        assert "edit doc.md" in req.prompt.lower()             # permission is explicit
        assert "burn rate cancels out" in req.prompt           # which passage

    def test_explain_prompt_forbids_editing(self, service, backends):
        t = topic(service)
        service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        assert "do not edit doc.md" in backends["claude"].requests[-1].prompt.lower()

    def test_update_as_first_turn(self, service, backends):
        backends["codex"].script = lambda req: {"write": {"doc.md": EDITED}}
        t = topic(service)
        th = service.ask(t["id"], question="Clarify 2%", backend="codex", intent="update",
                         **ANCHOR)
        assert service.wait_idle(10)
        assert service.store.read_doc(t["id"]) == EDITED
        assert answers(service, t, th)[-1]["doc_diff"]

    def test_update_with_no_change(self, service, backends):
        t = topic(service)
        th = service.ask(t["id"], question="Fix it", intent="update", **ANCHOR)
        assert service.wait_idle(10)
        a = answers(service, t, th)[-1]
        assert a["status"] == "done" and a["doc_diff"] == ""

    def test_passage_changed_when_quote_is_rewritten(self, service, backends):  # I7
        backends["claude"].script = lambda req: {"write": {"doc.md": DOC.replace(
            "burn rate cancels out", "the rate drops out of the formula")}}
        t = topic(service)
        th = service.ask(t["id"], question="Reword", intent="update", **ANCHOR)
        assert service.wait_idle(10)
        assert answers(service, t, th)[-1]["passage_changed"] is True


# ---------- I4: undo ----------

class TestUndo:
    def edited(self, service, backends):
        backends["claude"].script = lambda req: {"write": {"doc.md": EDITED}}
        t = topic(service)
        th = service.ask(t["id"], question="Clarify", intent="update", **ANCHOR)
        assert service.wait_idle(10)
        return t, th

    def test_undo_restores_doc(self, service, backends):
        t, th = self.edited(service, backends)
        got = service.undo_edit(t["id"], th["id"], 2)
        assert service.store.read_doc(t["id"]) == DOC
        assert got["turns"][1]["undone"] is True

    def test_undo_twice_is_rejected(self, service, backends):
        t, th = self.edited(service, backends)
        service.undo_edit(t["id"], th["id"], 2)
        with pytest.raises(ValueError):
            service.undo_edit(t["id"], th["id"], 2)

    def test_undo_refuses_when_doc_changed_since(self, service, backends):
        t, th = self.edited(service, backends)
        service.store.write_doc(t["id"], EDITED + "\nLater edit.\n")
        with pytest.raises(RuntimeError):
            service.undo_edit(t["id"], th["id"], 2)
        assert service.store.read_doc(t["id"]).endswith("Later edit.\n")

    def test_undo_only_applies_to_update_turns(self, service, backends):
        t = topic(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        with pytest.raises(ValueError):
            service.undo_edit(t["id"], th["id"], 2)
        with pytest.raises(KeyError):
            service.undo_edit(t["id"], th["id"], 9)


# ---------- I5: task ----------

class TestTask:
    def test_task_becomes_a_linked_track(self, service, backends):
        from research_app.tracker import build_overview
        backends["claude"].script = lambda req: {"write": {"assets/check.csv": "claim,ok\n1,yes\n"}}
        t = topic(service)
        th = service.ask(t["id"], question="Verify this against two sources", intent="task",
                         **ANCHOR)
        assert service.wait_idle(10)
        a = answers(service, t, th)[-1]
        assert a["intent"] == "task" and a["task_run_id"]
        run = service.get_run(a["task_run_id"])
        assert run["kind"] == "task" and run["thread_id"] == th["id"]
        track = [x for x in build_overview(service.store, t["id"])["tracks"]
                 if x["id"] == f"run-{run['id']}"][0]
        assert track["title"] == "Verify this against two sources"
        assert track["thread_id"] == th["id"] and track["status"] == "done"
        assert "assets/check.csv" in track["files"]

    def test_task_may_change_the_doc(self, service, backends):
        backends["claude"].script = lambda req: {"write": {"doc.md": EDITED}}
        t = topic(service)
        service.ask(t["id"], question="Rewrite with sources", intent="task", **ANCHOR)
        assert service.wait_idle(10)
        assert service.store.read_doc(t["id"]) == EDITED

    def test_running_task_shows_on_the_thread(self, service, backends):
        backends["claude"].script = lambda req: {"sleep": 1}
        t = topic(service)
        th = service.ask(t["id"], question="Long job", intent="task", **ANCHOR)
        wait_for(lambda: answers(service, t, th) and
                 answers(service, t, th)[-1]["status"] == "running")
        assert service.wait_idle(10)
        assert answers(service, t, th)[-1]["status"] == "done"


# ---------- API ----------

class TestIntentApi:
    def test_intents_and_undo_over_http(self, server, backends):
        c = server.client
        _, t, _ = c.post("/api/topics", {"title": "T", "research": False})
        server.service.store.write_doc(t["id"], DOC)
        backends["claude"].script = lambda req: {"write": {"doc.md": EDITED}}
        code, th, _ = c.post(f"/api/topics/{t['id']}/threads",
                             {**ANCHOR, "question": "Clarify", "intent": "update"})
        assert code == 201
        assert server.service.wait_idle(10)
        code, got, _ = c.post(f"/api/topics/{t['id']}/threads/{th['id']}/turns/2/undo")
        assert code == 200 and got["turns"][1]["undone"] is True
        assert c.post(f"/api/topics/{t['id']}/threads/{th['id']}/turns/2/undo")[0] == 400
        assert c.post(f"/api/topics/{t['id']}/threads/{th['id']}/turns/9/undo")[0] == 404
        code, _, _ = c.post(f"/api/topics/{t['id']}/threads/{th['id']}/turns",
                            {"question": "Make a chart", "intent": "task"})
        assert code == 202
        assert server.service.wait_idle(10)
        assert c.post(f"/api/topics/{t['id']}/threads/{th['id']}/turns",
                      {"question": "x", "intent": "bogus"})[0] == 400

    def test_undo_conflict_is_409(self, server, backends):
        c = server.client
        _, t, _ = c.post("/api/topics", {"title": "T", "research": False})
        server.service.store.write_doc(t["id"], DOC)
        backends["claude"].script = lambda req: {"write": {"doc.md": EDITED}}
        _, th, _ = c.post(f"/api/topics/{t['id']}/threads",
                          {**ANCHOR, "question": "Clarify", "intent": "update"})
        assert server.service.wait_idle(10)
        server.service.store.write_doc(t["id"], "# changed since\n")
        assert c.post(f"/api/topics/{t['id']}/threads/{th['id']}/turns/2/undo")[0] == 409
