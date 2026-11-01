"""P3, A1-A3, TT1: TL;DR answers, intent worked out from the comment, titles from statements.

research_app/answers.py:
    parse_answer(text) -> {"intent": "explain"|"update"|"task"|None, "tldr": str, "body": str}
        Leading lines, in order and both optional:
            INTENT: explain|update|task        (case-insensitive; may be wrapped in **)
            TL;DR: one or two sentences        (also "TLDR:", "**TL;DR:**", "**TL;DR**", "TL;DR -")
        Both lines are removed from the body. Without a TL;DR line, tldr is the body's first
        sentence (cut to 200 chars at a word boundary, ending in "…" if cut).

Service: the default intent is "auto". Under auto the backend declares its intent; only an
explicit update or task may change doc.md. Answer turns carry "tldr", "intent" (resolved),
"intent_source" ("auto" or "user") and "doc_restored" when an edit was reverted.

Titles: Service.create_topic(title=None, focus="", backend="claude", research=True,
statement=None). POST /api/topics accepts {"statement": ...} with the title optional.
Working title: the statement's first sentence (split after . ! or ? followed by
whitespace). If it is longer than 60 characters: take the first 59, cut back to the last
space, strip trailing " ,;:-", append "…". Topics without title_source count as "user".
"""
import pytest

from research_app.answers import parse_answer

DOC = """# SLO alerts

Budget spent = burn rate cancels out. So the 1h page fires at 2%.
"""
ANCHOR = dict(quote="burn rate cancels out", prefix="Budget spent = ",
              suffix=". So the 1h page", start=0)
EDITED = DOC.replace("fires at 2%.", "fires at 2% of the 30-day budget.")


def topic(service):
    t = service.create_topic("SLO alerts", research=False)
    service.store.write_doc(t["id"], DOC)
    return t


def last_answer(service, t, th):
    return [x for x in service.store.get_thread(t["id"], th["id"])["turns"]
            if x["role"] == "assistant"][-1]


# ---------- P3: parsing answers ----------

class TestParseAnswer:
    def test_intent_and_tldr_lines(self):
        p = parse_answer("INTENT: update\nTL;DR: Added the 30-day detail.\n\nI edited line 3.")
        assert p == {"intent": "update", "tldr": "Added the 30-day detail.",
                     "body": "I edited line 3."}

    @pytest.mark.parametrize("head", ["**INTENT: task**", "intent: Task", "INTENT:task"])
    def test_intent_variants(self, head):
        assert parse_answer(f"{head}\nTL;DR: x\n\nbody")["intent"] == "task"

    @pytest.mark.parametrize("line", ["TL;DR: Short.", "TLDR: Short.", "**TL;DR:** Short.",
                                      "TL;DR - Short.", "**TL;DR** Short."])
    def test_tldr_variants(self, line):
        p = parse_answer(f"{line}\n\nLonger explanation.")
        assert p["tldr"] == "Short." and p["body"] == "Longer explanation." and p["intent"] is None

    def test_unknown_intent_is_none_and_line_removed(self):
        p = parse_answer("INTENT: delete-all\nTL;DR: x\n\nbody")
        assert p["intent"] is None and "INTENT" not in p["body"]

    def test_fallback_first_sentence(self):
        p = parse_answer("The 14.4 comes from the SRE workbook [1]. It is a design choice.")
        assert p["intent"] is None
        assert p["tldr"] == "The 14.4 comes from the SRE workbook [1]."
        assert p["body"].startswith("The 14.4 comes")

    def test_fallback_is_cut_at_a_word_boundary(self):
        p = parse_answer("word " * 100)
        assert len(p["tldr"]) <= 201 and p["tldr"].endswith("…") and " wor…" not in p["tldr"]

    def test_empty(self):
        assert parse_answer("") == {"intent": None, "tldr": "", "body": ""}


# ---------- A1-A3: intent from the comment ----------

class TestAutoIntent:
    def test_default_is_auto_and_backend_declares_update(self, service, backends):
        backends["claude"].script = lambda req: {
            "write": {"doc.md": EDITED},
            "text": "INTENT: update\nTL;DR: Said 2% of what.\n\nI changed one sentence."}
        t = topic(service)
        th = service.ask(t["id"], question="Make it say 2% of what", **ANCHOR)
        assert service.wait_idle(10)
        a = last_answer(service, t, th)
        assert service.store.get_thread(t["id"], th["id"])["turns"][0]["intent"] == "auto"
        assert (a["intent"], a["intent_source"]) == ("update", "auto")
        assert a["tldr"] == "Said 2% of what." and a["text"] == "I changed one sentence."
        assert service.store.read_doc(t["id"]) == EDITED and a["doc_diff"]
        assert service.get_run(a["run_id"])["kind"] == "edit"

    def test_auto_prompt_explains_the_choice(self, service, backends):
        t = topic(service)
        service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        p = backends["claude"].requests[-1].prompt
        for needle in ("INTENT:", "explain", "update", "task", "TL;DR:", "do not edit doc.md"):
            assert needle in p, needle

    def test_declared_explain_with_edit_is_reverted(self, service, backends):
        backends["claude"].script = lambda req: {"write": {"doc.md": EDITED},
                                                 "text": "INTENT: explain\nTL;DR: Because.\n\nx"}
        t = topic(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        a = last_answer(service, t, th)
        assert a["intent"] == "explain" and a["doc_restored"] is True
        assert service.store.read_doc(t["id"]) == DOC

    def test_declared_task_becomes_a_track(self, service, backends):
        from research_app.tracker import build_overview
        backends["claude"].script = lambda req: {"write": {"assets/c.csv": "a\n1\n"},
                                                 "text": "INTENT: task\nTL;DR: Built a chart.\n\nDone."}
        t = topic(service)
        th = service.ask(t["id"], question="Chart burn rate over time", **ANCHOR)
        assert service.wait_idle(10)
        a = last_answer(service, t, th)
        assert a["intent"] == "task" and a["task_run_id"]
        assert service.get_run(a["task_run_id"])["kind"] == "task"
        assert any(x.get("thread_id") == th["id"]
                   for x in build_overview(service.store, t["id"])["tracks"])

    def test_user_override_still_works(self, service, backends):
        t = topic(service)
        th = service.ask(t["id"], question="Why?", intent="explain", **ANCHOR)
        assert service.wait_idle(10)
        a = last_answer(service, t, th)
        assert (a["intent"], a["intent_source"]) == ("explain", "user")
        # a forced explain says so; it doesn't ask the backend to choose
        p = backends["claude"].requests[-1].prompt
        assert "do not edit doc.md" in p.lower() and "INTENT:" not in p

    def test_every_answer_has_a_tldr(self, service, backends):
        backends["claude"].script = lambda req: {"text": "Plain answer. More detail here."}
        t = topic(service)
        th = service.ask(t["id"], question="Why?", **ANCHOR)
        assert service.wait_idle(10)
        assert last_answer(service, t, th)["tldr"] == "Plain answer."

    def test_api_accepts_auto(self, server, backends):
        c = server.client
        _, t, _ = c.post("/api/topics", {"title": "T", "research": False})
        assert c.post(f"/api/topics/{t['id']}/threads",
                      {**ANCHOR, "question": "?", "intent": "auto"})[0] == 201
        assert server.service.wait_idle(10)


# ---------- TT1: titles from the problem statement ----------

STATEMENT = ("Can formal proofs make SLO burn-rate alerting trustworthy for SRE teams? "
             "I want to understand where proofs help and where they don't.")


class TestTitles:
    def test_working_title_from_statement(self, service):
        t = service.create_topic(statement=STATEMENT, research=False)
        assert t["title"] == "Can formal proofs make SLO burn-rate alerting trustworthy…"
        assert len(t["title"]) <= 60 and t["title_source"] == "statement"
        assert t["focus"] == STATEMENT

    def test_short_first_sentence_is_used_whole(self, service):
        t = service.create_topic(statement="Why is Kafka slow? Lots of details follow.",
                                 research=False)
        assert t["title"] == "Why is Kafka slow?"

    def test_title_follows_doc_h1_after_research(self, service, backends):
        backends["claude"].script = lambda req: {"write": {"doc.md": "# Formal proofs for SLO alerts\n\nBody.\n"}}
        t = service.create_topic(statement=STATEMENT)
        assert service.wait_idle(10)
        got = service.store.get_topic(t["id"])
        assert got["title"] == "Formal proofs for SLO alerts" and got["title_source"] == "doc"

    def test_typed_title_is_never_replaced(self, service, backends):
        backends["claude"].script = lambda req: {"write": {"doc.md": "# Other title\n"}}
        t = service.create_topic(title="My title", statement=STATEMENT)
        assert service.wait_idle(10)
        got = service.store.get_topic(t["id"])
        assert got["title"] == "My title" and got["title_source"] == "user"

    def test_needs_title_or_statement(self, service):
        with pytest.raises(ValueError):
            service.create_topic(statement="   ", research=False)

    def test_api_statement_only(self, server):
        code, t, _ = server.client.post("/api/topics", {"statement": STATEMENT, "research": False})
        assert code == 201 and t["title_source"] == "statement"
        assert server.client.post("/api/topics", {"statement": "", "research": False})[0] == 400
