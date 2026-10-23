"""
test_release_check.py — the release decision (core.release_check), offline
==========================================================================
GO only when everything the decision depends on ran and passed; NO-GO on an app failure or a
confirmed security bug at or above the bar; NOT PROVEN when something did not run — never "fine".
"""

import subprocess
import sys
from pathlib import Path

from core.release_check import EXIT, decide, render_markdown

ROOT = Path(__file__).resolve().parent.parent


def _r(cid, status, cause="", **kw):
    return dict({"test_case_id": cid, "title": cid, "status": status, "cause": cause,
                 "steps": [{"step_number": 1, "status": status, "actual_result": "seen"}]}, **kw)


def test_everything_ran_and_passed_is_go():
    d = decide({"results": [_r("A", "PASS"), _r("B", "PASS")]}, scan={"claims": [], "coverage_gaps": []},
               scan_requested=True)
    assert d["verdict"] == "GO" and d["exit_code"] == 0 and d["counts"]["passed"] == 2


def test_an_app_failure_blocks():
    d = decide({"results": [_r("A", "PASS"), _r("B", "FAIL", "app")]})
    assert d["verdict"] == "NO-GO" and d["exit_code"] == 1 and d["blockers"][0]["id"] == "B"


def test_a_test_or_environment_problem_is_not_proven_never_go_never_a_bug():
    for cause in ("test", "environment"):
        d = decide({"results": [_r("A", "PASS"), _r("B", "FAIL", cause)]})
        assert d["verdict"] == "NOT PROVEN" and not d["blockers"]
    d = decide({"results": [_r("A", "PASS"), _r("B", "ERROR", "environment")]})
    assert d["verdict"] == "NOT PROVEN" and d["exit_code"] == 2


def test_no_case_ran_is_not_proven():
    assert decide(None)["verdict"] == "NOT PROVEN"
    assert decide({"results": []}, selection={"run_all": False, "why": "0 of 9", "skipped": []})["verdict"] == "NOT PROVEN"


def test_security_blocks_at_or_above_the_bar_only():
    scan = {"claims": [{"severity": "P2", "title": "IDOR"}], "coverage_gaps": []}
    ok = {"results": [_r("A", "PASS")]}
    assert decide(ok, scan=scan, scan_requested=True, fail_on="P1")["verdict"] == "GO"
    d = decide(ok, scan=scan, scan_requested=True, fail_on="P2")
    assert d["verdict"] == "NO-GO" and d["blockers"][0]["kind"] == "security"


def test_leads_never_block_and_a_failed_scan_is_not_proven():
    ok = {"results": [_r("A", "PASS")]}
    assert decide(ok, scan={"claims": [], "leads": [{"severity": "P1"}]}, scan_requested=True)["verdict"] == "GO"
    assert decide(ok, scan={"error": "connection refused"}, scan_requested=True)["verdict"] == "NOT PROVEN"
    assert decide(ok, scan=None, scan_requested=True)["verdict"] == "NOT PROVEN"
    assert decide(ok, scan=None, scan_requested=False)["verdict"] == "GO"      # not asked for: GO, and the report says so


def test_a_quarantined_failure_does_not_block():
    d = decide({"results": [_r("A", "PASS"), _r("B", "FAIL", "app")]}, quarantined={"B"})
    assert d["verdict"] == "GO" and d["counts"]["quarantined"] == 1


def test_accessibility_blocks_only_when_asked():
    page = {"url": "/signup", "accessibility": [{"rule": "Images must have alt text", "wcag": "1.1.1",
                                                 "impact": "critical", "target": "img"}], "slow": []}
    res = {"results": [_r("A", "PASS", quality={"pages": [page]})]}
    assert decide(res)["verdict"] == "GO" and len(decide(res)["observations"]["accessibility"]) == 1
    assert decide(res, a11y_fail_on="serious")["verdict"] == "NO-GO"


def test_skipped_cases_are_listed_as_not_covered():
    sel = {"run_all": False, "why": "1 of 2", "skipped": [{"id": "B", "reason": "visits only /vets"}]}
    d = decide({"results": [_r("A", "PASS")]}, selection=sel)
    assert d["verdict"] == "GO" and d["not_covered"][0]["id"] == "B"


def test_the_markdown_report_says_what_was_not_checked():
    md = render_markdown(decide({"results": [_r("A", "PASS"), _r("B", "ERROR", "environment")]},
                                scan=None, scan_requested=True), app_url="http://staging", change="origin/main")
    assert "NOT PROVEN" in md and "did not run to a verdict" in md and "security scan was asked for" in md
    assert "no AI decides" in md and "origin/main" in md


def test_exit_codes_are_distinct_for_ci():
    assert EXIT == {"GO": 0, "NO-GO": 1, "NOT PROVEN": 2}


def test_the_command_refuses_to_run_without_authorization(tmp_path):
    p = subprocess.run([sys.executable, str(ROOT / "main.py"), "release-check", "--file", str(tmp_path / "x.json"),
                        "--base-url", "http://127.0.0.1:1"], capture_output=True, text=True, cwd=tmp_path, timeout=60)
    assert p.returncode == 3 and "--authorized" in p.stdout


# ── web routes and MCP tools ─────────────────────────────────────────────────


def _client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    from web.server import app
    return app.test_client()


def test_release_check_route_keeps_the_gates(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    case = [{"test_case_id": "T", "automation_steps": [{"playwright_action": "navigate", "value": "/"}]}]
    assert c.post("/api/release-check", json={"tests": case, "url": "http://127.0.0.1:1"}).status_code == 400  # not authorized
    assert c.post("/api/release-check", json={"tests": [], "url": "http://x", "authorized": True}).status_code == 400
    r = c.post("/api/release-check", json={"tests": case, "url": "http://x", "authorized": True,
                                           "changed_since": "origin/main", "repo": "/no/such/folder"})
    assert r.status_code == 400 and "checkout" in r.get_json()["error"]


def test_release_check_route_returns_the_decision(monkeypatch, tmp_path):
    import services.release_check_service as rcs
    seen = {}

    def fake(tests, url, project, **kw):
        seen.update(kw, tests=len(tests), url=url)
        from core.release_check import decide, render_markdown
        d = decide({"results": [{"test_case_id": "T", "status": "PASS"}]}, scan=None, scan_requested=False)
        return dict(d, report_md=render_markdown(d), files={}, duration_s=0.1)
    monkeypatch.setattr(rcs, "run_release_check", fake)
    c = _client(monkeypatch, tmp_path)
    d = c.post("/api/release-check", json={"tests": [{"test_case_id": "T"}], "url": "http://x", "authorized": True,
                                           "scan_writes": False, "fail_on": "p2"}).get_json()
    assert d["verdict"] == "GO" and seen["scan_read_only"] is True and seen["fail_on"] == "P2" and seen["scan"] is True


def test_fix_brief_route_builds_markdown_without_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("QAX_VAR_SECRET_PW", "hunter2-xyz")
    c = _client(monkeypatch, tmp_path)
    res = {"test_case_id": "T9", "title": "checkout", "status": "FAIL", "cause": "app",
           "steps": [{"step_number": 1, "status": "PASS", "action_type": "navigate",
                      "actual_result": "Navigated to: http://shop/cart"},
                     {"step_number": 2, "status": "FAIL", "action_type": "assert",
                      "actual_result": "Text mismatch. Expected 'Total', got 'hunter2-xyz'"}]}
    case = {"test_case_id": "T9", "automation_steps": [{"step_number": 2, "assertion_type": "text", "assertion_value": "Total"}]}
    md = c.post("/api/fix-brief", json={"result": res, "case": case, "url": "http://shop"}).get_json()["markdown"]
    assert "# Fix: checkout (T9)" in md and "expected: `text Total`" in md and "--only T9" in md
    assert "hunter2-xyz" not in md and "Fix the application, not the test" in md
    assert c.post("/api/fix-brief", json={}).status_code == 400


def test_import_recording_route(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    text = (ROOT / "tests/data/recordings/playwright_add_note.spec.ts").read_text(encoding="utf-8")
    d = c.post("/api/import-recording", json={"text": text}).get_json()
    assert d["format"] == "playwright-js" and d["case"]["automation_steps"] and d["needs_env"] == ["QAX_VAR_PASSWORD"]
    assert c.post("/api/import-recording", json={"text": "nope"}).status_code == 400


def test_mcp_offers_the_new_tools():
    from core.mcp_server import TOOLS, call_tool
    names = {t["name"] for t in TOOLS}
    assert {"qax_release_check", "qax_fix_brief", "qax_import_recording"} <= names
    d, is_err = call_tool("qax_import_recording", {"text": (ROOT / "tests/data/recordings/chrome_add_note.json").read_text(encoding="utf-8")})
    assert not is_err and d["case"]["test_case_id"]
    d, is_err = call_tool("qax_release_check", {"url": "http://127.0.0.1:1", "test_cases": [{"test_case_id": "T"}]})
    assert is_err and "authoriz" in str(d.get("error", "")).lower()
