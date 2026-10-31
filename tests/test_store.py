"""T1, T4, W1, W3, W4, H3, H10, N3, N5: on-disk storage and filesystem safety."""
import json
import os
import subprocess

import pytest

from research_app.fsutil import atomic_write_text, diff_snapshots, safe_join, snapshot


# ---------- fsutil ----------

class TestSafeJoin:
    def test_plain_relative_path(self, tmp_path):
        assert safe_join(tmp_path, "assets/a.csv") == (tmp_path / "assets" / "a.csv").resolve()

    @pytest.mark.parametrize("bad", ["../x", "assets/../../x", "/etc/passwd", "", ".",
                                     ".git/config"])
    def test_rejects_escapes_and_internal_paths(self, tmp_path, bad):
        with pytest.raises(ValueError):
            safe_join(tmp_path, bad)

    def test_rejects_symlink_escape(self, tmp_path):
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("s")
        (tmp_path / "link").symlink_to(outside)
        with pytest.raises(ValueError):
            safe_join(tmp_path, "link/secret.txt")


class TestAtomicWrite:
    def test_writes_and_leaves_no_temp_files(self, tmp_path):
        p = tmp_path / "f.json"
        atomic_write_text(p, "one")
        atomic_write_text(p, "two")
        assert p.read_text() == "two"
        assert sorted(x.name for x in tmp_path.iterdir()) == ["f.json"]

    def test_failure_keeps_old_content(self, tmp_path, monkeypatch):
        p = tmp_path / "f.json"
        atomic_write_text(p, "old")

        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            atomic_write_text(p, "new")
        monkeypatch.undo()
        assert p.read_text() == "old"
        assert sorted(x.name for x in tmp_path.iterdir()) == ["f.json"]


class TestSnapshots:
    def test_detects_created_modified_deleted(self, tmp_path):
        (tmp_path / "keep.txt").write_text("a")
        (tmp_path / "change.txt").write_text("a")
        (tmp_path / "gone.txt").write_text("a")
        before = snapshot(tmp_path)
        (tmp_path / "change.txt").write_text("b")
        (tmp_path / "gone.txt").unlink()
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "new.csv").write_text("x")
        d = diff_snapshots(before, snapshot(tmp_path))
        assert d == {"created": ["assets/new.csv"], "modified": ["change.txt"],
                     "deleted": ["gone.txt"]}

    def test_ignores_git_and_app_bookkeeping(self, tmp_path):
        before = snapshot(tmp_path)
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("x")
        (tmp_path / "threads").mkdir()
        (tmp_path / "threads" / "t.json").write_text("{}")
        (tmp_path / "runs").mkdir()
        (tmp_path / "runs" / "r.json").write_text("{}")
        assert diff_snapshots(before, snapshot(tmp_path)) == \
            {"created": [], "modified": [], "deleted": []}


# ---------- topics ----------

class TestTopics:
    def test_create_layout(self, store):
        t = store.create_topic("Formal verification for SRE", focus="for a talk", backend="codex")
        assert t["title"] == "Formal verification for SRE"
        assert t["focus"] == "for a talk" and t["backend"] == "codex"
        assert t["id"] and t["created"] > 0
        d = store.topic_dir(t["id"])
        assert (d / "topic.json").is_file()
        assert (d / "doc.md").is_file()
        assert (d / "assets").is_dir() and (d / "threads").is_dir() and (d / "runs").is_dir()
        assert json.loads((d / "topic.json").read_text())["title"] == t["title"]

    def test_default_backend_is_claude(self, store):
        assert store.create_topic("X")["backend"] == "claude"

    def test_rejects_bad_input(self, store):
        with pytest.raises(ValueError):
            store.create_topic("   ")
        with pytest.raises(ValueError):
            store.create_topic("X", backend="gpt-9000")

    def test_ids_are_filesystem_safe_and_unique(self, store):
        a = store.create_topic("../../etc/passwd")
        b = store.create_topic("../../etc/passwd")
        assert a["id"] != b["id"]
        for t in (a, b):
            assert "/" not in t["id"] and ".." not in t["id"]
            assert store.topic_dir(t["id"]).parent == store.topic_dir(a["id"]).parent

    def test_list_newest_first_with_note_count(self, store):
        a = store.create_topic("A")
        b = store.create_topic("B")
        store.create_thread(a["id"], quote="q", prefix="", suffix="", start=0,
                            question="?", backend="claude")
        ts = store.list_topics()
        assert [t["id"] for t in ts] == [b["id"], a["id"]]
        assert {t["id"]: t["note_count"] for t in ts} == {a["id"]: 1, b["id"]: 0}

    def test_get_and_delete(self, store):
        t = store.create_topic("A")
        assert store.get_topic(t["id"])["title"] == "A"
        store.delete_topic(t["id"])
        assert store.list_topics() == []
        with pytest.raises(KeyError):
            store.get_topic(t["id"])

    def test_unknown_or_malicious_topic_id(self, store):
        for bad in ("nope", "../x", ""):
            with pytest.raises(KeyError):
                store.get_topic(bad)

    def test_doc_versioning(self, store):
        t = store.create_topic("A")
        v0 = store.doc_version(t["id"])
        v1 = store.write_doc(t["id"], "# A\n\nhello")
        assert v1 > v0 and store.read_doc(t["id"]) == "# A\n\nhello"
        # an external edit (backend or your editor) also bumps the version
        (store.topic_dir(t["id"]) / "doc.md").write_text("# A\n\nedited outside")
        assert store.doc_version(t["id"]) > v1

    def test_workspace_is_git_repo(self, store):  # W5
        t = store.create_topic("A")
        d = store.topic_dir(t["id"])
        out = subprocess.run(["git", "-C", str(d), "rev-parse", "--is-inside-work-tree"],
                             capture_output=True, text=True)
        assert out.stdout.strip() == "true"


# ---------- threads ----------

class TestThreads:
    def make(self, store):
        t = store.create_topic("A")
        th = store.create_thread(t["id"], quote="burn rate", prefix="the ", suffix=" cancels",
                                 start=120, question="What does this mean?", backend="codex")
        return t, th

    def test_create_thread_shape(self, store):
        t, th = self.make(store)
        assert th["id"] and th["topic_id"] == t["id"]
        assert (th["quote"], th["prefix"], th["suffix"], th["start"]) == \
            ("burn rate", "the ", " cancels", 120)
        assert len(th["turns"]) == 1
        first = th["turns"][0]
        assert (first["role"], first["text"], first["backend"]) == \
            ("user", "What does this mean?", "codex")
        assert first["at"] > 0
        # v0.5 layout (R1): one folder per thread
        assert (store.topic_dir(t["id"]) / "threads" / th["id"] / "thread.json").is_file()

    def test_append_and_update_turns(self, store):
        t, th = self.make(store)
        store.append_turn(t["id"], th["id"], {"role": "assistant", "text": "", "status": "running",
                                              "backend": "codex"})
        store.update_last_turn(t["id"], th["id"], text="It means...", status="done",
                               session_id="s1")
        got = store.get_thread(t["id"], th["id"])
        assert got["turns"][-1]["text"] == "It means..."
        assert got["turns"][-1]["status"] == "done" and got["turns"][-1]["session_id"] == "s1"

    def test_list_and_delete(self, store):
        t, th = self.make(store)
        assert [x["id"] for x in store.list_threads(t["id"])] == [th["id"]]
        store.delete_thread(t["id"], th["id"])
        assert store.list_threads(t["id"]) == []
        with pytest.raises(KeyError):
            store.get_thread(t["id"], th["id"])

    def test_empty_quote_rejected(self, store):
        t = store.create_topic("A")
        with pytest.raises(ValueError):
            store.create_thread(t["id"], quote="  ", prefix="", suffix="", start=0,
                                question="?", backend="claude")


# ---------- runs and assets ----------

class TestRunsAndAssets:
    def test_save_and_list_runs(self, store):
        t = store.create_topic("A")
        store.save_run(t["id"], {"id": "r1", "topic_id": t["id"], "started": 1.0,
                                 "status": "done", "files_changed": {"created": ["assets/a.csv"],
                                                                     "modified": [],
                                                                     "deleted": []}})
        store.save_run(t["id"], {"id": "r2", "topic_id": t["id"], "started": 2.0,
                                 "status": "running", "files_changed": {}})
        assert [r["id"] for r in store.list_runs(t["id"])] == ["r2", "r1"]

    def test_list_assets_with_provenance(self, store):
        t = store.create_topic("A")
        d = store.topic_dir(t["id"])
        (d / "assets" / "a.csv").write_text("x,y\n1,2\n")
        (d / "assets" / "chart.svg").write_text("<svg/>")
        (d / "notes.md").write_text("n")
        store.save_run(t["id"], {"id": "r1", "topic_id": t["id"], "started": 1.0,
                                 "status": "done", "kind": "research",
                                 "files_changed": {"created": ["assets/a.csv"], "modified": [],
                                                   "deleted": []}})
        assets = {a["path"]: a for a in store.list_assets(t["id"])}
        assert set(assets) == {"assets/a.csv", "assets/chart.svg", "notes.md"}
        assert assets["assets/a.csv"]["size"] == len("x,y\n1,2\n")
        assert assets["assets/a.csv"]["type"] == "text/csv"
        assert assets["assets/chart.svg"]["type"] == "image/svg+xml"
        assert assets["assets/a.csv"]["run_id"] == "r1"
        assert assets["assets/chart.svg"]["run_id"] is None
        # bookkeeping files are not assets
        assert not any(p.startswith(("threads/", "runs/", ".git")) or p in ("topic.json", "doc.md")
                       for p in assets)


# ---------- comment resolution (H14) ----------

class TestResolve:
    def test_threads_start_unresolved_and_can_be_resolved(self, store):
        t = store.create_topic("A")
        th = store.create_thread(t["id"], quote="q", prefix="", suffix="", start=0,
                                 question="?", backend="claude")
        assert th["resolved"] is False
        got = store.set_thread_resolved(t["id"], th["id"], True)
        assert got["resolved"] is True and got["resolved_at"] > 0
        assert store.get_thread(t["id"], th["id"])["resolved"] is True
        got = store.set_thread_resolved(t["id"], th["id"], False)
        assert got["resolved"] is False
        assert store.get_thread(t["id"], th["id"])["turns"] == th["turns"]   # nothing lost
