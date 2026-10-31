"""TD3, TD5, TD6, TD7, TD9: the topic dashboard's data.

`tracker.json` (optional, in the topic workspace, maintained by the backend):

    {
      "stats":  [{"label": "Verified references", "value": 66}],
      "tracks": [{"id": "lit-review", "title": "Literature review", "status": "done",
                  "owner": "claude", "updated": 1793480000.0,
                  "summary": "66 references verified",
                  "files": ["related_work/refs.bib"], "log": "logs/lit.log",
                  "run_id": null}]
    }

Statuses: done, running, pending, blocked, failed. research_app/tracker.py provides
load_tracker(workdir) and build_overview(store, topic_id). docmodel provides
split_sections(md).
"""
import json
import time

import pytest

from research_app.docmodel import split_sections
from research_app.tracker import STATUSES, build_overview, load_tracker

DOC = """# Proving SLO alerts

**Summary.** Intro text [1].

## The policy

Rules.

```python
## not a section
```

## Live experiments

Runs.

## Live experiments

Duplicate title.

## Sources

1. [A](https://a.example)
"""


# ---------- sections (TD3) ----------

class TestSections:
    def test_split_in_order_with_summary_first(self):
        s = split_sections(DOC)
        assert [x["title"] for x in s] == ["Summary", "The policy", "Live experiments",
                                          "Live experiments", "Sources"]
        assert [x["id"] for x in s] == ["summary", "the-policy", "live-experiments",
                                       "live-experiments-2", "sources"]

    def test_section_markdown_and_fence_handling(self):
        s = {x["id"]: x for x in split_sections(DOC)}
        assert "Intro text [1]." in s["summary"]["markdown"]
        assert "# Proving SLO alerts" not in s["summary"]["markdown"]   # the H1 is the page title
        assert "## not a section" in s["the-policy"]["markdown"]          # inside a code fence
        assert s["sources"]["markdown"].strip().startswith("## Sources")

    def test_no_summary_tab_when_doc_starts_with_a_section(self):
        assert [x["id"] for x in split_sections("# T\n\n## Only\n\ntext\n")] == ["only"]

    def test_empty_doc(self):
        assert split_sections("") == []


# ---------- tracker.json (TD7) ----------

def write_tracker(d, data):
    (d / "tracker.json").write_text(json.dumps(data) if not isinstance(data, str) else data)


class TestLoadTracker:
    def test_missing_file(self, tmp_path):
        assert load_tracker(tmp_path) == {"stats": [], "tracks": [], "errors": [],
                                          "tabs": "single"}

    def test_valid(self, tmp_path):
        write_tracker(tmp_path, {"stats": [{"label": "Live", "value": 143}],
                                 "tracks": [{"id": "a", "title": "A", "status": "done",
                                             "owner": "codex", "files": ["census/raw.csv"]}]})
        t = load_tracker(tmp_path)
        assert t["errors"] == []
        assert t["stats"] == [{"label": "Live", "value": 143}]
        assert t["tracks"][0]["title"] == "A" and t["tracks"][0]["status"] == "done"

    def test_invalid_json_is_an_error_not_a_crash(self, tmp_path):
        write_tracker(tmp_path, "{not json")
        t = load_tracker(tmp_path)
        assert t["stats"] == [] and t["tracks"] == [] and t["errors"]

    def test_bad_entries_are_dropped_or_fixed_with_errors(self, tmp_path):
        write_tracker(tmp_path, {"stats": [{"label": "ok", "value": 1}, {"value": 2}, "junk"],
                                 "tracks": [{"id": "a", "title": "A", "status": "weird"},
                                            {"title": "no id"},
                                            {"id": "b", "title": "B", "status": "done",
                                             "files": ["ok.md", "../../etc/passwd", ".git/x"]}]})
        t = load_tracker(tmp_path)
        assert t["stats"] == [{"label": "ok", "value": 1}]
        by_id = {x["id"]: x for x in t["tracks"]}
        assert by_id["a"]["status"] == "pending"          # unknown status -> pending
        assert by_id["b"]["files"] == ["ok.md"]           # paths outside the workspace dropped
        assert len(t["errors"]) >= 3

    def test_statuses(self):
        assert set(STATUSES) == {"done", "running", "pending", "blocked", "failed"}


# ---------- overview (TD5, TD6, TD7) ----------

def run_record(topic_id, rid, started, status="done", kind="research", instruction="do it",
               backend="claude", progress=None, created=None):
    return {"id": rid, "topic_id": topic_id, "kind": kind, "backend": backend,
            "model": "m", "status": status, "started": started, "finished": started + 5,
            "instruction": instruction,
            "files_changed": {"created": created or [], "modified": ["doc.md"], "deleted": []},
            "progress": progress or []}


class TestOverview:
    def topic(self, store, doc=DOC):
        t = store.create_topic("SLO study")
        store.write_doc(t["id"], doc)
        return t, store.topic_dir(t["id"])

    def test_shape(self, store):
        t, d = self.topic(store)
        o = build_overview(store, t["id"])
        assert o["topic"]["id"] == t["id"]
        assert o["workspace"] == str(d)
        assert [s["id"] for s in o["sections"]] == ["summary", "the-policy", "live-experiments",
                                                    "live-experiments-2", "sources"]
        assert isinstance(o["generated_at"], float)

    def test_default_stats(self, store):
        t, d = self.topic(store, DOC + '\n```viz\n{"type":"table","data":"assets/a.csv",'
                                       '"title":"T","source":"S"}\n```\n')
        (d / "assets" / "a.csv").write_text("x\n1\n")
        store.create_thread(t["id"], quote="Rules", prefix="", suffix="", start=0,
                            question="?", backend="claude")
        store.save_run(t["id"], run_record(t["id"], "r1", time.time()))
        stats = {s["label"]: s["value"] for s in build_overview(store, t["id"])["stats"]}
        assert stats == {"Sources": 1, "Charts": 1, "Threads": 1, "Runs": 1, "Files": 1}

    def test_tracker_stats_replace_defaults(self, store):
        t, d = self.topic(store)
        write_tracker(d, {"stats": [{"label": "Live", "value": 143}]})
        assert build_overview(store, t["id"])["stats"] == [{"label": "Live", "value": 143}]

    def test_every_research_run_is_a_track(self, store):
        t, d = self.topic(store)
        now = time.time()
        store.save_run(t["id"], run_record(t["id"], "r1", now - 100, instruction="Add a chart",
                                           created=["assets/c.csv"],
                                           progress=[{"kind": "tool", "text": f"step {i}",
                                                      "at": now} for i in range(8)]))
        store.save_run(t["id"], run_record(t["id"], "r2", now - 10, status="running",
                                           backend="codex", instruction="Check claim 3"))
        store.save_run(t["id"], run_record(t["id"], "r3", now - 5, kind="answer"))
        tracks = build_overview(store, t["id"])["tracks"]
        assert [x["id"] for x in tracks] == ["run-r2", "run-r1"]     # answers aren't tracks
        r1 = tracks[1]
        assert r1["title"] == "Add a chart" and r1["status"] == "done" and r1["owner"] == "claude"
        assert r1["files"] == ["assets/c.csv", "doc.md"]
        assert r1["log"] == [f"step {i}" for i in range(3, 8)]       # last 5 progress lines
        assert r1["updated"] == pytest.approx(now - 95)
        assert tracks[0]["status"] == "running" and tracks[0]["owner"] == "codex"

    @pytest.mark.parametrize("run_status,track_status", [("queued", "pending"),
                                                         ("running", "running"), ("done", "done"),
                                                         ("error", "failed"),
                                                         ("cancelled", "failed")])
    def test_run_status_mapping(self, store, run_status, track_status):
        t, d = self.topic(store)
        store.save_run(t["id"], run_record(t["id"], "r1", time.time(), status=run_status))
        assert build_overview(store, t["id"])["tracks"][0]["status"] == track_status

    def test_tracker_tracks_first_and_linked_runs_merge(self, store):
        t, d = self.topic(store)
        now = time.time()
        store.save_run(t["id"], run_record(t["id"], "r1", now - 50, status="running",
                                           instruction="census"))
        store.save_run(t["id"], run_record(t["id"], "r2", now - 20, instruction="other"))
        write_tracker(d, {"tracks": [
            {"id": "census", "title": "Census: web SSH", "status": "pending", "owner": "claude",
             "run_id": "r1", "summary": "planned"},
            {"id": "review", "title": "Review", "status": "pending", "owner": "you"}]})
        tracks = build_overview(store, t["id"])["tracks"]
        assert [x["id"] for x in tracks] == ["census", "review", "run-r2"]
        # a linked run's live status wins over the planned status, and r1 isn't listed twice
        assert tracks[0]["status"] == "running" and tracks[0]["title"] == "Census: web SSH"

    def test_track_log_file_tail(self, store):
        t, d = self.topic(store)
        (d / "logs").mkdir()
        (d / "logs" / "h.log").write_text("\n".join(f"line {i}" for i in range(20)) + "\n")
        write_tracker(d, {"tracks": [{"id": "h", "title": "Harness", "status": "running",
                                      "owner": "codex", "log": "logs/h.log"}]})
        assert build_overview(store, t["id"])["tracks"][0]["log"] == \
            [f"line {i}" for i in range(15, 20)]

    def test_done_count(self, store):
        t, d = self.topic(store)
        write_tracker(d, {"tracks": [{"id": "a", "title": "A", "status": "done"},
                                     {"id": "b", "title": "B", "status": "pending"}]})
        o = build_overview(store, t["id"])
        assert (o["tracks_done"], o["tracks_total"]) == (1, 2)


# ---------- API and prompt ----------

def test_overview_endpoint(server, backends):
    c = server.client
    code, t, _ = c.post("/api/topics", {"title": "T", "research": False})
    code, body, _ = c.get(f"/api/topics/{t['id']}/overview")
    assert code == 200
    assert set(body) >= {"topic", "workspace", "stats", "tracks", "sections", "tracks_done",
                         "tracks_total", "generated_at", "tabs", "layout"}
    assert c.get("/api/topics/nope/overview")[0] == 404


def test_research_prompt_asks_for_tracker(service, backends):  # TD9
    t = service.create_topic("X", research=False)
    service.instruct(t["id"], "go")
    assert service.wait_idle(10)
    p = backends["claude"].requests[-1].prompt
    assert "tracker.json" in p
    for status in ("done", "running", "pending", "blocked"):
        assert status in p


# ---------- TD3 (v0.5): one document by default; split only when the topic asks ----------

class TestTabs:
    def topic(self, store):
        t = store.create_topic("SLO study")
        store.write_doc(t["id"], DOC)
        return t, store.topic_dir(t["id"])

    def test_single_document_by_default(self, store):
        t, d = self.topic(store)
        o = build_overview(store, t["id"])
        assert o["layout"] == "single"
        assert [x["id"] for x in o["tabs"]] == ["overview", "document", "assets", "runs"]
        assert [x["title"] for x in o["tabs"]] == ["Overview", "Document", "Assets", "Runs"]
        assert len(o["sections"]) == 5   # still available, e.g. for a table of contents

    def test_split_when_tracker_asks(self, store):
        t, d = self.topic(store)
        write_tracker(d, {"tabs": "sections"})
        o = build_overview(store, t["id"])
        assert o["layout"] == "sections"
        assert [x["id"] for x in o["tabs"]] == ["overview", "summary", "the-policy",
                                                "live-experiments", "live-experiments-2",
                                                "sources", "assets", "runs"]

    def test_unknown_tabs_value_falls_back_to_single(self, store):
        t, d = self.topic(store)
        write_tracker(d, {"tabs": "columns"})
        o = build_overview(store, t["id"])
        assert o["layout"] == "single" and load_tracker(d)["errors"]


def test_research_prompt_explains_when_to_split(service, backends):
    t = service.create_topic("X", research=False)
    service.instruct(t["id"], "go")
    assert service.wait_idle(10)
    p = backends["claude"].requests[-1].prompt
    assert '"tabs"' in p and '"sections"' in p and '"single"' in p
