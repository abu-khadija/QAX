"""
cli/lint_tests.py — `qax lint-tests --file cases.json [--json] [--fail-on error|warn]`
=====================================================================================
Deterministic quality lint (core/test_lint.py) over a saved test-case file
(`output/test_cases_*.json`: a list of cases, or {"test_cases": [...]}).
Exit 0 = ok, 1 = issues at/above --fail-on, 2 = unreadable input.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_RANK = {"info": 0, "warn": 1, "error": 2}


def load_cases(path: str) -> list[dict]:
    """Cases from a JSON file ([] when it is not a case list). OSError/ValueError on unreadable input."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("test_cases") or data.get("cases") or []
    return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []


def run(argv: list[str]) -> int:
    """Entry point for `qax lint-tests`."""
    from core.test_lint import lint_cases, render
    p = argparse.ArgumentParser(prog="qax lint-tests", description="Lint generated test cases.")
    p.add_argument("--file", required=True, help="test-case JSON file")
    p.add_argument("--json", action="store_true", help="machine-readable report")
    p.add_argument("--fail-on", choices=["error", "warn"], default=None,
                   help="exit 1 when an issue at/above this severity exists")
    a = p.parse_args(argv)
    try:
        cases = load_cases(a.file)
    except (OSError, ValueError) as exc:
        print(f"QAX: cannot read {a.file}: {exc}")
        return 2
    report = lint_cases(cases)
    print(json.dumps(report, indent=2) if a.json else render(report))
    if a.fail_on:
        floor = _RANK[a.fail_on]
        if any(_RANK.get(s, 0) >= floor and n for s, n in report["by_severity"].items()):
            return 1
    return 0
