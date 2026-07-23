"""
test_critic.py — CriticService (verify, don't trust)
=====================================================
Offline tests — the Anthropic client is mocked.

Run with:
    pytest tests/test_critic.py -v
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from services.context import QAXContext
from services.critic_service import CriticService
from services.exceptions import QAXConfigError
from services.results import CriticResult, ExecutionResult


def _ctx() -> QAXContext:
    return QAXContext(anthropic_key="k", project="")


def _exec(results) -> ExecutionResult:
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    return ExecutionResult(mode="api", raw={
        "total": total, "passed": passed,
        "failed": total - passed, "errors": 0,
        "pass_rate": round(passed / total * 100, 1) if total else 0.0,
        "results": results,
    })


def _fail(title, step_status=("FAIL",), got=(401,)):
    return {
        "test_case_id": title, "title": title, "status": "FAIL",
        "steps": [{"step_number": i + 1, "status": s, "response_code": g,
                   "error": "Expected 200, got 401"}
                  for i, (s, g) in enumerate(zip(step_status, got))],
    }


def _fake_anthropic(json_text: str):
    client = MagicMock()
    block = MagicMock()
    block.text = json_text
    msg = MagicMock()
    msg.content = [block]
    client.messages.create.return_value = msg
    return client


# ─────────────────────────────────────────────────────────────────────────────

def test_missing_key_raises():
    with pytest.raises(QAXConfigError):
        CriticService(QAXContext(anthropic_key="")).review(_exec([]), [])


def test_no_failures_returns_empty():
    ex = _exec([{"test_case_id": "T1", "title": "ok", "status": "PASS", "steps": []}])
    result = CriticService(_ctx()).review(ex, [])
    assert isinstance(result, CriticResult)
    assert result.success
    assert result.verdicts == []


def test_classifies_broken_test_vs_real_bug():
    ex = _exec([
        _fail("login without register"),
        _fail("app returns 500 on valid create"),
    ])
    tcs = [
        {"test_case_id": "login without register", "title": "login without register",
         "api_steps": [{"step_number": 1, "method": "POST", "path": "/auth/login",
                        "expected_status": 200}]},
        {"test_case_id": "app returns 500 on valid create", "title": "app returns 500 on valid create",
         "api_steps": [{"step_number": 1, "method": "POST", "path": "/todos",
                        "expected_status": 201}]},
    ]
    verdict_json = json.dumps([
        {"title": "login without register", "classification": "broken_test",
         "reason": "Logs in without registering first.", "suggestion": "Register a user first."},
        {"title": "app returns 500 on valid create", "classification": "real_bug",
         "reason": "Valid create returned 500.", "suggestion": "Reproduce with POST /todos."},
    ])
    with patch("anthropic.Anthropic", return_value=_fake_anthropic(verdict_json)):
        result = CriticService(_ctx()).review(ex, tcs)

    assert len(result.real_bugs) == 1
    assert len(result.broken_tests) == 1
    assert "1 real bug(s)" in result.summary()


def test_unreturned_failure_becomes_uncertain():
    ex = _exec([_fail("A"), _fail("B")])
    # model only returns a verdict for A
    verdict_json = json.dumps([
        {"title": "A", "classification": "broken_test", "reason": "x", "suggestion": "y"},
    ])
    with patch("anthropic.Anthropic", return_value=_fake_anthropic(verdict_json)):
        result = CriticService(_ctx()).review(ex, [])

    assert len(result.verdicts) == 2          # nothing dropped
    assert len(result.uncertain) == 1         # B defaulted to uncertain


def test_bad_json_is_captured_not_crash():
    ex = _exec([_fail("A")])
    with patch("anthropic.Anthropic", return_value=_fake_anthropic("not json")):
        result = CriticService(_ctx()).review(ex, [])
    # No verdicts parsed → the one failure defaults to uncertain, no crash
    assert result.success
    assert len(result.uncertain) == 1
