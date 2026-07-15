"""
core/adapters/memory_adapter.py — Memory (No-Op) Export Adapter
================================================================
Skips export entirely — test cases remain in memory for the pipeline.

Use this adapter when:
  - You only want to run Playwright execution without pushing to any system
  - You are testing the generation phase in isolation
  - You explicitly pass --export none on the CLI

The adapter always succeeds and returns an empty references list so the
pipeline can proceed to execution without any external dependencies.

Usage:
    adapter = MemoryAdapter()
    result  = adapter.export(test_cases)   # always succeeds
"""

import logging

from core.adapters.base import BaseExportAdapter

logger = logging.getLogger(__name__)


class MemoryAdapter(BaseExportAdapter):
    """
    No-op export adapter that holds test cases in memory only.

    Always returns success so downstream pipeline phases (execution) are
    never blocked by an export step. Use with --export none on the CLI.
    """

    name = "none"

    def validate_config(self) -> tuple[bool, str]:
        """
        No credentials required — always valid.

        Returns:
            tuple[bool, str]: (True, "") unconditionally.
        """
        return True, ""

    def export(
        self,
        test_cases: list[dict],
        project: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        """
        Log the count and return success without writing anything.

        Args:
            test_cases (list[dict]): Test cases from the generation phase.
            project (str | None): Unused.
            dry_run (bool): Unused — this adapter is always a no-op.

        Returns:
            dict: Standard result dict with success=True, empty references.
        """
        count = len(test_cases)
        logger.info(
            "QAX MemoryAdapter: %d test case(s) held in memory — export skipped",
            count,
        )
        print(f"  [MemoryAdapter] {count} test case(s) held in memory — skipping export")
        return {
            "success":        True,
            "exported_count": count,
            "failed_count":   0,
            "references":     [],
            "error":          None,
        }
