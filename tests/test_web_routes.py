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
    html = "".join(_c().get(u).get_data(as_text=True) for u in ("/app", "/app.js", "/app.css"))
    for needle in ("/api/plan", "/api/runs", "read_only", "previewPlan()", "notTestedHTML", 'id="readonly"'):
        assert needle in html, f"app.html is missing {needle!r}"


def test_stream_is_posted_not_put_in_a_url_and_secrets_never_in_a_query_string():
    html = "".join(_c().get(u).get_data(as_text=True) for u in ("/app", "/app.js", "/app.css"))
    # api() is the token-aware fetch wrapper (adds X-QAX-Token as a header, never in a URL)
    assert "EventSource" not in html and "api('/api/scan/stream'" in html and "method:'POST'" in html
    assert "async function api(path, opts)" in html and "let r=await fetch(path, opts)" in html
    assert "URLSearchParams({url" not in html                         # the old query-string request is gone


def test_scan_inputs_accept_headers_endpoint_list_and_examples(monkeypatch):
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    from web.server import app as flask_app
    body = {"url": "http://127.0.0.1:9", "token": "T", "cookie": "sid=1", "headers": "CSRF-Token: n1\nbad line",
            "endpoints": "GET /api/x\nPOST /api/x name!", "examples": '{"POST /api/x": {"name": "n {{unique}}"}}'}
    with flask_app.test_request_context("/api/scan/stream", method="POST", json=body):
        from web.server import _inputs
        url, repo, auth, ro, spec, extras = _inputs()
    assert auth["headers"] == {"CSRF-Token": "n1"} and auth["cookie"] == "sid=1" and auth["token"] == "T"
    assert [(e["method"], e["path"]) for e in extras["endpoints"]] == [("GET", "/api/x"), ("POST", "/api/x")]
    assert extras["examples"] == {("POST", "/api/x"): {"name": "n {{unique}}"}}


def test_post_stream_still_requires_the_authorization_confirmation(monkeypatch):
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    r = _c().post("/api/scan/stream", json={"url": "http://127.0.0.1:9"})
    assert r.status_code == 400 and "authorized" in r.get_json()["error"]
