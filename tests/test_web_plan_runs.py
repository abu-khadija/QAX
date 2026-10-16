"""Web UI backend for PLAN.md Phase 1 item 7: plan preview before a scan, and run history."""
import json
import socket
import threading
import wsgiref.simple_server as ws

import pytest

from web import server
from web.server import app


def _c():
    return app.test_client()


@pytest.fixture
def live_app():
    """A tiny spec-less app: / lists one endpoint in text, /api/things is live."""
    def wsgi(environ, start_response):
        path = environ.get("PATH_INFO", "/")
        if path == "/":
            body = b"API: GET /api/things"
        elif path == "/api/things":
            body = b"[]"
        else:
            start_response("404 Not Found", [("Content-Type", "text/plain")]); return [b"no"]
        start_response("200 OK", [("Content-Type", "text/html" if path == "/" else "application/json")])
        return [body]
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = ws.make_server("127.0.0.1", port, wsgi)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def test_plan_previews_surface_checks_and_missing_prereqs(live_app, monkeypatch):
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    r = _c().post("/api/plan", json={"url": live_app, "read_only": True})
    assert r.status_code == 200
    p = r.get_json()
    assert p["read_only"] is True
    assert p["surface"]["endpoint_count"] >= 1 and "/api/things" in p["surface"]["sample"]
    assert p["checks"]["total"] == 40 and p["checks"]["skipped_because_read_only"]   # writes skipped
    assert any("second user" in m for m in p["missing_prerequisites"])               # no token_b
    assert "GET/HEAD/OPTIONS" in p["note"]


def test_plan_full_scan_lists_write_probes_as_running(live_app, monkeypatch):
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    p = _c().post("/api/plan", json={"url": live_app, "read_only": False}).get_json()
    assert p["checks"]["skipped_because_read_only"] == [] and p["checks"]["will_run"] == 40
    assert "test records" in p["note"]


def test_plan_needs_a_url():
    assert _c().post("/api/plan", json={}).status_code == 400


def test_run_history_lists_and_loads_saved_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_RUNS_DIR", str(tmp_path))
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    rid = server._save_run({"url": "http://app", "read_only": True, "claims": [{"title": "x"}],
                            "leads": [], "coverage_gaps": [{"probe": "bola"}], "qax_commit": "abc1234"})
    assert rid
    runs = _c().get("/api/runs").get_json()["runs"]
    assert len(runs) == 1 and runs[0]["claims"] == 1 and runs[0]["not_tested"] == 1
    full = _c().get(f"/api/runs/{rid}").get_json()
    assert full["url"] == "http://app" and full["run_id"] == rid
    assert _c().get("/api/runs/nope").status_code == 404
