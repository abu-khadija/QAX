"""
test_claude_parser.py — Robustness tests for ClaudeClient response parsing
===========================================================================
These tests lock in the fix for the real bug found in live testing: a
truncated Claude response (model hit max_tokens mid-array) caused the parser
to return inner ``manual_steps`` objects instead of test cases, and that
garbage then poisoned the cache.

No API key or network is needed — the parser is exercised directly on crafted
strings. ClaudeClient is instantiated via __new__ so __init__ (which needs an
API key and the system-prompt file) is skipped; the parser methods don't touch
any instance state.

Run with:
    pytest tests/test_claude_parser.py -v
"""

import json

from core.claude_client import ClaudeClient


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _parser() -> ClaudeClient:
    """A ClaudeClient with the parser available but __init__ skipped."""
    return ClaudeClient.__new__(ClaudeClient)


def tc(i: int = 1) -> dict:
    """A test-case-shaped dict."""
    return {
        "test_case_id": f"TC-{i}",
        "title": f"Verify behaviour {i}",
        "manual_steps": [
            {"step_number": 1, "action": "do x", "expected_result": "y", "notes": ""},
            {"step_number": 2, "action": "do z", "expected_result": "w", "notes": ""},
        ],
        "automation_steps": [],
        "confidence_score": 9,
    }


def step() -> dict:
    """A bare step-shaped dict (the thing that leaked as a 'test case')."""
    return {"step_number": 1, "action": "click", "expected_result": "ok", "notes": ""}


# ─────────────────────────────────────────────────────────────────────────────
# Shape guards
# ─────────────────────────────────────────────────────────────────────────────

def test_looks_like_test_case():
    assert ClaudeClient._looks_like_test_case(tc()) is True
    assert ClaudeClient._looks_like_test_case(step()) is False
    assert ClaudeClient._looks_like_test_case("not a dict") is False
    assert ClaudeClient._looks_like_test_case({}) is False
    assert ClaudeClient._looks_like_test_case(None) is False


def test_accept_test_cases():
    assert ClaudeClient._accept_test_cases([tc(1), tc(2)]) == [tc(1), tc(2)]
    # a pure steps array is NOT a test-case list
    assert ClaudeClient._accept_test_cases([step(), step()]) is None
    assert ClaudeClient._accept_test_cases([]) is None
    # strays are dropped, real ones kept
    assert ClaudeClient._accept_test_cases([tc(1), step()]) == [tc(1)]


# ─────────────────────────────────────────────────────────────────────────────
# _parse_response — happy paths
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_plain_array():
    out = _parser()._parse_response(json.dumps([tc(1), tc(2)]))
    assert out is not None and len(out) == 2


def test_parse_markdown_fenced():
    text = "```json\n" + json.dumps([tc(1)]) + "\n```"
    out = _parser()._parse_response(text)
    assert out is not None and len(out) == 1


def test_parse_with_preamble_text():
    text = "Here are the test cases:\n" + json.dumps([tc(1), tc(2)]) + "\nDone."
    out = _parser()._parse_response(text)
    assert out is not None and len(out) == 2


def test_parse_unwraps_dict_wrapper():
    text = json.dumps({"test_cases": [tc(1)]})
    out = _parser()._parse_response(text)
    assert out is not None and len(out) == 1


# ─────────────────────────────────────────────────────────────────────────────
# _parse_response — the bug: steps must never be returned as test cases
# ─────────────────────────────────────────────────────────────────────────────

def test_pure_steps_array_is_rejected():
    """A response that is just a steps array must NOT be accepted."""
    out = _parser()._parse_response(json.dumps([step(), step()]))
    assert out is None


def test_truncated_array_salvages_complete_cases():
    """Two complete cases + an incomplete third (no closing) → keep the two."""
    truncated = (
        "[" + json.dumps(tc(1)) + "," + json.dumps(tc(2)) + ","
        + '{"test_case_id": "TC-3", "title": "Verify incompl'
    )
    out = _parser()._parse_response(truncated)
    assert out is not None and len(out) == 2
    assert all("title" in o for o in out)


def test_truncation_never_returns_inner_steps():
    """
    The exact production failure: the outer array is truncated so the last
    complete ']' belongs to an inner manual_steps array. We must recover the
    complete test case(s) and never return step objects.
    """
    truncated = (
        "[" + json.dumps(tc(1)) + ","
        + '{"test_case_id":"TC-2","title":"x","manual_steps":'
        + '[{"step_number":1,"action":"a","expected_result":"b"}'
    )
    out = _parser()._parse_response(truncated)
    assert out is not None
    assert all(ClaudeClient._looks_like_test_case(o) for o in out)
    assert all("step_number" not in o for o in out)          # no bare steps
    assert any(o.get("test_case_id") == "TC-1" for o in out)  # the complete one


# ─────────────────────────────────────────────────────────────────────────────
# Low-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_outer_array_is_string_aware():
    text = '[{"title":"a ] bracket in a string","test_case_id":"TC-1"}]'
    arr = ClaudeClient._extract_outer_array(text)
    assert arr == text
    assert json.loads(arr)[0]["test_case_id"] == "TC-1"


def test_extract_outer_array_truncated_returns_none():
    assert ClaudeClient._extract_outer_array('[{"title":"a"') is None
    assert ClaudeClient._extract_outer_array("no array here") is None


def test_salvage_skips_nested_steps_and_incomplete():
    text = json.dumps([tc(1)])           # nested manual_steps must not be extracted
    objs = ClaudeClient._salvage_objects(text)
    assert len(objs) == 1
    assert objs[0]["test_case_id"] == "TC-1"
    # a bare steps blob salvages nothing
    assert ClaudeClient._salvage_objects(json.dumps([step(), step()])) == []
