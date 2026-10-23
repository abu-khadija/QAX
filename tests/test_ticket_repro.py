"""
test_ticket_repro.py — a bug ticket becomes a run and a verdict (core/ticket_repro.py), and the
plain-language step reader it relies on never passes a step it did not do.
"""

import asyncio
from pathlib import Path

import pytest

from core.ticket_repro import build, parse, reproduce, verdict

T = Path(__file__).resolve().parent / "data" / "tickets"


def _r(cid, statuses):
    return {"test_case_id": cid, "status": "FAIL" if "FAIL" in statuses else "PASS",
            "steps": [{"step_number": i, "status": s, "actual_result": s.lower()} for i, s in enumerate(statuses, 1)]}


def test_a_ticket_is_read_into_its_parts():
    t = parse((T / "linkding_tag_count_link.md").read_text(encoding="utf-8"))
    assert t["title"].startswith("Tag management") and len(t["steps"]) == 6
    assert t["steps"][5] == 'Click "1" in the "qaxticket" row'
    assert "/bookmarks?q=%23qaxticket" in t["expected"] and "Content missing" in t["actual"]


def test_checks_come_from_quoted_text_or_a_page_and_preconditions_are_met():
    b = build(parse((T / "linkding_admin_tasks.md").read_text(encoding="utf-8")))
    assert b["expected_case"]["manual_steps"][-1]["action"] == 'Verify that the URL contains "/admin/login/"'
    assert b["symptom_case"] is None and "logged-out browser" in b["notes"][0]
    assert [s["action"] for s in b["expected_case"]["manual_steps"]][0] == "Go to /admin/tasks/"
    b = build(parse((T / "demo_wrong_password.md").read_text(encoding="utf-8")))
    assert b["expected_case"]["manual_steps"][-1]["action"] == 'Verify that "Invalid username or password" is shown'


def test_the_verdicts():
    built = {"why_not": []}
    assert verdict(_r("REPRO-EXPECTED", ["PASS", "FAIL"]), None, built)["verdict"] == "REPRODUCED"
    assert verdict(_r("REPRO-EXPECTED", ["PASS", "PASS"]), None, built)["verdict"] == "NOT REPRODUCED"
    v = verdict(_r("REPRO-EXPECTED", ["SKIP", "FAIL"]), None, built)
    assert v["verdict"] == "COULD NOT RUN" and v["step"] == 1            # a step it could not do decides nothing
    assert verdict(None, _r("REPRO-SYMPTOM", ["PASS", "PASS"]), built)["verdict"] == "REPRODUCED"
    assert verdict(None, _r("REPRO-SYMPTOM", ["PASS", "FAIL"]), built)["verdict"] == "NOT REPRODUCED"
    assert verdict(None, None, {"why_not": ["Expected: x"]})["verdict"] == "NOT DECIDABLE"


def test_a_loose_ticket_is_not_decidable_unless_a_writer_supplies_a_case():
    loose = "Saving is broken\n\nSteps to reproduce:\n1. Go to /settings\n2. Click \"Save\"\n\nExpected: it works\nActual: it does not"
    r = reproduce(loose, "http://x", run=lambda cases: [])
    assert r["verdict"] == "NOT DECIDABLE" and "quote the text" in r["why"]
    case = {"title": "save", "automation_steps": [{"step_number": 1, "playwright_action": "navigate", "value": "/settings"},
                                                  {"step_number": 2, "playwright_action": "assert", "assertion_type": "url",
                                                   "assertion_value": "/saved"}]}
    seen = []
    r = reproduce(loose, "http://x", writer=lambda text: case,
                  run=lambda cases: seen.extend(cases) or [_r("REPRO-EXPECTED", ["PASS", "FAIL"])])
    assert r["verdict"] == "REPRODUCED" and r["ai_written"] and seen[0]["test_case_id"] == "REPRO-EXPECTED"
    assert "fix_brief" in r


# ── the plain-language step reader: never a pass for a step it did not do ────


def test_names_match_whole_words_never_substrings():
    from execution.action_mapper import _word_match
    table = {"login_username": "#username", "login_button": "#login-btn", "bundles_preview_page_1": "a.p1", "1": "a.one"}
    assert _word_match("log", table) is None                       # "log" is not "login"
    assert _word_match("login_button", table) == "#login-btn"
    assert _word_match("username", table) == "#username"
    assert _word_match("1", {"bundles_preview_page_1": "a.p1"}) is None   # a number alone is not the key


def test_values_and_paths_are_read_from_plain_language():
    from execution.action_mapper import ActionMapper
    m = ActionMapper.__new__(ActionMapper)
    assert m._extract_fill_value("Enter demo in the Username field") == "demo"
    assert m._extract_fill_value('Type "a b" in the name field') == "a b"
    assert m._extract_fill_value("Click save") == ""
    import os
    os.environ["APP_BASE_URL"] = "http://h"
    assert m._extract_url("Go to /tags?search=x") == "http://h/tags?search=x"      # the query is kept


def test_a_fill_without_a_value_and_a_url_check_without_one_are_not_passes():
    from execution.action_mapper import ActionMapper
    m = ActionMapper.__new__(ActionMapper)
    m.selectors, m._candidates, m.case_vars = {"name": "#n"}, {}, {}

    class _P:
        url = "http://h/a"
        async def fill(self, *a, **k):
            raise AssertionError("must not type")
    r = asyncio.run(m.map_and_execute({"step_number": 1, "action": "Fill in the name field"}, _P()))
    assert r["status"] == "SKIP"
    r = asyncio.run(m._run_assertion(_P(), "Verify the URL", ""))
    assert r["status"] == "SKIP"


def test_the_reproduce_route_keeps_the_gates(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    from web.server import app
    c = app.test_client()
    text = (T / "demo_wrong_password.md").read_text(encoding="utf-8")
    assert c.post("/api/reproduce", json={"text": text, "url": "http://127.0.0.1:1"}).status_code == 400   # authorized?
    assert c.post("/api/reproduce", json={"text": text, "url": "ftp://x", "authorized": True}).status_code == 400
    assert c.post("/api/reproduce", json={"jira": "not a key", "url": "http://x", "authorized": True}).status_code == 400
    assert c.post("/api/reproduce", json={"url": "http://x", "authorized": True}).status_code == 400
    import core.ticket_repro as tr
    monkeypatch.setattr(tr, "reproduce", lambda text, url, project, **kw: {"verdict": "NOT REPRODUCED", "why": "ok"})
    d = c.post("/api/reproduce", json={"text": text, "url": "http://x", "authorized": True}).get_json()
    assert d["verdict"] == "NOT REPRODUCED"
    from core.mcp_server import TOOLS
    assert "qax_reproduce_bug" in {t["name"] for t in TOOLS}


def test_the_how_we_measure_page_is_served_and_linked(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    from web.server import app
    c = app.test_client()
    body = c.get("/how-we-measure").get_data(as_text=True)
    assert "How we measure" in body and "120/120" in body and "125/125" not in body and "What we do not claim yet" in body
    assert "/how-we-measure" in c.get("/").get_data(as_text=True)


def test_the_github_setup_page_is_served_linked_and_names_the_real_variables(monkeypatch, tmp_path):
    """The setup page lists exactly the variables and secrets the shipped workflow reads."""
    import re
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    from web.server import app
    c = app.test_client()
    body = c.get("/setup").get_data(as_text=True)
    assert "Set up QAX on GitHub" in body and "NOT PROVEN" in body
    assert "/setup" in c.get("/").get_data(as_text=True)
    wf = (Path(__file__).resolve().parent.parent / "ci" / "github_action_release_check.yml").read_text(encoding="utf-8")
    for name in set(re.findall(r"(?:vars|secrets)\.(QAX_[A-Z_]+)", wf)):
        assert name in body, name
