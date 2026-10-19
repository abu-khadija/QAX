"""
test_browser_engine.py — the UI engine in a REAL browser (Playwright + Chromium)
================================================================================
Positive control for functional QA: evals/targets/ui_demo_app.py (one planted bug)
and evals/data/ui_demo_cases.json (each case carries its true outcome). The run
must reproduce the truth exactly — working flows PASS, the planted bug FAILs, a
step QAX cannot run is SKIP (never PASS) — and, with AI healing off (the default),
nothing may be sent to the AI provider even when a key is set.

Skipped (and says why) when Playwright or its Chromium is not installed.
"""

import asyncio
import json
import socket
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

playwright = pytest.importorskip("playwright.async_api", reason="Playwright not installed")


def _chromium_available() -> bool:
    """True when Playwright's Chromium can actually launch here."""
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _chromium_available(),
                                reason="Chromium not installed (playwright install chromium)")


@pytest.fixture
def ui_app(monkeypatch, tmp_path):
    """Serve the demo app on a free local port for one test."""
    import importlib.util

    from werkzeug.serving import make_server
    spec = importlib.util.spec_from_file_location("ui_demo_app", ROOT / "evals/targets/ui_demo_app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = make_server("127.0.0.1", port, mod.app, threaded=True)   # /stream never ends
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("APP_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("DEFAULT_PROJECT", "uidemo")
    monkeypatch.setenv("AUTO_BUG_REPORT", "false")
    monkeypatch.delenv("QAX_AI_HEAL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")   # a key is present …
    monkeypatch.chdir(tmp_path)                                         # screenshots go here
    yield port
    server.shutdown()


def _cases():
    """The positive-control cases with their true outcomes."""
    return json.loads((ROOT / "evals/data/ui_demo_cases.json").read_text())["test_cases"]


def _run(cases):
    """Execute cases with the real PlaywrightRunner + TestExecutor (headless)."""
    from execution.playwright_runner import PlaywrightRunner
    from execution.test_executor import TestExecutor

    async def go():
        runner = PlaywrightRunner()
        await runner.setup(headless=True)
        try:
            return await TestExecutor().execute_batch(cases, runner)
        finally:
            await runner.teardown()
    return asyncio.run(go())


def test_real_browser_run_reproduces_the_truth_and_sends_nothing_to_ai(ui_app, monkeypatch):
    import anthropic

    ai_calls = []   # recorded, not raised: the healer swallows exceptions

    def no_ai(*a, **k):
        ai_calls.append(1)
        raise RuntimeError("blocked in test")
    monkeypatch.setattr(anthropic, "Anthropic", no_ai)          # … but must never be used

    cases = _cases()
    batch = _run(cases)
    got = {r["test_case_id"]: r["status"] for r in batch["results"]}
    want = {c["test_case_id"]: c["expected_outcome"].split()[0] for c in cases}
    assert got == want, batch["results"]
    assert batch["passed"] == 3 and batch["failed"] == 1 and batch["skipped"] == 1
    assert ai_calls == [], "an AI provider call was made with AI healing off"
    failed = next(r for r in batch["results"] if r["status"] == "FAIL")
    assert failed["screenshot"] and Path(failed["screenshot"]).exists()   # evidence captured


def test_an_assertion_that_cannot_be_evaluated_is_not_a_pass(ui_app):
    case = {"test_case_id": "TC-X", "title": "unknown selector", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"},
        {"step_number": 2, "playwright_action": "assert", "assertion_type": "url"}]}  # no value
    r = _run([case])["results"][0]
    assert r["status"] == "SKIP" and r["steps"][1]["status"] == "SKIP"


def test_positive_control_the_ai_detector_sees_a_call_when_healing_is_opted_in(ui_app, monkeypatch):
    """Proves the zero-AI assertion above can fail: with --ai-heal the healer does call out."""
    import anthropic
    calls = []

    def record(*a, **k):
        calls.append(1)
        raise RuntimeError("blocked in test")
    monkeypatch.setattr(anthropic, "Anthropic", record)
    monkeypatch.setenv("QAX_AI_HEAL", "true")
    bug = next(c for c in _cases() if c["expected_outcome"].startswith("FAIL"))
    assert _run([bug])["results"][0]["status"] == "FAIL"     # healing failed → still FAIL
    assert calls, "the detector did not see the opted-in AI call"


def test_web_execute_runs_the_browser_engine_end_to_end(ui_app, monkeypatch):
    """The web UI path: POST /api/execute engine=ui → real Chromium → the same truth."""
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    from web.server import app
    cases = _cases()
    r = app.test_client().post("/api/execute", json={
        "tests": cases, "url": f"http://127.0.0.1:{ui_app}", "engine": "ui", "project": "uidemo",
        "read_only": False, "authorized": True})
    d = r.get_json()
    assert r.status_code == 200, d
    got = {x["test_case_id"]: x["status"] for x in d["results"]}
    assert got == {c["test_case_id"]: c["expected_outcome"].split()[0] for c in cases}
    import os
    assert os.environ.get("QAX_AI_HEAL") is None              # restored after the run



# ── Regressions from the first real-app run (linkding 1.47.0, 2026-10-19) ─────
# Each of these broke QAX on a real app while the demo app was green.


def _login_case(cid):
    return {"test_case_id": cid, "title": "login", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"},
        {"step_number": 2, "playwright_action": "fill", "selector_key": "username", "value": "demo"},
        {"step_number": 3, "playwright_action": "fill", "selector_key": "password", "value": "demo123"},
        {"step_number": 4, "playwright_action": "click", "selector_key": "login_button"},
        {"step_number": 5, "playwright_action": "assert", "assertion_type": "url",
         "assertion_value": "/dashboard"}]}


def test_each_case_gets_a_fresh_session(ui_app):
    """linkding: after one case logged in, /login redirected and every later case lost its form."""
    batch = _run([_login_case("A"), _login_case("B"), _login_case("C")])
    assert [r["status"] for r in batch["results"]] == ["PASS", "PASS", "PASS"], batch["results"]


def test_an_assertion_waits_for_an_async_update_and_a_real_failure_still_fails(ui_app, monkeypatch):
    """linkding (Turbo): checked 3 ms after the click → false FAIL. Now retried until a timeout."""
    monkeypatch.setenv("QAX_ASSERT_TIMEOUT_MS", "3000")

    def case(cid, expected):
        return {"test_case_id": cid, "title": cid, "automation_steps": [
            {"step_number": 1, "playwright_action": "navigate", "value": "/delayed"},
            {"step_number": 2, "playwright_action": "click", "selector_key": "save_button"},
            {"step_number": 3, "playwright_action": "assert", "selector_key": "status",
             "assertion_type": "text", "assertion_value": expected}]}
    batch = _run([case("late-text", "Saved!"), case("never", "Deleted!")])
    got = {r["test_case_id"]: r for r in batch["results"]}
    assert got["late-text"]["status"] == "PASS"
    assert got["never"]["status"] == "FAIL"
    assert "still failing after 3s" in got["never"]["steps"][2]["actual_result"]


def test_a_secret_comes_from_the_environment_and_never_reaches_the_report(ui_app, monkeypatch, tmp_path):
    """Case files must not hold passwords, and typed secrets must not be printed."""
    monkeypatch.setenv("QAX_VAR_DEMO_PASSWORD", "demo123")
    case = _login_case("S")
    case["automation_steps"][2]["value"] = "{{env:QAX_VAR_DEMO_PASSWORD}}"
    batch = _run([case])
    r = batch["results"][0]
    assert r["status"] == "PASS"                              # the real value was typed
    shown = " ".join(s.get("actual_result", "") for s in r["steps"])
    assert "demo123" not in shown and "QAX_VAR_DEMO_PASSWORD" in shown
    from execution.results_reporter import ResultsReporter
    html = Path(ResultsReporter().generate_html_report(batch, output_folder=str(tmp_path))).read_text()
    assert "demo123" not in html


def test_only_qax_var_names_can_be_typed_from_the_environment(monkeypatch):
    from execution.action_mapper import resolve_step_value
    monkeypatch.setenv("QAX_VAR_OK", "v")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    assert resolve_step_value("{{env:QAX_VAR_OK}}") == ("v", "QAX_VAR_OK")
    assert resolve_step_value("{{env:ANTHROPIC_API_KEY}}")[0] != "sk-secret"   # never resolved


def _discover(url, page_name):
    from core.dom_analyzer import DOMAnalyzer
    from playwright.async_api import async_playwright

    async def go():
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True)
            page = await b.new_page()
            try:
                return await DOMAnalyzer().discover_selectors(url, page_name, page)
            finally:
                await b.close()
    return asyncio.run(go())


def test_discovery_works_without_ai_and_on_a_page_that_never_goes_idle(ui_app, monkeypatch):
    """linkding: waiting for network idle timed out (0 selectors); with no AI credit, 0 again."""
    import anthropic
    monkeypatch.delenv("QAX_AI_DISCOVER", raising=False)
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: pytest.fail("AI used without --ai"))
    sel = _discover(f"http://127.0.0.1:{ui_app}/live", "live")
    assert sel.get("live_field") == "#live-input" or "#live-input" in sel.values(), sel


def test_discovering_the_login_page_records_the_login_form(ui_app, monkeypatch):
    """linkding: with APP_USERNAME/PASSWORD set, discovery logged in first and labelled the page behind it."""
    monkeypatch.setenv("APP_USERNAME", "demo")
    monkeypatch.setenv("APP_PASSWORD", "demo123")
    sel = _discover(f"http://127.0.0.1:{ui_app}/login", "login")
    assert {"#username", "#password", "#login-btn"} <= set(sel.values()), sel


def test_page_key_checks_text_anywhere_and_writes_are_recorded(ui_app):
    """No selector for an error that only appears after a click → the built-in `page` key;
    and the run says which data-changing requests it sent (QAX does not undo them)."""
    case = {"test_case_id": "P", "title": "p", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"},
        {"step_number": 2, "playwright_action": "fill", "selector_key": "username", "value": "demo"},
        {"step_number": 3, "playwright_action": "fill", "selector_key": "password", "value": "nope"},
        {"step_number": 4, "playwright_action": "click", "selector_key": "login_button"},
        {"step_number": 5, "playwright_action": "assert", "selector_key": "page",
         "assertion_type": "text", "assertion_value": "Invalid username or password"}]}
    batch = _run([case])
    r = batch["results"][0]
    assert r["status"] == "PASS", r["steps"]
    assert r["writes"] == [{"method": "POST", "host": f"127.0.0.1:{ui_app}", "path": "/login"}]
    assert batch["writes_sent"] == 1


def test_tls_errors_are_not_ignored_unless_opted_in(monkeypatch):
    import execution.playwright_runner as pr
    seen = []

    class _Ctx:
        def set_default_timeout(self, ms): pass
        def on(self, ev, fn): pass
        async def new_page(self): return object()

    class _Browser:
        async def new_context(self, **kw):
            seen.append(kw.get("ignore_https_errors"))
            return _Ctx()
    r = pr.PlaywrightRunner()
    r.browser = _Browser()
    monkeypatch.delenv("QAX_INSECURE", raising=False)
    asyncio.run(r._open_context())
    monkeypatch.setenv("QAX_INSECURE", "true")
    asyncio.run(r._open_context())
    assert seen == [False, True]
