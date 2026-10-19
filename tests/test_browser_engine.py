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
    server = make_server("127.0.0.1", port, mod.app)
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
