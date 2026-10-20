"""S3: the JSON API is auth-gated when a token is configured, and a caller can never
force a live export."""
from web.server import app


def _c():
    return app.test_client()


def test_api_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("QAX_API_TOKEN", "sekret")
    body = {"items": [{"title": "x"}], "kind": "tests", "target": "memory"}
    assert _c().post("/api/export", json=body).status_code == 401           # no header → 401
    r = _c().post("/api/export", json=body, headers={"X-QAX-Token": "sekret"})
    assert r.status_code != 401                                              # correct header → through


def test_api_open_when_no_token_configured(monkeypatch):
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    r = _c().post("/api/export", json={"items": [], "kind": "tests"})        # passes auth (then 400)
    assert r.status_code != 401


def test_export_cannot_be_forced_live_by_caller(monkeypatch):
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    monkeypatch.delenv("QAX_ALLOW_LIVE_EXPORT", raising=False)
    r = _c().post("/api/export", json={"items": [{"title": "x"}], "kind": "tests",
                                       "target": "memory", "dry_run": False})
    assert r.status_code == 200 and r.get_json().get("dry_run") is True      # forced dry-run


def test_a_web_page_elsewhere_cannot_drive_the_local_api(monkeypatch):
    """CSRF / DNS rebinding: the local API starts scans, runs and Jira writes."""
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    monkeypatch.delenv("QAX_ALLOWED_HOSTS", raising=False)
    body = '{"items": [], "kind": "tests"}'
    c = _c()
    # a cross-site form/fetch "simple request" (text/plain) is refused
    assert c.post("/api/export", data=body, content_type="text/plain").status_code == 415
    assert c.post("/api/export", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/export", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.get("/api/runs", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    # DNS rebinding: a non-loopback Host is refused while no token is set
    assert c.get("/api/config", headers={"Host": "attacker.example"}).status_code == 403
    monkeypatch.setenv("QAX_ALLOWED_HOSTS", "qax.internal")
    assert c.get("/api/config", headers={"Host": "qax.internal:8900"}).status_code == 200
    # the UI itself still works
    r = c.post("/api/export", json={"items": [], "kind": "tests"},
               headers={"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code not in (401, 403, 415)
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["Cache-Control"] == "no-store"


def test_files_serves_screenshots_only(tmp_path, monkeypatch):
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output" / "repos" / "x").mkdir(parents=True)
    (tmp_path / "output" / "repos" / "x" / "a.png").write_bytes(b"\x89PNG")
    (tmp_path / "output" / "report.json").write_text("{}")
    (tmp_path / "output" / "shot.png").write_bytes(b"\x89PNG")
    c = _c()
    assert c.get("/files/report.json").status_code == 404
    assert c.get("/files/repos/x/a.png").status_code == 404
    assert c.get("/files/shot.png").status_code == 200


def test_a_page_on_another_localhost_port_cannot_start_a_scan_with_a_get(monkeypatch):
    """An <img src> on any localhost page is 'same-site' and sends no Origin: GET must be refused."""
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    r = _c().get("/api/scan/stream?url=http://127.0.0.1:9&authorized=true", headers={"Sec-Fetch-Site": "same-site"})
    assert r.status_code == 403
    r = _c().get("/api/scan/stream?url=http://127.0.0.1:9")            # curl: no browser headers → old behaviour
    assert r.status_code == 400 and "authorized" in r.get_json()["error"]


def test_only_one_scan_runs_at_a_time(monkeypatch):
    """Engineering audit 2026-10-20: concurrent scans corrupted each other's AI-usage numbers and
    there was no limit on how many hit the target at once."""
    import web.server as ws
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    assert ws._SCAN_LOCK.acquire(blocking=False)
    try:
        body = {"url": "http://127.0.0.1:9", "authorized": True}
        assert _c().post("/api/scan", json=body).status_code == 409
        assert _c().post("/api/scan/stream", json=body).status_code == 409
    finally:
        ws._SCAN_LOCK.release()


def test_a_scan_stops_when_its_viewer_leaves():
    import queue as _q

    import pytest as _pt

    import web.server as ws
    p = ws._QueueProgress(_q.Queue())
    p.emit("probe", "info", "running")
    p.cancelled = True
    with _pt.raises(ws.ScanCancelled):
        p.emit("probe", "info", "next stage")
