"""
test_detectors.py — DetectorService (knowledge base + differential reasoning)
=============================================================================
Offline tests. The deterministic detectors are exercised directly on AppModels;
the knowledge-guided pass is mocked.

Run with:
    pytest tests/test_detectors.py -v
"""

import json
from unittest.mock import MagicMock, patch

from services.context import QAXContext
from services.detector_service import DetectorService
from services.results import AppModel, DetectionResult


def _ctx() -> QAXContext:
    return QAXContext(anthropic_key="k", project="")


def _fake_anthropic(json_text: str):
    client = MagicMock()
    block = MagicMock(); block.text = json_text
    msg = MagicMock(); msg.content = [block]
    client.messages.create.return_value = msg
    return client


def _model() -> AppModel:
    return AppModel(
        root="/x",
        config=[{"key": "jwt_secret", "default": "change-this", "is_secret": True,
                 "risk": "anyone can forge tokens"}],
        entities=[
            {"name": "TodoCreate", "fields": [
                {"name": "title", "type": "str", "constraints": ""},
                {"name": "description", "type": "str", "constraints": ""},
            ]},
            {"name": "CategoryCreate", "fields": [
                {"name": "name", "type": "str", "constraints": "min_length=1,max_length=100"},
            ]},
        ],
        contracts=[{"frontend_call": "GET /todos", "backend_endpoint": "GET /todos",
                    "mismatch": "default page size differs (10 vs 20)"}],
    )


# ── deterministic detectors (no model call) ──────────────────────────────────

def test_hardcoded_secret_detected_static():
    result = DetectorService(_ctx()).detect(_model(), use_knowledge_pass=False)
    secrets = [f for f in result.findings if f.pattern == "hardcoded-secret"]
    assert len(secrets) == 1
    assert secrets[0].severity == "P1"
    assert secrets[0].source == "static"


def test_missing_constraint_differential_flags_title():
    result = DetectorService(_ctx()).detect(_model(), use_knowledge_pass=False)
    miss = [f for f in result.findings if f.pattern == "missing-input-validation"]
    wheres = {f.where for f in miss}
    assert "TodoCreate.title" in wheres          # peer 'name' is constrained, title is not
    # 'title' is an identifying field → medium confidence, not low
    title_f = next(f for f in miss if f.where == "TodoCreate.title")
    assert title_f.confidence == "medium"


def test_no_differential_when_no_peer_has_constraint():
    m = AppModel(root="/x", entities=[{"name": "TodoCreate", "fields": [
        {"name": "title", "type": "str", "constraints": ""}]}])
    result = DetectorService(_ctx()).detect(m, use_knowledge_pass=False)
    assert [f for f in result.findings if f.pattern == "missing-input-validation"] == []


def test_contract_mismatch_detected_static():
    result = DetectorService(_ctx()).detect(_model(), use_knowledge_pass=False)
    assert any(f.pattern == "contract-mismatch" for f in result.findings)


# ── knowledge pass + integration ─────────────────────────────────────────────

def test_knowledge_pass_adds_semantic_findings():
    reply = json.dumps([
        {"pattern": "weak-crypto-limit", "title": "password 128 > bcrypt 72",
         "severity": "P2", "where": "UserCreate.password",
         "evidence": "schema max_length 128, bcrypt limit 72", "confidence": "high"},
        {"pattern": "computed-value-offbyone", "title": "total_pages floor not ceil",
         "severity": "P2", "where": "GET /todos", "evidence": "total // page_size",
         "confidence": "high"},
    ])
    with patch("anthropic.Anthropic", return_value=_fake_anthropic(reply)):
        result = DetectorService(_ctx()).detect(_model(), use_knowledge_pass=True)
    patterns = {f.pattern for f in result.findings}
    assert "weak-crypto-limit" in patterns
    assert "computed-value-offbyone" in patterns
    assert "hardcoded-secret" in patterns        # static + knowledge combined
    # sorted most-severe first
    assert result.findings[0].severity == "P1"


def test_knowledge_pass_failure_keeps_static_findings():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("api down")
    with patch("anthropic.Anthropic", return_value=client):
        result = DetectorService(_ctx()).detect(_model(), use_knowledge_pass=True)
    assert result.success                        # not an outright failure
    assert any(f.pattern == "hardcoded-secret" for f in result.findings)


def test_no_model_is_error():
    result = DetectorService(_ctx()).detect(AppModel(error="bad"))
    assert not result.success
