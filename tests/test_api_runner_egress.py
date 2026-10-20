"""S2: the API runner must not talk to — or send the token to — an off-origin host."""
from execution.api_runner import APIRunner


def _runner(base):
    r = APIRunner()
    r.setup(base_url=base)   # creates a session, no network
    return r


def test_is_approved_origin_rules():
    r = _runner("http://localhost:8911")
    assert r._is_approved_origin("/orders")                       # relative → same origin
    assert r._is_approved_origin("http://localhost:8911/orders")  # same host+port
    assert not r._is_approved_origin("http://evil.example.com/x") # different host
    assert not r._is_approved_origin("http://localhost:9999/x")   # different port


def test_no_base_url_refuses_absolute_allows_relative():
    r = _runner("")
    assert r._is_approved_origin("/x")
    assert not r._is_approved_origin("http://evil.example.com/x")


def test_off_origin_step_is_blocked_before_any_request_and_token():
    r = _runner("http://localhost:8911")
    step = {"step_number": 1, "method": "GET",
            "path": "http://evil.example.com/steal", "use_auth": "token"}
    res = r.execute_api_step(step, {"token": "super-secret"})   # no network: blocked first
    assert res["status"] == "FAIL" and "egress" in (res["error"] or "").lower()
    assert res["response_code"] is None                          # never sent


# S7: a legacy UI-shaped assertion must not read as PASS (it is not evaluated here).
def test_legacy_assert_step_is_not_evaluated_not_pass():
    r = _runner("http://localhost:8911")   # setup() creates a session, no network
    res = r.execute_step({"step_number": 1, "playwright_action": "assert",
                          "assertion_type": "text", "assertion_value": "ok"})
    assert res["status"] == "SKIP"            # never PASS — nothing was checked
    assert "not evaluated" in res["actual_result"].lower()


def test_anonymous_steps_do_not_carry_the_operators_cookie():
    """Regression (CTFd): in cookie mode a step with no use_auth must be anonymous, or 'POST requires
    authentication' tests are answered 2xx by the cookie and reported as a P1 missing-auth bug."""
    import socket
    import threading
    import wsgiref.simple_server as ws
    from execution.api_runner import APIRunner

    seen = []

    def app(environ, start_response):
        seen.append(("sid=S1" in environ.get("HTTP_COOKIE", ""), environ.get("HTTP_CSRF_TOKEN")))
        start_response("200 OK", [("Content-Type", "application/json")]); return [b"{}"]

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = ws.make_server("127.0.0.1", port, app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = APIRunner(); r.setup(base_url=f"http://127.0.0.1:{port}")
        r.session.cookies.set("sid", "S1"); r.session.headers["CSRF-Token"] = "N1"; r.session_creds = True
        r.execute_api_step({"step_number": 1, "method": "GET", "path": "/x", "expected_status": 200}, {})                 # anonymous
        r.execute_api_step({"step_number": 2, "method": "GET", "path": "/x", "expected_status": 200, "use_auth": "token_a"}, {})   # as A
        r.teardown()
    finally:
        srv.shutdown()
    assert seen == [(False, None), (True, "N1")]


def test_a_case_with_no_condition_checked_nothing_and_is_not_a_pass():
    """A step without expected_status/expect_json 'passes' on any answer, even a 500 —
    so a case made only of such steps is SKIP, not PASS."""
    import socket
    import threading
    import wsgiref.simple_server as ws
    from execution.api_runner import APIRunner

    def app(environ, start_response):
        start_response("500 Internal Server Error", [("Content-Type", "text/plain")]); return [b"boom"]
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = ws.make_server("127.0.0.1", port, app, handler_class=type("Q", (ws.WSGIRequestHandler,),
                                                                     {"log_message": lambda *a: None}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = APIRunner(); r.setup(base_url=f"http://127.0.0.1:{port}")
        unchecked = r.execute_test_case({"test_case_id": "U", "title": "u",
                                         "api_steps": [{"method": "GET", "path": "/x"}]})
        checked = r.execute_test_case({"test_case_id": "C", "title": "c",
                                       "api_steps": [{"method": "GET", "path": "/x", "expected_status": 200}]})
    finally:
        srv.shutdown()
    assert unchecked["status"] == "SKIP" and "nothing was checked" in unchecked["steps"][-1]["error"]
    assert checked["status"] == "FAIL"                       # with a condition, the 500 is caught


def test_a_redirect_off_the_target_is_not_followed_and_credentials_stay_home():
    """Engineering audit 2026-10-20 (P1): requests followed a 302 to another host and carried the
    custom auth header, static headers and the session cookie there. Also: no answer = ERROR
    (environment), and an expected status QAX cannot read is never a silent PASS."""
    import threading

    from flask import Flask, redirect, request as freq
    from werkzeug.serving import make_server
    seen = []
    evil, app = Flask("evil"), Flask("target")

    @evil.route("/<path:p>")
    def steal(p):
        seen.append(dict(freq.headers))
        return "got it"
    e = make_server("127.0.0.1", 0, evil)
    threading.Thread(target=e.serve_forever, daemon=True).start()

    @app.route("/go")
    def go():
        return redirect(f"http://127.0.0.1:{e.server_port}/collect")

    @app.route("/inside")
    def inside():
        return redirect("/ok")

    @app.route("/ok")
    def ok():
        return {"ok": True}
    t = make_server("127.0.0.1", 0, app)
    threading.Thread(target=t.serve_forever, daemon=True).start()
    try:
        r = _runner(f"http://127.0.0.1:{t.server_port}")
        r.session.headers["X-API-Key"] = "canary-key"
        r.session.cookies.set("sid", "canary-cookie")
        res = r.execute_api_step({"step_number": 1, "method": "GET", "path": "/go", "expected_status": 200}, {})
        assert res["status"] == "ERROR" and "not followed" in res["error"]
        assert seen == []                                                  # the other host got nothing
        ok = r.execute_api_step({"step_number": 2, "method": "GET", "path": "/inside", "expected_status": 200}, {})
        assert ok["status"] == "PASS", ok                                  # same-origin redirects still work
        odd = r.execute_api_step({"step_number": 3, "method": "GET", "path": "/ok", "expected_status": "two hundred"}, {})
        assert odd["status"] == "ERROR"
        assert r.execute_api_step({"step_number": 4, "method": "GET", "path": "/ok", "expected_status": "2xx"}, {})["status"] == "PASS"
    finally:
        t.shutdown()
        e.shutdown()
    dead = _runner("http://127.0.0.1:9")
    res = dead.execute_api_step({"step_number": 1, "method": "GET", "path": "/x", "expected_status": 200}, {})
    assert res["status"] == "ERROR" and "environment" in res["error"]
