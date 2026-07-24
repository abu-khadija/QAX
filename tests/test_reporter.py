"""
test_reporter.py — ReporterService (group by root cause, write reports)
=======================================================================
Offline tests — the Anthropic client is mocked.

Run with:
    pytest tests/test_reporter.py -v
"""

import json
from unittest.mock import MagicMock, patch

from services.context import QAXContext
from services.reporter_service import ReporterService
from services.results import BugReportResult, InvestigationResult, InvestigationVerdict


def _ctx() -> QAXContext:
    return QAXContext(anthropic_key="k", project="")


def _confirmed(title: str) -> InvestigationVerdict:
    return InvestigationVerdict(test_case_id=title, title=title, status="confirmed",
                                reproduced=True, reason="create returned 200 not 201",
                                evidence="POST /todos expect 201; got 200")


def _investigation(titles) -> InvestigationResult:
    return InvestigationResult(verdicts=[_confirmed(t) for t in titles])


def _fake_anthropic(json_text: str):
    client = MagicMock()
    block = MagicMock(); block.text = json_text
    msg = MagicMock(); msg.content = [block]
    client.messages.create.return_value = msg
    return client


# ─────────────────────────────────────────────────────────────────────────────

def test_no_confirmed_returns_empty():
    result = ReporterService(_ctx()).report(InvestigationResult(verdicts=[]))
    assert isinstance(result, BugReportResult)
    assert result.entries == []
    assert result.success


def test_groups_many_tests_into_one_family():
    inv = _investigation(["test A", "test B", "test C"])
    reply = json.dumps([{
        "title": "[Todos] create returns 200 instead of 201",
        "severity": "P3", "severity_reason": "contract violation, create still works",
        "description": "A valid create returns 200 not 201.",
        "steps_to_reproduce": ["register", "login", "POST /todos"],
        "expected": "201 Created", "actual": "200 OK",
        "root_cause": "wrong status_code on the route decorator",
        "impact": "clients checking for 201 break",
        "occurrences": ["test A", "test B", "test C"],
    }])
    with patch("anthropic.Anthropic", return_value=_fake_anthropic(reply)):
        result = ReporterService(_ctx()).report(inv)
    assert len(result.entries) == 1                 # 3 tests → 1 bug
    assert result.occurrence_count == 3
    assert "1 bug from 3 failing test(s)" == result.summary()
    assert result.entries[0].severity == "P3"


def test_uncovered_confirmed_bug_is_not_dropped():
    inv = _investigation(["covered", "orphan"])
    reply = json.dumps([{
        "title": "[X] covered bug", "severity": "P2",
        "occurrences": ["covered"],  # 'orphan' left out by the model
    }])
    with patch("anthropic.Anthropic", return_value=_fake_anthropic(reply)):
        result = ReporterService(_ctx()).report(inv)
    covered = {o for e in result.entries for o in e.occurrences}
    assert "orphan" in covered                      # backfilled, nothing dropped
    assert result.occurrence_count == 2


def test_bad_severity_defaults_p3_and_markdown_renders():
    inv = _investigation(["t1"])
    reply = json.dumps([{"title": "[Y] bug", "severity": "BOGUS",
                         "occurrences": ["t1"], "expected": "x", "actual": "y"}])
    with patch("anthropic.Anthropic", return_value=_fake_anthropic(reply)):
        result = ReporterService(_ctx()).report(inv)
    assert result.entries[0].severity == "P3"
    md = result.to_markdown()
    assert "[Y] bug" in md and "Expected" in md


def test_bad_json_captured_via_backfill_not_crash():
    inv = _investigation(["t1"])
    with patch("anthropic.Anthropic", return_value=_fake_anthropic("not json")):
        result = ReporterService(_ctx()).report(inv)
    # No families parsed → the confirmed bug is backfilled, not lost.
    assert result.success
    assert result.occurrence_count == 1
