"""HTTP API used by the web front end, plus N1/N6/N7 security checks.

Endpoints (JSON unless noted):

    GET    /                                      the app (HTML)
    GET    /api/backends                          [{name, available}]
    GET    /api/topics                            list
    POST   /api/topics                            {title, focus?, backend?, research?} -> 201
    GET    /api/topics/{id}                       {topic, doc, doc_version}
    DELETE /api/topics/{id}                       204
    GET    /api/topics/{id}/version               {doc_version, active_runs}
    POST   /api/topics/{id}/instruct              {instruction, backend?} -> 202 run
    GET    /api/topics/{id}/runs                  list
    GET    /api/runs/{run_id}                     run (with progress)
    POST   /api/runs/{run_id}/cancel              200
    GET    /api/topics/{id}/assets                [{path, size, type, run_id}]
    GET    /api/topics/{id}/files/{path}          raw file bytes
    GET    /api/topics/{id}/table?path=...        {columns, rows}
    GET    /api/topics/{id}/viz                   [{index, spec, errors}]
    GET    /api/topics/{id}/threads               list
    POST   /api/topics/{id}/threads               {quote, prefix, suffix, start, question?, backend?} -> 201
    POST   /api/topics/{id}/threads/{tid}/turns   {question, backend?} -> 202 (409 while answering)
    POST   /api/topics/{id}/threads/{tid}/retry   202
    DELETE /api/topics/{id}/threads/{tid}         204
"""
import http.client
import json

import pytest

from conftest import wait_for

BRIEF = """# T

Claim [1].

```viz
{"type": "bar", "data": "assets/t.csv", "x": "year", "y": "n", "title": "N", "source": "test"}
```

## Sources

1. [A](https://a.example)
"""


def new_topic(c, **kw):
    code, t, _ = c.post("/api/topics", {"title": "Topic", "research": False, **kw})
    assert code == 201
    return t


def idle(server):
    assert server.service.wait_idle(10)


class TestBasics:
    def test_index_html(self, server):
        code, body, headers = server.client.get("/", raw=True)
        assert code == 200 and headers["Content-Type"].startswith("text/html")
        assert b"<html" in body.lower() or b"<!doctype" in body.lower()

    def test_binds_localhost_only(self, server):  # N1
        assert server.server_address[0] == "127.0.0.1"

    def test_backends_endpoint(self, server):
        code, body, _ = server.client.get("/api/backends")
        assert code == 200
        assert {b["name"] for b in body} == {"claude", "codex"}
        assert all("available" in b for b in body)

    def test_unknown_route_404(self, server):
        assert server.client.get("/api/nope")[0] == 404


class TestTopicsApi:
    def test_create_list_get_delete(self, server):
        c = server.client
        t = new_topic(c, title="Formal verification", focus="SRE", backend="codex")
        assert t["backend"] == "codex"
        assert [x["id"] for x in c.get("/api/topics")[1]] == [t["id"]]
        code, body, _ = c.get(f"/api/topics/{t['id']}")
        assert code == 200 and body["topic"]["title"] == "Formal verification"
        assert "doc" in body and isinstance(body["doc_version"], int)
        assert c.delete(f"/api/topics/{t['id']}")[0] == 204
        assert c.get(f"/api/topics/{t['id']}")[0] == 404

    @pytest.mark.parametrize("payload", [{"title": ""}, {"title": "x", "backend": "gemini"},
                                         {"focus": "no title"}])
    def test_validation(self, server, payload):
        assert server.client.post("/api/topics", payload)[0] == 400

    def test_create_with_research_runs_backend(self, server, backends):
        backends["claude"].script = lambda req: {"write": {"doc.md": BRIEF}}
        code, t, _ = server.client.post("/api/topics", {"title": "Topic"})
        assert code == 201
        idle(server)
        assert server.client.get(f"/api/topics/{t['id']}")[1]["doc"] == BRIEF

    def test_version_endpoint_for_live_reload(self, server, backends):
        c = server.client
        t = new_topic(c)
        v0 = c.get(f"/api/topics/{t['id']}/version")[1]
        assert set(v0) >= {"doc_version", "active_runs"}
        backends["claude"].script = lambda req: {"write": {"doc.md": BRIEF}}
        c.post(f"/api/topics/{t['id']}/instruct", {"instruction": "go"})
        idle(server)
        v1 = c.get(f"/api/topics/{t['id']}/version")[1]
        assert v1["doc_version"] > v0["doc_version"] and v1["active_runs"] == 0


class TestRunsApi:
    def test_instruct_and_inspect_run(self, server, backends):
        c = server.client
        t = new_topic(c)
        backends["codex"].script = lambda req: {"write": {"assets/t.csv": "year,n\n2024,1\n"}}
        code, run, _ = c.post(f"/api/topics/{t['id']}/instruct",
                              {"instruction": "make data", "backend": "codex"})
        assert code == 202 and run["backend"] == "codex"
        idle(server)
        code, r, _ = c.get(f"/api/runs/{run['id']}")
        assert code == 200 and r["status"] == "done"
        assert r["files_changed"]["created"] == ["assets/t.csv"]
        assert [x["id"] for x in c.get(f"/api/topics/{t['id']}/runs")[1]] == [run["id"]]

    def test_instruct_validation(self, server):
        c = server.client
        t = new_topic(c)
        assert c.post(f"/api/topics/{t['id']}/instruct", {"instruction": " "})[0] == 400
        assert c.post(f"/api/topics/{t['id']}/instruct",
                      {"instruction": "x", "backend": "nope"})[0] == 400
        assert c.post("/api/topics/missing/instruct", {"instruction": "x"})[0] == 404

    def test_cancel(self, server, backends):
        c = server.client
        t = new_topic(c)
        backends["claude"].script = lambda req: {"sleep": 30}
        _, run, _ = c.post(f"/api/topics/{t['id']}/instruct", {"instruction": "go"})
        wait_for(lambda: c.get(f"/api/runs/{run['id']}")[1]["status"] == "running")
        assert c.post(f"/api/runs/{run['id']}/cancel")[0] == 200
        idle(server)
        assert c.get(f"/api/runs/{run['id']}")[1]["status"] == "cancelled"


class TestAssetsApi:
    def setup_topic(self, server, backends):
        c = server.client
        t = new_topic(c)
        backends["claude"].script = lambda req: {"write": {
            "doc.md": BRIEF, "assets/t.csv": "year,n\n2024,1\n2025,4\n",
            "assets/d.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>"}}
        c.post(f"/api/topics/{t['id']}/instruct", {"instruction": "go"})
        idle(server)
        return c, t

    def test_list_assets(self, server, backends):
        c, t = self.setup_topic(server, backends)
        assets = {a["path"]: a for a in c.get(f"/api/topics/{t['id']}/assets")[1]}
        assert set(assets) == {"assets/t.csv", "assets/d.svg"}
        assert assets["assets/t.csv"]["run_id"]

    def test_file_download(self, server, backends):
        c, t = self.setup_topic(server, backends)
        code, body, headers = c.get(f"/api/topics/{t['id']}/files/assets/t.csv", raw=True)
        assert code == 200 and body.startswith(b"year,n")
        assert headers["Content-Type"].startswith("text/csv")
        assert headers.get("X-Content-Type-Options") == "nosniff"

    def test_svg_is_sandboxed(self, server, backends):  # N6: SVG can carry script
        c, t = self.setup_topic(server, backends)
        _, _, headers = c.get(f"/api/topics/{t['id']}/files/assets/d.svg", raw=True)
        assert "sandbox" in headers.get("Content-Security-Policy", "")

    @pytest.mark.parametrize("path", ["../topic.json", "..%2f..%2fetc%2fpasswd",
                                      "assets/../../../etc/passwd", ".git/config",
                                      "threads/x.json"])
    def test_file_traversal_blocked(self, server, backends, path):
        c, t = self.setup_topic(server, backends)
        assert c.get(f"/api/topics/{t['id']}/files/{path}", raw=True)[0] in (400, 403, 404)

    def test_missing_file_404(self, server, backends):
        c, t = self.setup_topic(server, backends)
        assert c.get(f"/api/topics/{t['id']}/files/assets/nope.csv", raw=True)[0] == 404

    def test_table_endpoint(self, server, backends):
        c, t = self.setup_topic(server, backends)
        code, body, _ = c.get(f"/api/topics/{t['id']}/table?path=assets/t.csv")
        assert code == 200 and body == {"columns": ["year", "n"], "rows": [[2024, 1], [2025, 4]]}
        assert c.get(f"/api/topics/{t['id']}/table?path=../../x.csv")[0] in (400, 403, 404)

    def test_viz_endpoint_validates(self, server, backends):
        c, t = self.setup_topic(server, backends)
        code, body, _ = c.get(f"/api/topics/{t['id']}/viz")
        assert code == 200 and len(body) == 1
        assert body[0]["index"] == 0 and body[0]["errors"] == []
        assert body[0]["spec"]["type"] == "bar"


class TestThreadsApi:
    def test_thread_lifecycle(self, server, backends):
        c = server.client
        t = new_topic(c)
        code, th, _ = c.post(f"/api/topics/{t['id']}/threads",
                             {"quote": "Claim", "prefix": "", "suffix": " [1]", "start": 4,
                              "question": "Source?"})
        assert code == 201 and th["quote"] == "Claim"
        idle(server)
        got = c.get(f"/api/topics/{t['id']}/threads")[1]
        assert got[0]["turns"][-1]["status"] == "done"
        code, _, _ = c.post(f"/api/topics/{t['id']}/threads/{th['id']}/turns",
                            {"question": "More?", "backend": "codex"})
        assert code == 202
        idle(server)
        turns = c.get(f"/api/topics/{t['id']}/threads")[1][0]["turns"]
        assert [x["backend"] for x in turns] == ["claude", "claude", "codex", "codex"]
        assert c.delete(f"/api/topics/{t['id']}/threads/{th['id']}")[0] == 204
        assert c.get(f"/api/topics/{t['id']}/threads")[1] == []

    def test_turn_while_answering_409(self, server, backends):
        c = server.client
        t = new_topic(c)
        backends["claude"].script = lambda req: {"sleep": 2}
        _, th, _ = c.post(f"/api/topics/{t['id']}/threads",
                          {"quote": "Claim", "prefix": "", "suffix": "", "start": 0})
        assert c.post(f"/api/topics/{t['id']}/threads/{th['id']}/turns",
                      {"question": "again"})[0] == 409
        idle(server)

    def test_retry(self, server, backends):
        c = server.client
        t = new_topic(c)
        backends["claude"].script = lambda req: {"ok": False, "error_kind": "timeout"}
        _, th, _ = c.post(f"/api/topics/{t['id']}/threads",
                          {"quote": "Claim", "prefix": "", "suffix": "", "start": 0})
        idle(server)
        backends["claude"].script = lambda req: {}
        assert c.post(f"/api/topics/{t['id']}/threads/{th['id']}/retry")[0] == 202
        idle(server)
        assert c.get(f"/api/topics/{t['id']}/threads")[1][0]["turns"][-1]["status"] == "done"

    def test_empty_quote_400(self, server):
        c = server.client
        t = new_topic(c)
        assert c.post(f"/api/topics/{t['id']}/threads",
                      {"quote": " ", "prefix": "", "suffix": "", "start": 0})[0] == 400


class TestRequestSafety:  # N7
    def raw(self, server, method, path, body=None, headers=None):
        host, port = server.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        r.read()
        conn.close()
        return r.status

    def test_rejects_foreign_host_header(self, server):
        """DNS rebinding: a page on evil.example resolving to 127.0.0.1."""
        assert self.raw(server, "GET", "/api/topics", headers={"Host": "evil.example"}) in (400, 403)

    def test_rejects_cross_origin_post(self, server):
        port = server.server_address[1]
        status = self.raw(server, "POST", "/api/topics", body=json.dumps({"title": "x"}),
                          headers={"Host": f"127.0.0.1:{port}", "Origin": "https://evil.example",
                                   "Content-Type": "application/json"})
        assert status == 403

    def test_rejects_form_post(self, server):
        """CSRF via a plain HTML form: such requests can't set a JSON content type."""
        port = server.server_address[1]
        status = self.raw(server, "POST", "/api/topics", body="title=x",
                          headers={"Host": f"127.0.0.1:{port}",
                                   "Content-Type": "application/x-www-form-urlencoded"})
        assert status in (400, 415)

    def test_accepts_localhost_host_header(self, server):
        port = server.server_address[1]
        assert self.raw(server, "GET", "/api/topics",
                        headers={"Host": f"localhost:{port}"}) == 200

    def test_body_size_limit(self, server):
        port = server.server_address[1]
        try:
            status = self.raw(server, "POST", "/api/topics",
                              body=json.dumps({"title": "x" * 3_000_000}),
                              headers={"Host": f"127.0.0.1:{port}",
                                       "Content-Type": "application/json"})
            assert status == 413
        except (ConnectionResetError, BrokenPipeError):
            pass  # the server may hang up before the client finishes sending
        # either way nothing was created and the server is still up
        assert server.client.get("/api/topics")[:2] == (200, [])


class TestResolveApi:  # H14
    def test_resolve_and_reopen(self, server, backends):
        c = server.client
        t = new_topic(c)
        _, th, _ = c.post(f"/api/topics/{t['id']}/threads",
                          {"quote": "Claim", "prefix": "", "suffix": "", "start": 0})
        idle(server)
        code, body, _ = c.post(f"/api/topics/{t['id']}/threads/{th['id']}/resolve",
                               {"resolved": True})
        assert code == 200 and body["resolved"] is True
        assert c.get(f"/api/topics/{t['id']}/threads")[1][0]["resolved"] is True
        assert c.post(f"/api/topics/{t['id']}/threads/{th['id']}/resolve",
                      {"resolved": False})[1]["resolved"] is False
        assert c.post(f"/api/topics/{t['id']}/threads/{th['id']}/resolve",
                      {"resolved": "yes"})[0] == 400
        assert c.post(f"/api/topics/{t['id']}/threads/missing/resolve",
                      {"resolved": True})[0] == 404


class TestNetworkAccess:  # N1 (revised): opt-in network binding, still DNS-rebinding safe
    @pytest.fixture
    def lan_server(self, tmp_path, backends):
        import threading
        from research_app.server import create_server
        srv = create_server(tmp_path / "data", backends=backends, host="0.0.0.0", port=0,
                            allowed_hosts=["10.0.0.164", "vm.tailnet.example"])
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield srv
        srv.shutdown(); srv.server_close(); srv.service.shutdown()

    def raw(self, srv, method, path, headers, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse(); r.read(); conn.close()
        return r.status

    def test_binds_requested_interface(self, lan_server):
        assert lan_server.server_address[0] == "0.0.0.0"

    def test_allowed_names_and_loopback_accepted(self, lan_server):
        port = lan_server.server_address[1]
        for name in ("10.0.0.164", "vm.tailnet.example", "127.0.0.1", "localhost"):
            assert self.raw(lan_server, "GET", "/api/topics", {"Host": f"{name}:{port}"}) == 200

    def test_other_names_and_wrong_port_rejected(self, lan_server):
        port = lan_server.server_address[1]
        assert self.raw(lan_server, "GET", "/api/topics", {"Host": f"evil.example:{port}"}) in (400, 403)
        assert self.raw(lan_server, "GET", "/api/topics", {"Host": "10.0.0.164:1"}) in (400, 403)

    def test_origin_must_match_an_allowed_name(self, lan_server):
        port = lan_server.server_address[1]
        ok = {"Host": f"10.0.0.164:{port}", "Origin": f"http://10.0.0.164:{port}",
              "Content-Type": "application/json"}
        assert self.raw(lan_server, "POST", "/api/topics", ok,
                        json.dumps({"title": "x", "research": False})) == 201
        bad = {**ok, "Origin": "https://evil.example"}
        assert self.raw(lan_server, "POST", "/api/topics", bad, json.dumps({"title": "y"})) == 403


def test_static_files_are_revalidated(server):
    """After a deploy the browser must not keep running a cached app.js."""
    for path in ("/", "/web/app.js"):
        code, _, headers = server.client.get(path, raw=True)
        assert code == 200 and headers.get("Cache-Control") == "no-cache"
