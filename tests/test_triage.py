"""Failure triage: app / test / environment / unclear — rules from recorded signals, never AI."""
from core.triage import classify, summary


def _r(status, actual="", error="", **kw):
    return dict({"status": status, "steps": [{"step_number": 1, "status": status, "actual_result": actual,
                                              "error": error, **kw.pop("step", {})}]}, **kw)


def test_each_kind_of_failure_gets_its_cause():
    cases = {
        "app": _r("FAIL", "Text mismatch. Expected 'Saved', got 'Error'"),
        "app5xx": _r("FAIL", "GET /api → 500", "Expected status 200, got 500", step={"response_code": 500}),
        "env": _r("ERROR", error="no answer from the app (environment): Connection refused"),
        "envnav": _r("FAIL", "Step execution failed", "Page.goto: net::ERR_CONNECTION_REFUSED at http://x/"),
        "throttled": dict(_r("ERROR"), throttled=True),
        "test": _r("ERROR", "No element named 'save_button' on this page"),
        "testafter": dict(_r("ERROR"), error="Step 3 could not run (ERROR); the failures after it are its consequence"),
        "unclear": _r("FAIL", "Step execution failed", 'Page.click: Timeout 7000ms exceeded.\nCall log:\nwaiting for locator("#save")'),
        "crash": _r("ERROR", "The browser stopped (environment)", "Target closed"),
    }
    got = {k: classify(v)["cause"] for k, v in cases.items()}
    assert got == {"app": "app", "app5xx": "app", "env": "environment", "envnav": "environment",
                   "throttled": "environment", "test": "test", "testafter": "test", "unclear": "unclear",
                   "crash": "environment"}, got
    assert "cause" not in classify({"status": "PASS", "steps": []})
    assert summary(list(cases.values())) == {"app": 2, "test": 2, "environment": 4, "unclear": 1}
