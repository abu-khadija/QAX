"""
test_investigator.py — InvestigatorService (verify before you file)
===================================================================
Offline tests — ExecutionService (reproduction) and the Anthropic client are
both mocked, so no network or live app is needed.

Run with:
    pytest tests/test_investigator.py -v
"""

import json
from unittest.mock import MagicMock, patch

from services.context import QAXContext
from services.investigator_service import InvestigatorService
from services.results import CriticResult, FailureVerdict, InvestigationResult


def _ctx() -> QAXContext:
    return QAXContext(anthropic_key="k", project="")


def _bug(title: str) -> FailureVerdict:
    return FailureVerdict(test_case_id=title, title=title, classification="real_bug",
                          reason="suspected", suggestion="repro")


def _tc(title: str) -> dict:
    return {"test_case_id": title, "title": title,
            "api_steps": [{"step_number": 1, "method": "POST", "path": "/x",
                           "body": {"v": 1}, "expected_status": 201}]}


def _exec_with(fail: bool, code=200):
    """Fake ExecutionService whose one result fails (or passes) at step 1."""
    step = {"step_number": 1,
            "status": "FAIL" if fail else "PASS",
            "response_code": code,
            "error": f"Expected status 201, got {code}" if fail else None}
    ex = MagicMock()
    ex.results = [{"test_case_id": "t", "title": "t", "status": "FAIL" if fail else "PASS",
                   "steps": [step]}]
    svc = MagicMock()
    svc.execute.return_value = ex
    return svc


def _fake_anthropic(json_text: str):
    client = MagicMock()
    block = MagicMock(); block.text = json_text
    msg = MagicMock(); msg.content = [block]
    client.messages.create.return_value = msg
    return client


# ─────────────────────────────────────────────────────────────────────────────

def test_no_candidates_returns_empty():
    result = InvestigatorService(_ctx()).investigate(CriticResult(verdicts=[]), [], "http://x")
    assert isinstance(result, InvestigationResult)
    assert result.verdicts == []
    assert result.success


def test_no_base_url_is_inconclusive():
    critic = CriticResult(verdicts=[_bug("A")])
    result = InvestigatorService(_ctx()).investigate(critic, [_tc("A")], base_url=None)
    assert len(result.inconclusive) == 1
    assert result.confirmed == []


def test_flaky_failure_is_not_reproduced_without_model():
    # Fails once, passes once → not reproduced, and no model call is made.
    critic = CriticResult(verdicts=[_bug("A")])
    svc = MagicMock()
    ex_fail = MagicMock(); ex_fail.results = [{"title": "A", "status": "FAIL",
        "steps": [{"step_number": 1, "status": "FAIL", "response_code": 500, "error": "boom"}]}]
    ex_pass = MagicMock(); ex_pass.results = [{"title": "A", "status": "PASS",
        "steps": [{"step_number": 1, "status": "PASS", "response_code": 201}]}]
    svc.execute.side_effect = [ex_fail, ex_pass]
    with patch("services.execution_service.ExecutionService", return_value=svc), \
         patch("anthropic.Anthropic") as anthropic_cls:
        result = InvestigatorService(_ctx()).investigate(critic, [_tc("A")], "http://x", runs=2)
    assert len(result.not_reproduced) == 1
    anthropic_cls.assert_not_called()          # deterministic downgrade, no model spend


def test_reproduced_and_confirmed():
    critic = CriticResult(verdicts=[_bug("A")])
    svc = _exec_with(fail=True, code=200)      # both runs fail the same way
    verdict_json = json.dumps([{"title": "A", "status": "confirmed",
                                "reason": "created resource returned 200 not 201"}])
    with patch("services.execution_service.ExecutionService", return_value=svc), \
         patch("anthropic.Anthropic", return_value=_fake_anthropic(verdict_json)):
        result = InvestigatorService(_ctx()).investigate(critic, [_tc("A")], "http://x", runs=2)
    assert len(result.confirmed) == 1
    assert "1 confirmed" in result.summary()


def test_reproduced_but_downgraded_to_false_alarm():
    critic = CriticResult(verdicts=[_bug("A")])
    svc = _exec_with(fail=True, code=201)
    verdict_json = json.dumps([{"title": "A", "status": "not_reproduced",
                                "reason": "the payload was a valid email; 201 is correct"}])
    with patch("services.execution_service.ExecutionService", return_value=svc), \
         patch("anthropic.Anthropic", return_value=_fake_anthropic(verdict_json)):
        result = InvestigatorService(_ctx()).investigate(critic, [_tc("A")], "http://x", runs=2)
    assert len(result.not_reproduced) == 1
    assert result.confirmed == []


def test_bad_json_leaves_reproduced_inconclusive_not_confirmed():
    critic = CriticResult(verdicts=[_bug("A")])
    svc = _exec_with(fail=True, code=500)
    with patch("services.execution_service.ExecutionService", return_value=svc), \
         patch("anthropic.Anthropic", return_value=_fake_anthropic("not json")):
        result = InvestigatorService(_ctx()).investigate(critic, [_tc("A")], "http://x", runs=2)
    assert result.confirmed == []              # never a false confirm on parse failure
    assert len(result.inconclusive) == 1
