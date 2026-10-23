"""
test_phase2_services.py — Unit Tests for Phase 2 Services
==========================================================
Covers the CSV adapter and the ExecutionService facade. Everything runs offline: no Jira, no browser, no
network. External runners are patched; the CSV path uses a temp directory.

Run with:
    pytest tests/test_phase2_services.py -v
"""

import csv
from pathlib import Path
from unittest.mock import patch

from core.adapters import get_adapter
from services.context import QAXContext
from services.execution_service import ExecutionService
from services.results import ExecutionResult


def _tc(**over) -> dict:
    """Minimal test case dict (export/report don't run schema validation)."""
    base = {
        "test_case_id": "TC-1", "title": "Login works", "module": "Auth",
        "test_type": "functional", "priority": "high", "confidence_score": 9,
        "preconditions": ["User exists"],
        "manual_steps": [{"step_number": 1, "action": "Enter creds",
                          "expected_result": "Logged in"}],
        "tags": ["smoke"],
    }
    base.update(over)
    return base


def _ctx(**over) -> QAXContext:
    return QAXContext(anthropic_key="k", project="demo", dry_run=True, **over)


# ─────────────────────────────────────────────────────────────────────────────
# CSV adapter
# ─────────────────────────────────────────────────────────────────────────────

def test_csv_adapter_is_registered():
    """CSV is wired into the adapter registry."""
    assert get_adapter("csv").name == "csv"


def test_csv_adapter_writes_valid_file(tmp_path, monkeypatch):
    """A real (non-dry) CSV export produces a parseable file with a header."""
    monkeypatch.chdir(tmp_path)  # output/ is created relative to cwd
    result = get_adapter("csv").export([_tc()], project="demo", dry_run=False)
    assert result["success"] is True
    path = Path(result["references"][0])
    assert path.exists()
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert rows[0][0] == "ID"
    assert rows[1][0] == "TC-1"


# ─────────────────────────────────────────────────────────────────────────────
# ExecutionService
# ─────────────────────────────────────────────────────────────────────────────

def test_execution_mode_none_runs_nothing():
    """mode='none' returns an empty, successful result without touching runners."""
    result = ExecutionService(_ctx()).execute([_tc()], mode="none")
    assert isinstance(result, ExecutionResult)
    assert result.mode == "none"
    assert result.total == 0
    assert result.success is True


def test_execution_empty_cases():
    """No test cases yields an empty result, not a crash."""
    result = ExecutionService(_ctx()).execute([], mode="api")
    assert result.total == 0
    assert result.success is True


def test_execution_api_mode_uses_runner():
    """API mode drives APIRunner and wraps its batch result."""
    fake_batch = {"total": 1, "passed": 1, "failed": 0, "errors": 0,
                  "pass_rate": 100.0, "results": [{"test_case_id": "TC-1",
                                                    "status": "PASS"}]}
    with patch("execution.api_runner.APIRunner") as MockRunner:
        MockRunner.return_value.execute_batch.return_value = fake_batch
        result = ExecutionService(_ctx()).execute(
            [_tc()], mode="api", base_url="https://staging.example.com"
        )
        MockRunner.return_value.setup.assert_called_once()
        MockRunner.return_value.teardown.assert_called_once()

    assert result.mode == "api"
    assert result.passed == 1
    assert result.pass_rate == 100.0


def test_execution_ui_without_playwright_is_captured():
    """When Playwright is unavailable, UI mode returns an error result, no crash."""
    with patch("execution.playwright_runner.PLAYWRIGHT_AVAILABLE", False):
        result = ExecutionService(_ctx()).execute([_tc()], mode="ui")
    assert result.mode == "ui"
    assert result.success is False
    assert "Playwright" in (result.error or "")
