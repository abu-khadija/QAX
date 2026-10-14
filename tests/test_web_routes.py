"""Web routes: the app UI is served at /app (and / never 404s), and the scan endpoints
enforce the S4 authorized-target gate just like the CLI's --authorized."""
from web.server import app


def _c():
    return app.test_client()


def test_app_ui_served_at_app_and_root():
    assert _c().get("/app").status_code == 200
    assert _c().get("/").status_code == 200


def test_scan_requires_authorized_target(monkeypatch):
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    r = _c().post("/api/scan", json={"url": "http://127.0.0.1:9"})
    assert r.status_code == 400 and "authorized" in r.get_json()["error"]
    r = _c().get("/api/scan/stream?url=http://127.0.0.1:9")
    assert r.status_code == 400 and "authorized" in r.get_json()["error"]
