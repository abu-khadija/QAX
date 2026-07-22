"""
services/export_service.py — ExportService (Phase 2)
=====================================================
Sends generated test cases to a target of the user's choice — Jira/Xray,
Zephyr, Excel, CSV, or nowhere ("none"). It wraps the existing output-adapter
layer so every face (CLI, UI, MCP, SaaS) exports the same way.

Key point for the product vision: the *user* picks the target. This service
makes that a single parameter — no target is privileged over another. Jira is
just one option among several, exactly as intended.

Never prints business errors, never calls sys.exit(). Export failures are
captured in ExportResult.error; they never crash a face and (per the pipeline
contract) never block a later execution phase.

Usage:
    from services import QAXContext
    from services.export_service import ExportService

    svc = ExportService(QAXContext.from_env(project="example"))
    result = svc.export(test_cases, target="csv")
    print(result.references)   # -> ["output/test_cases_example_....csv"]
"""

from __future__ import annotations

import logging

from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import ExportResult

logger = logging.getLogger(__name__)

# Targets the ExportService understands. Unknown targets fall back to "none"
# (memory only) inside get_adapter, so a typo never crashes the pipeline.
KNOWN_TARGETS = ("xray", "zephyr", "excel", "csv", "none")


class ExportService:
    """
    Export validated test cases to a chosen target via the adapter layer.

    Wraps core.adapters.get_adapter(). dry_run and project come from the
    QAXContext, so the caller never manages a global DRY_RUN env var.
    """

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Configuration and credentials. Built
                from the environment when None (single-tenant mode).
        """
        self.context = context or QAXContext.from_env()

    def export(
        self,
        test_cases: list[dict],
        *,
        target: str = "none",
        progress: ProgressReporter | None = None,
    ) -> ExportResult:
        """
        Export test cases to the given target.

        Args:
            test_cases (list[dict]): Validated test cases to export.
            target (str): One of KNOWN_TARGETS. Unknown values fall back to
                "none" (memory only) rather than erroring.
            progress (ProgressReporter | None): Progress sink.

        Returns:
            ExportResult: Outcome with references (keys/paths) and any error.
                Never raises — failures are captured in .error.
        """
        prog = progress or NullProgressReporter()
        target_name = (target or "none").lower()

        if not test_cases:
            prog.emit("export", "warn", "no test cases to export")
            return ExportResult(target=target_name, success=False,
                                error="No test cases provided")

        try:
            from core.adapters import get_adapter

            adapter = get_adapter(target_name)

            # Fail fast with an actionable message when the target is
            # configured incorrectly (e.g. missing Xray credentials).
            ok, err = adapter.validate_config()
            if not ok:
                prog.emit("export", "error", err)
                return ExportResult(
                    target=target_name,
                    success=False,
                    failed_count=len(test_cases),
                    error=err,
                )

            prog.emit("export", "start", f"exporting {len(test_cases)} case(s) → {target_name}")
            raw = adapter.export(
                test_cases,
                project=self.context.project,
                dry_run=self.context.dry_run,
            )

            result = ExportResult(
                target=target_name,
                success=bool(raw.get("success")),
                exported_count=int(raw.get("exported_count", 0)),
                failed_count=int(raw.get("failed_count", 0)),
                references=list(raw.get("references", [])),
                error=raw.get("error"),
            )
            status = "ok" if result.success else "warn"
            prog.emit("export", status,
                      f"{result.exported_count} exported, {result.failed_count} failed")
            return result

        except Exception as exc:  # never crash a face
            logger.error("ExportService failed for target '%s': %s", target_name, exc)
            prog.emit("export", "error", str(exc))
            return ExportResult(
                target=target_name,
                success=False,
                failed_count=len(test_cases),
                error=str(exc),
            )
