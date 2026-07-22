"""
test_phase2_services.py — Unit Tests for Phase 2 Services
==========================================================
Covers the CSV adapter and the ExportService / ExecutionService /
ReportingService facades. Everything runs offline: no Jira, no browser, no
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
from services.export_service import ExportService
from services.reporting_service import ReportingService
from services.results import ExecutionResult, ExportResult, ReportResult


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
# ExportService
# ─────────────────────────────────────────────────────────────────────────────

def test_export_service_csv_dry_run():
    """Dry-run CSV export succeeds and returns a target path without writing."""
    result = ExportService(_ctx()).export([_tc()], target="csv")
    assert isinstance(result, ExportResult)
    assert result.success is True
    assert result.target == "csv"
    assert result.references and result.references[0].endswith(".csv")


def test_export_service_empty_cases():
    """Exporting nothing is a captured failure, not a crash."""
    result = ExportService(_ctx()).export([], target="csv")
    assert result.success is False
    assert result.error


def test_export_service_none_target_is_safe():
    """The 'none' target (memory only) never fails on valid input."""
    result = ExportService(_ctx()).export([_tc()], target="none")
    assert isinstance(result, ExportResult)
    assert result.target == "none"


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


# ─────────────────────────────────────────────────────────────────────────────
# ReportingService
# ─────────────────────────────────────────────────────────────────────────────

def test_reporting_generates_html(tmp_path):
    """ReportingService builds an HTML report from an ExecutionResult."""
    batch = {"total": 1, "passed": 0, "failed": 1, "errors": 0, "pass_rate": 0.0,
             "results": [{"test_case_id": "TC-1", "status": "FAIL",
                          "title": "Login works"}]}
    exec_result = ExecutionResult(mode="ui", raw=batch)
    fake_path = str(tmp_path / "report.html")

    with patch("execution.results_reporter.ResultsReporter") as MockReporter:
        MockReporter.return_value.generate_html_report.return_value = fake_path
        report = ReportingService(_ctx()).report(exec_result, output_folder=str(tmp_path))

    assert isinstance(report, ReportResult)
    assert report.success is True
    assert report.html_path == fake_path


def test_reporting_empty_results_is_captured():
    """No results to report is a captured error, not a crash."""
    report = ReportingService(_ctx()).report(ExecutionResult(mode="ui", raw={}))
    assert report.success is False
    assert report.error
