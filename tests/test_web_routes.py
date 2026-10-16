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


def test_app_page_wires_plan_preview_history_and_readonly():
    """Regression for 'built but not connected': the page must actually call /api/plan and /api/runs,
    send read_only, and show Not tested. (They shipped in the backend a commit before the UI used them.)"""
    html = _c().get("/app").get_data(as_text=True)
    for needle in ("/api/plan", "/api/runs", "read_only", "previewPlan()", "notTestedHTML", 'id="readonly"'):
        assert needle in html, f"app.html is missing {needle!r}"
