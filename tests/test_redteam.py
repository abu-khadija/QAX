"""
test_redteam.py — RedTeamService + named-token auth
===================================================
Offline tests. The model call is mocked; the named-token runner change is checked
against the real APIRunner with a stubbed session.

Run with:
    pytest tests/test_redteam.py -v
"""

import json
from unittest.mock import MagicMock, patch

from execution.api_runner import APIRunner
from services.context import QAXContext
from services.redteam_service import RedTeamService
from services.results import AppModel, DetectionResult, Finding, GenerationResult


def _ctx() -> QAXContext:
    return QAXContext(anthropic_key="k", project="")


def _fake_anthropic(json_text: str):
    client = MagicMock()
    block = MagicMock(); block.text = json_text
    msg = MagicMock(); msg.content = [block]
    client.messages.create.return_value = msg
    return client


def _model() -> AppModel:
    return AppModel(root="/x", endpoints=[{"method": "GET", "path": "/todos/{id}",
                    "auth_required": True}], auth={"scheme": "JWT"})


# ── named-token auth in the runner ───────────────────────────────────────────

def test_named_token_auth_header():
    runner = APIRunner()
    runner.base_url = "http://x"
    sent = {}

    class FakeResp:
        status_code = 200
        text = "{}"
        def json(self): return {}

    class FakeSession:
        def request(self, method, url, json=None, headers=None, timeout=None):
            sent["headers"] = headers or {}
            return FakeResp()
    runner.session = FakeSession()

    variables = {"token": "AAA", "token_b": "BBB"}
    # use_auth: "token_b" must send userB's token, not the default one.
    step = {"step_number": 1, "method": "GET", "path": "/todos/1",
            "use_auth": "token_b", "expected_status": 200}
    runner.execute_api_step(step, variables)
    assert sent["headers"].get("Authorization") == "Bearer BBB"

    # use_auth: true still uses the default "token".
    runner.execute_api_step({"step_number": 2, "method": "GET", "path": "/x",
                             "use_auth": True, "expected_status": 200}, variables)
    assert sent["headers"].get("Authorization") == "Bearer AAA"


# ── RedTeamService ───────────────────────────────────────────────────────────

def test_no_model_is_error():
    result = RedTeamService(_ctx()).generate(AppModel(error="bad"))
    assert not result.success


def test_generates_attack_cases():
    reply = json.dumps([
        {"title": "Attack: userB reads userA's todo (IDOR)", "attack": "broken-object-authz",
         "api_steps": [{"step_number": 1, "method": "GET", "path": "/todos/{{rid}}",
                        "use_auth": "token_b", "expected_status": 404}]},
        {"title": "Attack: forge a token", "attack": "forge-jwt",
         "note": "sign a JWT with the leaked secret"},
    ])
    det = DetectionResult(findings=[Finding(pattern="broken-object-authz", title="IDOR?",
                                            severity="P1", where="GET /todos/{id}")])
    with patch("anthropic.Anthropic", return_value=_fake_anthropic(reply)):
        result = RedTeamService(_ctx()).generate(_model(), det)
    assert isinstance(result, GenerationResult)
    assert result.count == 2
    assert result.test_cases[0]["attack"] == "broken-object-authz"
    assert result.test_cases[0]["module"] == "red-team"
    # the forge-jwt intent is preserved for the probe phase
    assert any(tc["attack"] == "forge-jwt" for tc in result.test_cases)
    # ids and type are backfilled
    assert all(tc.get("test_case_id") and tc.get("test_type") for tc in result.test_cases)


def test_bad_json_yields_no_cases_not_crash():
    with patch("anthropic.Anthropic", return_value=_fake_anthropic("not json")):
        result = RedTeamService(_ctx()).generate(_model(), None)
    assert result.success
    assert result.count == 0
