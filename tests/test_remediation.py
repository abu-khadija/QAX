"""test_remediation.py — fix guidance mapping (pure logic)."""

from services.remediation import remediation_for


def test_probe_findings_get_guidance():
    for probe in ("forge-jwt", "jwt-flaws", "sqli", "nosql", "bola", "excessive-data",
                  "malformed-input", "debug-disclosure", "cookie-flags", "error-handling",
                  "path-traversal", "cors", "security-headers",
                  "mass-assignment", "open-redirect", "rate-limit", "command-injection"):
        r = remediation_for({"probe": probe, "title": "x"})
        assert r.get("why") and r.get("fix"), probe


def test_debugger_title_maps_even_without_probe_key():
    r = remediation_for({"source": "probe", "title": "Interactive debugger exposed on GET /x"})
    assert "debug" in r["fix"].lower()


def test_source_based_guidance():
    assert "console" in remediation_for({"source": "ui-tour", "title": "Console error on /x"})["fix"].lower()
    assert remediation_for({"source": "ui-tour", "title": "Server error (5xx) on a request from /x"}).get("fix")
    assert remediation_for({"source": "visual-regression", "title": "Visual change on screen_00.png"}).get("fix")
    assert remediation_for({"source": "coverage", "title": "Coverage: GET /x requires authentication"}).get("fix")
    assert remediation_for({"source": "coverage", "title": "Coverage: POST /x rejects empty 'name'"}).get("fix")


def test_unknown_returns_empty():
    assert remediation_for({"probe": "mystery", "title": "?"}) == {}
    assert remediation_for("not a dict") == {}
