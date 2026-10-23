"""
test_recording_import.py — browser recordings become QAX cases (core/recording_import.py)
=======================================================================================
Chrome DevTools Recorder JSON and Playwright codegen (Python, JS/TS) → automation_steps the engine
runs as they are; a recorded password never reaches the case file; checks become assertions.
"""

import json
from pathlib import Path

from core.recording_import import PASSWORD_VAR, convert, detect

REC = Path(__file__).resolve().parent / "data" / "recordings"


def _steps(name):
    r = convert((REC / name).read_text(encoding="utf-8"))
    assert r["case"], r
    return r, r["case"]["automation_steps"]


def test_each_format_is_recognised():
    assert detect((REC / "chrome_add_note.json").read_text(encoding="utf-8")) == "chrome"
    assert detect((REC / "playwright_add_note.py").read_text(encoding="utf-8")) == "playwright-python"
    assert detect((REC / "playwright_add_note.spec.ts").read_text(encoding="utf-8")) == "playwright-js"
    assert detect('{"test_cases": []}') == "qax" and detect("hello") == ""


def test_a_recorded_password_never_reaches_the_case_file():
    for name in ("chrome_add_note.json", "playwright_add_note.py", "playwright_add_note.spec.ts"):
        r, steps = _steps(name)
        assert "demo123" not in json.dumps(r["case"])
        assert PASSWORD_VAR in [s.get("value") for s in steps]
        assert r["needs_env"] == ["QAX_VAR_PASSWORD"]


def test_chrome_prefers_stable_selectors_and_keeps_its_checks():
    r, steps = _steps("chrome_add_note.json")
    clicks = [s["selector_key"] for s in steps if s["playwright_action"] == "click"]
    assert clicks == ["#username", "#login-btn", "#add-note-btn"]          # ids, not aria/xpath
    assert {"playwright_action": "assert", "assertion_type": "url", "assertion_value": "/dashboard"} in \
        [{k: v for k, v in s.items() if k != "step_number"} for s in steps]
    assert steps[-1]["assertion_type"] == "visible" and r["case"]["recorded_on"] == "http://127.0.0.1:8911"
    assert steps[0] == {"playwright_action": "navigate", "value": "/login", "step_number": 1}   # a path, not the host


def test_playwright_locators_become_selectors_the_engine_runs():
    _, py = _steps("playwright_add_note.py")
    sels = [s.get("selector_key") for s in py]
    assert 'role=button[name="Log in"]' in sels and '[placeholder="Username"]' in sels
    url = next(s for s in py if s.get("assertion_type") == "url")
    assert url["assertion_value"] == "dashboard"                             # re.compile(".*dashboard")
    _, ts = _steps("playwright_add_note.spec.ts")
    assert {"playwright_action": "key", "selector_key": "#note-input", "value": "Enter"} in \
        [{k: v for k, v in s.items() if k != "step_number"} for s in ts]      # a real key press, not a click


def test_a_recording_without_a_check_is_imported_but_flagged():
    r = convert('page.goto("http://x/a")\npage.locator("#b").click()\n')
    assert r["case"]["needs_review"] is True and any("no check" in w for w in r["warnings"])


def test_garbage_is_refused_with_a_reason():
    r = convert("not a recording")
    assert r["case"] is None and r["warnings"]


def test_the_engine_accepts_every_selector_form_the_importer_writes():
    """Level 0 of the engine's lookup must take these as selectors, not as element names."""
    import re
    level0 = re.compile(r"^([#.\[]|[a-z]+[#.\[:]|(xpath|text|css|role|internal:[a-z-]+)=)")
    for sel in ('role=button[name="Log in"]', 'text="Saved"', 'internal:label="Email"', '[placeholder="Username"]',
                '[data-testid="x"]', "xpath=//*[@id='a']", "#id", "div.card"):
        assert level0.match(sel), sel
