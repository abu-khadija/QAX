"""
test_hardening_round.py — the 2026-10-20 hardening round
========================================================
1. Error text shown in the UI carries no internal paths, credentials or tokens.
2. Jira/Xray calls retry 429/503 with backoff, never re-send a create after a possible success;
   the Xray token is renewed before its 24 h expiry and after a 401.
3. A request that cannot get the browser answers 409 "busy" instead of hanging.
4. Grounding gate: a case using an element QAX did not find on the app is not run until reviewed.
5. Per-run measurements: AI usage scoped to one run, evidence completeness, change selection, cost.
6. AI healing can only repair a moved element — it never turns a failed check into a pass.

Offline: no browser, no Jira, no AI provider.
"""

import asyncio
import threading
import time

import pytest
import requests

# ── 1 · public errors ────────────────────────────────────────────────────────


def test_ui_error_text_has_no_paths_credentials_or_tokens():
    from core.public_error import clean, public_error
    raw = ("open /Users/alice/proj/.env failed; GET https://bob:hunter2@jira.example/rest "
           "token=abc123 Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payloadpayloadpayload")
    out = clean(raw)
    for secret in ("/Users/alice", "hunter2", "abc123", "eyJhbGci"):
        assert secret not in out
    msg = public_error("Scan failed", RuntimeError(raw))
    assert msg.startswith("Scan failed: ") and "ref " in msg and "hunter2" not in msg


def test_a_failing_export_route_does_not_echo_the_raw_exception(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    import core.adapters as adapters
    from web.server import app

    def boom(*a, **k):
        raise RuntimeError("cannot read /home/runner/secret/config.yml with password=s3cret")
    monkeypatch.setattr(adapters, "get_adapter", boom)
    r = app.test_client().post("/api/export", json={"target": "excel", "tests": [{"title": "t"}]})
    body = r.get_data(as_text=True)
    assert "s3cret" not in body and "/home/runner" not in body


# ── 2 · retries and the Xray token ───────────────────────────────────────────


class _Resp:
    def __init__(self, code, headers=None, text="", body=None):
        self.status_code, self.headers, self.text, self._body = code, headers or {}, text, body or {}
        self.content = b"x"

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_a_create_is_retried_after_429_but_not_after_500():
    from core import http_retry
    seq = iter([_Resp(429, {"Retry-After": "1"}), _Resp(201)])
    waits = []
    assert http_retry.send(lambda: next(seq), sleep=waits.append).status_code == 201
    assert waits == [1.0]
    calls = []
    r = http_retry.send(lambda: calls.append(1) or _Resp(500), sleep=lambda s: None)
    assert r.status_code == 500 and len(calls) == 1          # may have been created: not re-sent
    calls.clear()
    r = http_retry.send(lambda: calls.append(1) or _Resp(502), idempotent=True, sleep=lambda s: None)
    assert len(calls) == 3                                    # a search/read is safe to repeat


def test_a_create_whose_answer_timed_out_is_not_sent_again():
    from core import http_retry
    calls = []

    def call():
        calls.append(1)
        raise requests.exceptions.ReadTimeout("read timed out")
    with pytest.raises(requests.exceptions.ReadTimeout):
        http_retry.send(call, sleep=lambda s: None)
    assert len(calls) == 1


def test_jira_bug_filing_survives_one_rate_limit(monkeypatch):
    from core import http_retry
    from core.jira_bugs import JiraBugFiler
    monkeypatch.setattr(http_retry.time, "sleep", lambda s: None)
    f = JiraBugFiler(dry_run=False, base_url="https://jira.invalid", email="e", token="t", project_key="QA")
    answers = iter([_Resp(429), _Resp(201, body={"key": "QA-7"})])
    monkeypatch.setattr(f.session, "post", lambda *a, **k: next(answers))
    monkeypatch.setattr(f, "find_duplicate", lambda fp: None)
    monkeypatch.setattr(f, "screen_fields", lambda: frozenset({"summary", "description", "issuetype", "project", "labels"}))
    res = f.file({"title": "Checkout fails", "description": "d", "steps": ["a"], "expected": "x", "actual": "y"})
    assert res.get("jira_key") == "QA-7", res


def test_xray_token_is_renewed_after_expiry_and_after_a_401(monkeypatch):
    import core.xray_client as xc
    monkeypatch.setenv("XRAY_CLIENT_ID", "id")
    monkeypatch.setenv("XRAY_CLIENT_SECRET", "sec")
    monkeypatch.setenv("JIRA_CLOUD_ID", "cloud-test")      # live mode requires it; was read from the real .env
    c = xc.XrayClient(dry_run=False)
    auths = []

    def fake_post(url, **kw):
        if url == xc.XRAY_AUTH_URL:
            auths.append(1)
            return _Resp(200, text=f'"tok{len(auths)}"')
        bearer = kw["headers"]["Authorization"]
        return _Resp(401) if bearer == "Bearer tok1" and len(auths) == 1 else _Resp(200, body={"key": "T-1"})
    monkeypatch.setattr(xc.requests, "post", fake_post)
    r = c._xray_post("https://xray.invalid/import/test", json_body={}, timeout=5, what="t")
    assert r.status_code == 200 and len(auths) == 2          # 401 → authenticated again, once
    assert c._get_auth_token() == "tok2" and len(auths) == 2  # cached while fresh
    c._auth_token_at -= xc.TOKEN_MAX_AGE + 1                   # 23 h later
    c._get_auth_token()
    assert len(auths) == 3                                     # renewed before Xray's 24 h expiry


# ── 3 · browser busy ─────────────────────────────────────────────────────────


def test_a_second_browser_job_gets_409_busy_instead_of_hanging(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    monkeypatch.setenv("QAX_BROWSER_WAIT", "0.2")
    import web.server as srv
    held = threading.Event()
    release = threading.Event()

    def hold():
        with srv._browser_lock():
            held.set()
            release.wait(5)
    t = threading.Thread(target=hold)
    t.start()
    held.wait(2)
    try:
        t0 = time.monotonic()
        r = srv.app.test_client().post("/api/map", json={"url": "http://127.0.0.1:9/", "project": "p", "authorized": True})
        assert time.monotonic() - t0 < 3
        assert r.status_code == 409, r.get_data(as_text=True)
        assert r.get_json()["busy"] is True
    finally:
        release.set()
        t.join()
    with pytest.raises(srv.BrowserBusy):
        with srv._browser_lock():
            with srv._browser_lock():   # the lock is not re-entrant: a nested wait times out
                pass


# ── 4 · grounding gate ───────────────────────────────────────────────────────


def test_a_case_using_an_element_not_on_the_app_is_held_back_until_reviewed(monkeypatch, tmp_path):
    import execution.test_executor as te
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(te, "_EXECUTION_AVAILABLE", True)
    monkeypatch.delenv("QAX_ALLOW_UNMAPPED", raising=False)
    ex = te.TestExecutor.__new__(te.TestExecutor)
    ex.dry_run, ex.healer, ex.action_mapper = True, None, object()
    ran = []

    async def step(st, page, runner, tc_id):
        ran.append(st)
        return {"step_number": 1, "status": "PASS", "actual_result": "", "error": None}
    ex._execute_step = step

    class _R:
        def is_ready(self): return True
        async def get_page(self): return None
        async def take_screenshot(self, n): return ""
    case = {"test_case_id": "TC-1", "title": "t", "unmapped_elements": ["magic_button"],
            "automation_steps": [{"playwright_action": "click", "selector_key": "magic_button"}]}
    res = asyncio.run(ex.execute_test_case(dict(case), _R()))
    assert res["status"] == "SKIP" and res["cause"] == "test" and "magic_button" in res["not_run_reason"]
    assert not ran
    res = asyncio.run(ex.execute_test_case(dict(case, reviewed=True), _R()))
    assert res["status"] == "PASS" and ran


def test_the_run_preview_lists_an_ungrounded_case_as_not_run():
    from web.server import _execute_plan
    plan = _execute_plan([{"test_case_id": "A", "automation_steps": [{"playwright_action": "click"}],
                           "unmapped_elements": ["ghost"]},
                          {"test_case_id": "B", "automation_steps": [{"playwright_action": "click"}]}],
                         "http://x", False, "ui")
    assert [r["test_case_id"] for r in plan["runnable"]] == ["B"]
    assert "ghost" in plan["not_run"][0]["reason"]


# ── 5 · measurements ─────────────────────────────────────────────────────────


def test_ai_usage_is_measured_per_run_not_mixed_with_other_threads():
    from services import usage
    with usage.measure() as m:
        usage.note(1000, 500, "claude-sonnet")
        t = threading.Thread(target=lambda: usage.note(10_000, 10_000, "claude-opus"))
        t.start()
        t.join()                                     # another job's call, in its own thread
        usage.note(10, 10, "other:llama")
    assert m["calls"] == 2 and m["input_tokens"] == 1010 and m["unpriced_calls"] == 1
    assert 0 < m["estimated_cost_usd"] < 0.02


def test_overview_shows_cost_evidence_and_change_selection_from_saved_runs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import core.app_mapper as am
    from core.project_overview import build_overview
    from core.run_history import save_generation_cost, save_run
    monkeypatch.setattr(am, "CONFIG_DIR", tmp_path / "projects")
    save_generation_cost("shop", {"calls": 3, "estimated_cost_usd": 0.05, "cases": 6})
    save_run("shop", "http://shop.local", [
        {"test_case_id": "T1", "status": "PASS"},
        {"test_case_id": "T2", "status": "FAIL", "screenshot": "s.png", "network_log": "n.har",
         "steps": [{"status": "FAIL", "error": "Text assertion failed"}]},
        {"test_case_id": "T3", "status": "ERROR", "steps": []}],
        cost={"duration_s": 12.5, "cases": 3, "calls": 0, "estimated_cost_usd": 0.0},
        selection={"run_all": False, "why": "3 of 9 case(s)", "selected": [{"id": "T1"}, {"id": "T2"}, {"id": "T3"}],
                   "skipped": [{"id": "T4"}], "affected_pages": {"/owners": ["x"]}, "files": 2})
    cards = {c["id"]: c for c in build_overview("shop", tmp_path / "projects", str(tmp_path / "runs"))["cards"]}
    assert cards["evidence"]["value"] == "1/2 non-passing case(s) fully evidenced"
    assert cards["evidence"]["rows"][0]["id"] == "T3"
    assert cards["change_selection"]["value"] == "3 run · 1 skipped"
    assert "12.5 s" in cards["cost"]["detail"] and "3 AI call(s)" in cards["cost"]["detail"]
    assert cards["not_tested"]["value"].startswith("1 case(s) did not run")
    assert cards["not_tested"]["rows"][0]["id"] == "T3"


# ── 6 · healing never hides a regression ─────────────────────────────────────


class _FakeHealer:
    """A model that always 'finds' an element — the worst case for hiding a regression."""

    def __init__(self):
        self.asked = 0

    async def get_page_html(self, page):
        return "<html><div id='other'>Saved</div></html>"

    async def heal_step(self, step, error, html):
        self.asked += 1
        return {"suggested_selector": "#other", "confidence": 9}

    def persist_selector(self, broken_selector="", healed_selector="", **kw):
        self.persisted = (broken_selector, healed_selector)       # the executor stores the selector that worked
        return True


class _FakeMapper:
    """First attempt fails with `error`; a retry on the healed selector passes."""

    def __init__(self, error):
        self.error = error

    async def map_and_execute(self, step, page, selector_override=None):
        if selector_override:
            return {"status": "PASS", "error": None, "actual_result": "ok"}
        return {"status": "FAIL", "error": self.error, "actual_result": ""}


def _healing_executor(monkeypatch, error):
    import execution.test_executor as te
    monkeypatch.setattr(te, "ai_assist_enabled", lambda: True)
    ex = te.TestExecutor.__new__(te.TestExecutor)
    ex.dry_run, ex.healer, ex.action_mapper = True, _FakeHealer(), _FakeMapper(error)
    return ex


@pytest.mark.parametrize("step, error", [
    ({"playwright_action": "assert", "expected_result": "Saved"}, "Text assertion failed"),
    ({"playwright_action": "verify", "expected_result": "Saved"}, "Timeout 5000ms exceeded."),
    ({"playwright_action": "click", "assertion_type": "visible"}, "Visibility assertion failed"),
    ({"playwright_action": "click"}, "Cannot click a disabled element"),
])
def test_healing_is_refused_when_it_could_hide_a_regression(monkeypatch, step, error):
    ex = _healing_executor(monkeypatch, error)
    res = asyncio.run(ex._execute_step(dict(step, step_number=1), None, None, "TC-1"))
    assert res["status"] == "FAIL" and res["not_healed"]
    assert ex.healer.asked == 0                       # the page never even went to the model


def test_a_moved_element_is_healed_and_the_pass_is_marked(monkeypatch, tmp_path):
    import execution.test_executor as te
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(te, "_EXECUTION_AVAILABLE", True)
    monkeypatch.setenv("AUTO_BUG_REPORT", "false")
    ex = _healing_executor(monkeypatch, "Timeout 5000ms exceeded.\nwaiting for locator('#save')")
    res = asyncio.run(ex._execute_step({"playwright_action": "click", "step_number": 1}, None, None, "TC-1"))
    assert res["status"] == "HEALED" and res["healed_selector"] == "#other"
    assert ex.healer.persisted[1] == "#other"                    # persisted without a second AI question

    class _R:
        def is_ready(self): return True
        async def get_page(self): return None
        async def take_screenshot(self, n): return ""
    case = {"test_case_id": "TC-1", "title": "t", "automation_steps": [{"playwright_action": "click"}]}
    out = asyncio.run(ex.execute_test_case(case, _R()))
    assert out["healed_steps"] == 1 and "AI healing" in out["verdict_note"]


# ── 7 · vendored axe-core stays unmodified (MPL-2.0 file-level obligations) ──


def test_the_vendored_axe_core_is_the_published_file_with_its_licences():
    import hashlib

    from core import page_quality
    folder = page_quality.AXE_PATH.parent
    digest = hashlib.sha256(page_quality.AXE_PATH.read_bytes()).hexdigest()
    assert digest == "c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1", \
        "axe.min.js changed — MPL-2.0 then requires publishing the change; re-vendor the published file instead"
    assert "Mozilla Public License" in (folder / "LICENSE").read_text(encoding="utf-8")
    assert (folder / "LICENSE-3RD-PARTY.txt").is_file()
    assert page_quality.AXE_VERSION in page_quality.AXE_PATH.read_text(encoding="utf-8")[:40]


# ── 8 · a missing login variable is a setup problem, never a bug in the app ──


def test_an_unset_env_variable_stops_the_case_as_an_environment_error(monkeypatch):
    """Found by the before/after benchmark: eShop ran without its QAX_VAR_* login and two cases
    came back FAIL — a false bug report. Now: ERROR, cause environment, the variable named."""
    from core.triage import classify
    from execution.action_mapper import ActionMapper
    monkeypatch.delenv("QAX_VAR_NOPE", raising=False)
    am = ActionMapper.__new__(ActionMapper)
    am.case_vars, am.selectors = {}, {}

    async def resolve(*a, **k):
        return "#pw", "exact"
    am.find_selector_with_fallback = resolve

    class _Page:
        url = "http://127.0.0.1:1/login"
        typed = []
        async def fill(self, sel, val, timeout=0):
            self.typed.append(val)
    page = _Page()
    res = asyncio.run(am.map_and_execute({"playwright_action": "fill", "selector_key": "#pw",
                                          "value": "{{env:QAX_VAR_NOPE}}", "step_number": 1}, page))
    assert res["status"] == "ERROR" and "QAX_VAR_NOPE is not set" in res["error"] and page.typed == []
    case = classify({"status": "ERROR", "steps": [res]})
    assert case["cause"] == "environment"


# ── 9 · functional benchmark integrity ───────────────────────────────────────


def test_every_functional_mutant_still_applies_to_exactly_one_place():
    """A refactor that moves a mutated line must fail here, not turn a mutant into a silent no-op."""
    from evals.functional_mutants import MUTANTS, ROOT
    stale = [name for name, rel, old, _new, _why in MUTANTS if (ROOT / rel).read_text(encoding="utf-8").count(old) != 1]
    assert not stale, stale
    assert len(MUTANTS) == 13


def test_the_real_bug_cases_differ_only_in_their_truth():
    """Old and fixed linkding run the same steps; only expected_outcome changes, and the truth of each
    real-bug case names its upstream source."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "evals" / "data"
    old = json.loads((root / "linkding_realbugs_old_cases.json").read_text(encoding="utf-8"))["test_cases"]
    fixed = json.loads((root / "linkding_realbugs_fixed_cases.json").read_text(encoding="utf-8"))["test_cases"]
    strip = lambda cs: [{k: v for k, v in c.items() if k != "expected_outcome"} for c in cs]  # noqa: E731
    assert strip(old) == strip(fixed)
    assert [c["expected_outcome"][:4] for c in old] == ["PASS", "FAIL", "FAIL"]
    assert all(c["expected_outcome"] == "PASS" for c in fixed)
    assert all("linkding commit" in c["truth_source"] for c in old[1:])


# ── 10 · what the UI exposes reaches the engine ──────────────────────────────


def test_the_accessibility_choice_in_the_ui_reaches_the_run(monkeypatch, tmp_path):
    import os

    import services.execution_service as es
    import web.server as srv
    monkeypatch.chdir(tmp_path)
    seen = []

    class _Res:
        raw, error = {"results": []}, None
    monkeypatch.setattr(es.ExecutionService, "execute",
                        lambda self, *a, **k: seen.append((os.getenv("QAX_A11Y"), os.getenv("QAX_PAGE_QUALITY"))) or _Res())
    for choice in ("axe", "basic", "off", "junk"):
        srv._run_browser([{"test_case_id": "T"}], "http://127.0.0.1:1", "p", a11y=choice)
    assert seen == [("axe", "true"), ("basic", "true"), ("basic", "false"), ("basic", "true")]
    assert os.getenv("QAX_A11Y") is None                     # restored after the run


def test_the_junit_download_turns_ui_results_into_junit(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    from web.server import app
    r = app.test_client().post("/api/junit", json={"results": [
        {"test_case_id": "T1", "title": "ok", "status": "PASS", "steps": []},
        {"test_case_id": "T2", "title": "broken", "status": "FAIL", "steps": [{"status": "FAIL", "actual_result": "x"}]}]})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "<testsuite" in body and "T2" in body and "<failure" in body


# ── 11 · accessibility and speed cards from what the run saw ─────────────────


def test_accessibility_and_speed_cards_come_from_the_pages_the_run_visited(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    import core.app_mapper as am
    from core.project_overview import build_overview
    from core.run_history import save_run
    monkeypatch.setattr(am, "CONFIG_DIR", tmp_path / "projects")
    pages = [{"url": "/signup", "a11y_engine": "axe-core 4.13.0", "slow": ["lcp_ms"],
              "performance": {"lcp_ms": 3900, "load_ms": 4100, "cls": 0.02},
              "accessibility": [{"rule": "Images must have alternative text", "wcag": "1.1.1", "impact": "critical",
                                 "target": "img"}, {"rule": "Form elements must have labels", "wcag": "4.1.2",
                                                    "impact": "serious", "target": "#email"}]},
             {"url": "/", "a11y_engine": "axe-core 4.13.0", "slow": [], "performance": {"lcp_ms": 900},
              "accessibility": []}]
    save_run("shop", "http://shop.local", [{"test_case_id": "T1", "status": "PASS", "quality": {"pages": pages}}])
    ov = build_overview("shop", tmp_path / "projects", str(tmp_path / "runs"))
    cards = {c["id"]: c for c in ov["cards"]}
    a, sp = cards["accessibility"], cards["speed"]
    assert a["state"] == "bad" and a["value"] == "2 issue(s) on 1 of 2 page(s)"
    assert a["rows"][0]["verdict"] == "CRITICAL" and a["rows"][0]["id"] == "1.1.1" and "axe-core" in a["detail"]
    assert sp["value"] == "1 of 2 page(s) over budget" and sp["rows"][0]["id"] == "/signup" and "3.9 s" in sp["rows"][0]["text"]
    assert "experience" in [g["id"] for g in ov["groups"]]
