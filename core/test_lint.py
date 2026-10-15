"""
core/test_lint.py — deterministic quality lint for generated test cases
=======================================================================
The test-case rewrite rules: a test case that *runs* can still be a bad
test — vague expected results, execution notes leaking into the spec, steps with no
verifiable outcome. This is a zero-LLM, rule-based check so quality is measured, not claimed.

Each rule returns issues with a severity:
  error — the case cannot be executed or verified as written (T1, T2, R1)
  warn  — the case is weak and should be rewritten (R4, R5, R6, A1, A2, A3, S2, S3, S4, S5)
  info  — style (S1)

`lint_case` / `lint_cases` never raise: a malformed case is itself reported (T0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BUG_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
_TCID = re.compile(r"\bTC[-_ ]?[A-Z0-9]*-?\d+\b", re.I)
_EXEC_NOTE = re.compile(
    r"\b(live[- ]confirmed|confirmed (?:live|on)|observed (?:on|in)|actual result|"
    r"was (?:passed|failed) on|tested on|run on build|verified on (?:uat|prod|staging|dev))\b", re.I)
_VAGUE = re.compile(
    r"\b(works? (?:correctly|properly|as expected|fine)|behaves? (?:correctly|properly)|"
    r"(?:is|are) (?:correct|proper|valid|fine)|as expected|properly|appropriately|successfully handled)\b",
    re.I)
_EITHER_OR = re.compile(r"\beither\b.+\bor\b", re.I | re.S)
_USERNAME = re.compile(r"\b(?:user(?:name)?|login|account)\s*[:=]\s*\S+|\b\w+@\w+\.\w+\b", re.I)
_VAGUE_END = re.compile(r"\b(?:verify|check|test|validate|ensure|confirm)\s*$", re.I)
_AREA = re.compile(r"^\s*\[[^\]]+\]")

MAX_EXPECTED = 350
MAX_TITLE = 130
MIN_TITLE = 35
MIN_ACTION = 8
MAX_ARROWS = 6


@dataclass(frozen=True)
class LintIssue:
    """One rule violation on one test case (or one of its steps)."""
    rule: str
    severity: str  # error | warn | info
    message: str
    step: int | None = None

    def to_dict(self) -> dict:
        """JSON-able form."""
        return {"rule": self.rule, "severity": self.severity, "message": self.message, "step": self.step}


def _steps(case: dict) -> list[dict]:
    """The manual steps that are dict-shaped (anything else is ignored, not crashed on)."""
    raw = case.get("manual_steps")
    return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []


def _title_issues(title: str) -> list[LintIssue]:
    """Title rules S1–S5."""
    out: list[LintIssue] = []
    if not _AREA.match(title):
        out.append(LintIssue("S1", "info", "title has no leading [Area] tag"))
    if len(title) > MAX_TITLE:
        out.append(LintIssue("S2", "warn", f"title is {len(title)} chars (max {MAX_TITLE})"))
    if len(title) < MIN_TITLE:
        out.append(LintIssue("S3", "warn", f"title is only {len(title)} chars — too short to say what is verified"))
    if _BUG_KEY.search(title):
        out.append(LintIssue("S4", "warn", "title carries a bug key — a test describes behaviour, not a ticket"))
    if _VAGUE_END.search(title.rstrip(" .:")):
        out.append(LintIssue("S5", "warn", "title ends on a bare verb (verify/check/…) — it never says what"))
    return out


def _step_issues(i: int, step: dict) -> list[LintIssue]:
    """Per-step rules T2, R1, R4–R6, A1–A3."""
    out: list[LintIssue] = []
    action = str(step.get("action") or "").strip()
    expected = str(step.get("expected_result") or "").strip()
    if not expected:
        out.append(LintIssue("T2", "error", "step has no expected result — nothing to verify", i))
    if len(action) < MIN_ACTION:
        out.append(LintIssue("A2", "warn", "action is too short to be reproducible", i))
    if _USERNAME.search(action) or _TCID.search(action):
        out.append(LintIssue("A1", "warn", "action hard-codes a username/e-mail/test-case id", i))
    if action.count("→") + action.count("->") >= MAX_ARROWS:
        out.append(LintIssue("A3", "warn", "action is a navigation chain of many arrows — split it", i))
    if expected:
        if _EXEC_NOTE.search(expected) or _BUG_KEY.search(expected):
            out.append(LintIssue("R1", "error", "expected result contains execution notes / a bug key", i))
        if _VAGUE.search(expected):
            out.append(LintIssue("R4", "warn", "expected result is vague ('works correctly', 'as expected'…)", i))
        if len(expected) > MAX_EXPECTED:
            out.append(LintIssue("R5", "warn", f"expected result is {len(expected)} chars (max {MAX_EXPECTED})", i))
        if _EITHER_OR.search(expected):
            out.append(LintIssue("R6", "warn", "expected result says 'either … or' — a test must have one outcome", i))
    return out


def lint_case(case: dict) -> list[LintIssue]:
    """All issues for one test case. Never raises."""
    try:
        if not isinstance(case, dict):
            return [LintIssue("T0", "error", "test case is not an object")]
        out = _title_issues(str(case.get("title") or ""))
        steps = _steps(case)
        if not steps and not case.get("api_steps") and not case.get("automation_steps"):
            out.append(LintIssue("T1", "error", "test case has no steps"))
        for n, step in enumerate(steps, 1):
            out.extend(_step_issues(int(step.get("step_number") or n), step))
        return out
    except Exception as exc:  # a lint must never take the pipeline down
        return [LintIssue("T0", "error", f"could not lint: {exc}")]


def lint_cases(cases: list[dict]) -> dict:
    """
    {"cases": N, "clean": M, "by_rule": {rule: count}, "by_severity": {...},
     "results": [{"title", "issues": [...]}]} — cases with any issue (incl. info) appear in results; `clean` = no error/warn.
    """
    by_rule: dict[str, int] = {}
    by_sev = {"error": 0, "warn": 0, "info": 0}
    results: list[dict] = []
    clean = 0
    for case in cases or []:
        issues = lint_case(case)
        if not any(it.severity != "info" for it in issues):
            clean += 1  # style-only (info) issues don't make a case "unclean"
        if not issues:
            continue
        for it in issues:
            by_rule[it.rule] = by_rule.get(it.rule, 0) + 1
            by_sev[it.severity] = by_sev.get(it.severity, 0) + 1
        title = str(case.get("title") or "") if isinstance(case, dict) else ""
        results.append({"title": title, "issues": [it.to_dict() for it in issues]})
    return {"cases": len(cases or []), "clean": clean, "by_rule": dict(sorted(by_rule.items())),
            "by_severity": by_sev, "results": results}


def summary_line(report: dict) -> str:
    """One line for the analyze output: '12/15 cases clean — 2 errors, 5 warnings'."""
    sev = report.get("by_severity") or {}
    if not report.get("cases"):
        return "Test quality: no cases to lint"
    if report["clean"] == report["cases"]:
        return f"Test quality: all {report['cases']} cases pass the lint"
    return (f"Test quality: {report['clean']}/{report['cases']} cases clean — "
            f"{sev.get('error', 0)} error(s), {sev.get('warn', 0)} warning(s), {sev.get('info', 0)} info "
            f"(run `qax lint-tests --file <cases.json>` for details)")


def render(report: dict) -> str:
    """Human-readable detail for `qax lint-tests`."""
    lines = [summary_line(report), ""]
    for r in report.get("results", []):
        lines.append(f"• {r['title'] or '(untitled)'}")
        for it in r["issues"]:
            where = f" step {it['step']}" if it.get("step") else ""
            lines.append(f"    [{it['severity']:<5}] {it['rule']}{where}: {it['message']}")
    return "\n".join(lines)
