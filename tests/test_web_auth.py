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
