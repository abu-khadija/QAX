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
