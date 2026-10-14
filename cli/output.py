"""cli/output.py — saving and finding generated test cases on disk (S8).

Extracted from main.py. `out_dir` is a parameter so the logic is testable against a
temporary directory; main.py's wrappers keep the original "output/" default.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def save_results_json(test_cases: list[dict], output_path: str | None = None,
                      out_dir: str = "output") -> str:
    """Write test cases to JSON (auto-named in out_dir when no path). Returns the path."""
    if output_path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = str(Path(out_dir) / f"test_cases_{stamp}.json")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(test_cases, f, indent=2, ensure_ascii=False)
    logger.info("Saved %d test case(s) to: %s", len(test_cases), output_path)
    return output_path


def save_results_markdown(test_cases: list[dict], source_files: list[str],
                          output_path: str) -> str:
    """Write test cases as a Markdown PR comment. Returns the path, or "" on error."""
    try:
        from core.markdown_reporter import generate_pr_comment
        comment = generate_pr_comment(test_cases, source_files)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(comment)
        logger.info("Saved markdown PR comment (%d cases) to: %s", len(test_cases), output_path)
        return output_path
    except Exception as e:
        logger.error("Could not save markdown output: %s", e)
        return ""


def find_test_case_in_output(tc_id: str, out_dir: str = "output") -> dict | None:
    """Search saved test_cases_*.json files (newest first) for a test case ID."""
    d = Path(out_dir)
    if not d.exists():
        return None
    for json_file in sorted(d.glob("test_cases_*.json"), reverse=True):
        try:
            with open(json_file, encoding="utf-8") as f:
                cases = json.load(f)
            for tc in cases:
                if isinstance(tc, dict) and tc.get("test_case_id") == tc_id:
                    return tc
        except (json.JSONDecodeError, OSError):
            continue
    return None
