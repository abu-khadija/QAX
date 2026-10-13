"""
services/reporting_service.py — ReportingService (Phase 2)
===========================================================
Turns execution results into a shareable report. The primary output is a
self-contained HTML report (the "bug report" the user asked for that does NOT
require Jira) — it lists every test case with pass/fail status, durations, and
inline failure details.

This is deliberately Jira-independent: the freelancing flow produces a file
you can hand a client. Pushing failures into Jira as bug tickets remains an
optional extra (via the existing BugReporter) and can be layered on later.

Wraps execution/results_reporter.ResultsReporter. Never prints business
errors, never calls sys.exit(); failures are captured in ReportResult.error.

Usage:
    from services.reporting_service import ReportingService

    svc = ReportingService()
    report = svc.report(execution_result)     # accepts an ExecutionResult
    print(report.html_path)                    # -> "output/reports/execution_report_....html"
"""

from __future__ import annotations

import logging

from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import ExecutionResult, ReportResult

logger = logging.getLogger(__name__)


class ReportingService:
    """
    Produce a standalone HTML report from execution results.

    Accepts either an ExecutionResult (preferred) or the raw batch dict the
    execution engine returns, so it composes naturally after ExecutionService.
    """

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Configuration. Built from the
                environment when None (single-tenant mode).
        """
        self.context = context or QAXContext.from_env()

    def report(
        self,
        execution: ExecutionResult | dict,
        *,
        output_folder: str = "output/reports",
        progress: ProgressReporter | None = None,
    ) -> ReportResult:
        """
        Generate a self-contained HTML report from execution results.

        Args:
            execution (ExecutionResult | dict): The result from
                ExecutionService.execute(), or a raw batch-result dict.
            output_folder (str): Directory for the HTML file. Created if absent.
            progress (ProgressReporter | None): Progress sink.

        Returns:
            ReportResult: With html_path set on success. Never raises —
                failures are captured in .error.
        """
        prog = progress or NullProgressReporter()
        batch = self._as_batch_dict(execution)

        if not batch or not batch.get("results"):
            prog.emit("report", "warn", "no execution results to report")
            return ReportResult(error="No execution results to report")

        try:
            from execution.results_reporter import ResultsReporter

            prog.emit("report", "start", "generating HTML report")
            reporter = ResultsReporter()
            html_path = reporter.generate_html_report(batch, output_folder=output_folder)

            if not html_path:
                prog.emit("report", "warn", "report generation returned no path")
                return ReportResult(error="Report generation produced no file")

            prog.emit("report", "ok", html_path)
            return ReportResult(html_path=html_path)

        except Exception as exc:  # never crash a face
            logger.error("ReportingService failed: %s", exc)
            prog.emit("report", "error", str(exc))
            return ReportResult(error=str(exc))

    @staticmethod
    def _as_batch_dict(execution: ExecutionResult | dict) -> dict:
        """
        Normalise the input to the batch-result dict ResultsReporter expects.

        Args:
            execution (ExecutionResult | dict): Execution output.

        Returns:
            dict: The raw batch-result dict.
        """
        if isinstance(execution, ExecutionResult):
            return execution.to_batch_dict()
        if isinstance(execution, dict):
            return execution
        return {}
