"""
test_ui_findings.py — UI bug findings from a Live-Tour result (pure logic, no browser)
"""

from services.ui_findings import findings_from_tour


def _tour(screens):
    return {"base_url": "http://x", "screens": screens, "login_ok": True, "error": None}


def test_pageerror_is_p2_console_error_is_p3_warning_ignored():
    t = _tour([{"url": "http://x/cart", "errors": [
        "pageerror: TypeError: undefined is not a function",
        "console.error: failed to load widget",
        "console.warning: deprecated API",
    ]}])
    out = findings_from_tour(t)
    sev = {f["title"].split(" on ")[0]: f["severity"] for f in out}
    assert sev.get("Uncaught JavaScript error") == "P2"
    assert sev.get("Console error") == "P3"
    assert len(out) == 2  # the warning produced nothing
    assert all(f["source"] == "ui-tour" for f in out)


def test_network_5xx_is_p2_and_4xx_is_p3():
    t = _tour([{"url": "http://x/dash", "net_errors": [
        "500 GET http://x/api/report",
        "404 GET http://x/api/missing",
        "failed GET http://x/api/down",
    ]}])
    out = findings_from_tour(t)
    by_sev = sorted(f["severity"] for f in out)
    assert by_sev == ["P2", "P3", "P3"]  # 5xx=P2, 4xx=P3, failed=P3
    assert any("Server error (5xx)" in f["title"] for f in out)


def test_clean_screens_yield_nothing():
    t = _tour([{"url": "http://x/", "errors": [], "net_errors": []},
               {"url": "http://x/ok", "elements": [{"selector": "#a"}]}])
    assert findings_from_tour(t) == []


def test_dedupes_and_handles_empty():
    t = _tour([{"url": "http://x/p", "errors": ["pageerror: boom", "pageerror: boom"]}])
    assert len(findings_from_tour(t)) == 1
    assert findings_from_tour(None) == []
    assert findings_from_tour({"screens": []}) == []
