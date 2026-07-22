"""
test_generation_service.py — Unit Tests for the GenerationService (Phase 1)
============================================================================
Verifies the extracted service layer without hitting the Anthropic API.

The Claude call and the SmartChecker are patched so the tests run offline and
deterministically. They confirm the three guarantees of a service:
  * it returns a typed GenerationResult (never prints / exits),
  * runtime failures land in result.error instead of crashing,
  * a missing API key is the one thing that raises (a config problem).

Run with:
    pytest tests/test_generation_service.py -v
"""

from unittest.mock import patch

import pytest

from services.context import QAXContext
from services.exceptions import QAXConfigError
from services.generation_service import GenerationOptions, GenerationService
from services.progress import ProgressReporter
from services.results import GenerationResult


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_valid_test_case(**overrides) -> dict:
    """Build a test case that passes schema validation (mirrors test_validator)."""
    base = {
        "test_case_id": "TC-INC-0001",
        "title": "Verify incident cannot be submitted without mandatory fields",
        "description": "Ensure the system prevents submission when fields are empty.",
        "module": "Orders",
        "test_type": "functional",
        "priority": "high",
        "preconditions": ["User is logged in", "User is on the New Incident form"],
        "manual_steps": [
            {"step_number": 1, "action": "Click Submit with empty fields",
             "expected_result": "Validation error appears.", "notes": ""},
            {"step_number": 2, "action": "Fill all fields and click Submit",
             "expected_result": "Incident is created.", "notes": ""},
        ],
        "automation_steps": [
            {"step_number": 1, "playwright_action": "click",
             "selector_key": "TODO_submit_button", "value": "",
             "assertion_type": "visible", "assertion_value": ""},
            {"step_number": 2, "playwright_action": "fill",
             "selector_key": "TODO_title_field", "value": "Spill",
             "assertion_type": "visible", "assertion_value": ""},
        ],
        "test_data": {"incident_title": "Chemical spill"},
        "confidence_score": 8,
        "needs_review": False,
        "review_note": "",
        "generated_from_file": "test_file.cs",
        "generated_at": "2025-01-15T10:30:00Z",
    }
    base.update(overrides)
    return base


class _FakeSmartChecker:
    """Stand-in for SmartChecker that returns a controllable pre-check dict."""

    def __init__(self, pre: dict) -> None:
        self._pre = pre

    def __call__(self, *args, **kwargs) -> "_FakeSmartChecker":
        # Allow SmartChecker(project=...) construction to return this instance.
        return self

    def run_pre_checks(self, **kwargs) -> dict:
        return self._pre


def _default_pre(cache_hit: bool = False, cached=None) -> dict:
    """A pre-check dict shaped like the real SmartChecker.run_pre_checks output."""
    return {
        "cache_hit": cache_hit,
        "cached_result": cached,
        "risk": {"risk_level": "low"},
        "test_type": "feature",
        "skill_context": "",
        "existing_tests": [],
        "recommended_model": "claude-haiku-4-5-20251001",
        "pre_check_summary": "Risk: LOW",
    }


def _ctx() -> QAXContext:
    """A context with a fake key so the config guard passes."""
    return QAXContext(anthropic_key="fake-key", project="")


# ─────────────────────────────────────────────────────────────────────────────
# Config guard
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_api_key_raises():
    """A missing Anthropic key is a config problem — it must raise."""
    with pytest.raises(QAXConfigError):
        GenerationService(QAXContext(anthropic_key="")).generate("x.py")


# ─────────────────────────────────────────────────────────────────────────────
# Failure capture (never crash)
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_file_is_captured_not_raised():
    """A missing source file returns an error result, it does not crash."""
    result = GenerationService(_ctx()).generate("nope_does_not_exist.py")
    assert isinstance(result, GenerationResult)
    assert result.success is False
    assert result.error
    assert result.count == 0


def test_empty_content_returns_zero_cases():
    """Empty content short-circuits before any API call."""
    result = GenerationService(_ctx()).generate(
        "story://x", options=GenerationOptions(content="   ")
    )
    assert result.success is True
    assert result.count == 0
    assert result.summary["total"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Full generation flow (mocked Claude)
# ─────────────────────────────────────────────────────────────────────────────

def test_generate_from_content_happy_path():
    """A normal generation returns validated cases and a summary."""
    raw_cases = [make_valid_test_case(), make_valid_test_case(test_case_id="TC-INC-0002")]

    with patch("core.smart_checker.SmartChecker", _FakeSmartChecker(_default_pre())), \
         patch("core.claude_client.ClaudeClient") as MockClient:
        MockClient.return_value.analyze_code.return_value = raw_cases

        progress = ProgressReporter()
        result = GenerationService(_ctx()).generate(
            "story://feature",
            options=GenerationOptions(content="def submit(): pass", module="Orders"),
            progress=progress,
        )

    assert result.success is True
    assert result.count == 2
    assert result.from_cache is False
    assert result.summary["valid_count"] == 2
    assert result.avg_confidence == 8
    # progress was emitted (not printed) — the service recorded events
    assert any(e.step == "generate" for e in progress.events)


def test_cache_hit_skips_api():
    """When SmartChecker reports a cache hit, no API call is made."""
    cached = [make_valid_test_case()]
    pre = _default_pre(cache_hit=True, cached=cached)

    with patch("core.smart_checker.SmartChecker", _FakeSmartChecker(pre)), \
         patch("core.claude_client.ClaudeClient") as MockClient:
        result = GenerationService(_ctx()).generate(
            "story://cached",
            options=GenerationOptions(content="def x(): pass"),
        )
        MockClient.return_value.analyze_code.assert_not_called()

    assert result.from_cache is True
    assert result.model == "cache"
    assert result.count == 1
    assert result.summary["total"] == 1


def test_cache_hit_with_garbage_regenerates():
    """
    A poisoned cache (cases that fail schema validation) must NOT be returned.
    The service re-validates cached cases, finds none valid, and regenerates —
    self-healing the cache entry.
    """
    garbage = [{"step_number": 1, "action": "click", "expected_result": "ok"}]
    pre = _default_pre(cache_hit=True, cached=garbage)
    good = [make_valid_test_case()]

    with patch("core.smart_checker.SmartChecker", _FakeSmartChecker(pre)), \
         patch("core.claude_client.ClaudeClient") as MockClient:
        MockClient.return_value.analyze_code.return_value = good
        result = GenerationService(_ctx()).generate(
            "story://poisoned",
            options=GenerationOptions(content="def x(): pass"),
        )
        MockClient.return_value.analyze_code.assert_called_once()

    assert result.from_cache is False   # cache was rejected
    assert result.count == 1            # regenerated a valid case


def test_repo_context_forwarded_to_claude():
    """The comprehension app-map (repo_context) is passed into analyze_code."""
    raw = [make_valid_test_case()]
    with patch("core.smart_checker.SmartChecker", _FakeSmartChecker(_default_pre())), \
         patch("core.claude_client.ClaudeClient") as MockClient:
        MockClient.return_value.analyze_code.return_value = raw
        GenerationService(_ctx()).generate(
            "story://x",
            options=GenerationOptions(content="def x(): pass",
                                      repo_context="App overview: a login module"),
        )
        _, kwargs = MockClient.return_value.analyze_code.call_args
        assert kwargs.get("repo_context") == "App overview: a login module"


def test_to_dict_is_json_shaped():
    """to_dict() exposes the fields an MCP/SaaS face needs as plain JSON."""
    result = GenerationResult(test_cases=[make_valid_test_case()], source="a.py",
                              summary={"avg_confidence": 8})
    data = result.to_dict()
    assert data["count"] == 1
    assert data["success"] is True
    assert data["source"] == "a.py"
    assert isinstance(data["test_cases"], list)
