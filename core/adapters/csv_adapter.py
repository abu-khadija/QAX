"""
core/adapters/csv_adapter.py — CSV Export Adapter
==================================================
Writes test cases to a plain .csv file using Python's built-in csv module.
No third-party dependency required (unlike the Excel adapter's openpyxl).

CSV is the most portable export: it opens in Excel, Google Sheets, Numbers,
and imports into most test-management tools. This is the lightweight default
for the "give me the test cases as a file" flow.

Output path:
    output/test_cases_{project}_{YYYYMMDD_HHMMSS}.csv

Usage:
    adapter = CSVAdapter()
    result  = adapter.export(test_cases, project="myapp", dry_run=True)
"""

import csv
import logging
import os
from datetime import datetime
from pathlib import Path

from core.adapters.base import BaseExportAdapter

logger = logging.getLogger(__name__)

# ── Column definitions: (header_label, extractor(tc, manual_steps)) ───────────
# Mirrors the Excel adapter's columns so both exports look the same.
_COLUMNS = [
    ("ID",              lambda tc, _: tc.get("test_case_id", "")),
    ("Title",           lambda tc, _: tc.get("title", "")),
    ("Module",          lambda tc, _: tc.get("module", "")),
    ("Type",            lambda tc, _: tc.get("test_type", "")),
    ("Priority",        lambda tc, _: tc.get("priority", "Medium")),
    ("Confidence",      lambda tc, _: tc.get("confidence_score", 0)),
    ("Preconditions",   lambda tc, _: "\n".join(tc.get("preconditions", []))
                        if isinstance(tc.get("preconditions"), list)
                        else tc.get("preconditions", "")),
    ("Manual Steps",    lambda tc, steps: "\n".join(
        f"{s.get('step_number', i + 1)}. {s.get('action', '')}"
        for i, s in enumerate(steps)
    )),
    ("Expected Result", lambda tc, steps: "\n".join(
        s.get("expected_result", "") for s in steps
    )),
    ("Tags",            lambda tc, _: ", ".join(tc.get("tags", []))
                        if isinstance(tc.get("tags"), list) else ""),
]


class CSVAdapter(BaseExportAdapter):
    """
    Export adapter that saves test cases as a .csv file.

    Uses the standard-library csv module, so it has zero external
    dependencies and cannot fail on a missing package. In dry_run mode the
    file is NOT written; only the target path is returned.
    """

    name = "csv"

    def validate_config(self) -> tuple[bool, str]:
        """
        CSV export has no external dependencies, so it is always ready.

        Returns:
            tuple[bool, str]: Always (True, "").
        """
        return True, ""

    def export(
        self,
        test_cases: list[dict],
        project: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        """
        Write test cases to a .csv file.

        Multi-line fields (steps, preconditions) keep embedded newlines; the
        csv module quotes them automatically so the file stays valid.

        Args:
            test_cases (list[dict]): Validated QAX test case dicts.
            project (str | None): Used in the output filename. Falls back to
                                   DEFAULT_PROJECT env var, then "export".
            dry_run (bool): When True, log the target path without writing.

        Returns:
            dict: Standard result dict with "references" holding the file path.
        """
        if not test_cases:
            return self._empty_result(0, error="No test cases provided")

        proj       = project or os.getenv("DEFAULT_PROJECT", "export")
        timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)
        filename   = str(output_dir / f"test_cases_{proj}_{timestamp}.csv")

        if dry_run:
            logger.info(
                "QAX CSVAdapter [DRY RUN]: would save %d case(s) to %s",
                len(test_cases), filename,
            )
            print(f"  [DRY RUN] Would save {len(test_cases)} test case(s) to: {filename}")
            return self._success_result(len(test_cases), 0, [filename])

        try:
            with open(filename, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([label for label, _ in _COLUMNS])
                for tc in test_cases:
                    steps = tc.get("manual_steps", []) or []
                    writer.writerow([extractor(tc, steps) for _, extractor in _COLUMNS])

            logger.info(
                "QAX CSVAdapter: saved %d test case(s) → %s",
                len(test_cases), filename,
            )
            return self._success_result(len(test_cases), 0, [filename])

        except Exception as exc:
            logger.error("QAX CSVAdapter: write failed — %s", exc)
            return self._empty_result(len(test_cases), error=str(exc))
