"""
core/junit.py — functional run results as JUnit XML (CI dashboards read it)
==========================================================================
GitHub Actions, GitLab, Jenkins and Azure DevOps all show JUnit XML as test results.
PASS → passed; FAIL → <failure> (the failing step, expected vs actual); ERROR → <error>
(the test could not run — a test problem, not an app bug); SKIP / not run → <skipped>.
A case is never reported as passed unless every step ran and passed. Never raises.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr


def _step_text(r: dict) -> str:
    """Every step of a case as readable lines."""
    lines = []
    for s in r.get("steps") or []:
        line = f"#{s.get('step_number', '?')} {s.get('status', '')} {s.get('action_type') or ''} — {s.get('actual_result') or ''}"
        if s.get("error"):
            line += f" — {s['error']}"
        if s.get("screenshot"):
            line += f" [screenshot: {s['screenshot']}]"
        lines.append(line)
    for e in r.get("page_errors") or []:
        lines.append(f"page problem: {e.get('kind')}: {e.get('text')}")
    return "\n".join(lines)


def to_junit(batch: dict, suite: str = "QAX functional QA") -> str:
    """
    Args:
        batch: TestExecutor.execute_batch() / ExecutionService raw result ({"results": [...]}).
        suite: the testsuite name.

    Returns:
        str: a JUnit XML document.
    """
    results = [r for r in (batch or {}).get("results") or [] if isinstance(r, dict)]
    counts = {"FAIL": 0, "ERROR": 0, "SKIP": 0}
    cases = []
    total_time = 0.0
    for r in results:
        st = str(r.get("status") or "SKIP").upper()
        t = float(r.get("duration_seconds") or r.get("duration") or 0)
        total_time += t
        name = f"{r.get('test_case_id', '?')} {r.get('title', '')}".strip()
        body = ""
        detail = _step_text(r)
        bad = next((s for s in r.get("steps") or [] if s.get("status") not in ("PASS", "HEALED")), {})
        msg = str(bad.get("actual_result") or r.get("error") or st)[:300]
        if st == "FAIL":
            counts["FAIL"] += 1
            body = f"<failure message={quoteattr(msg)} type=\"AssertionError\">{escape(detail)}</failure>"
        elif st == "ERROR":
            counts["ERROR"] += 1
            body = f"<error message={quoteattr(msg)} type=\"TestError\">{escape(detail)}</error>"
        elif st != "PASS":
            counts["SKIP"] += 1
            body = f"<skipped message={quoteattr(msg)}/>"
        if st == "PASS" and detail:
            body = f"<system-out>{escape(detail)}</system-out>"
        cases.append(f"  <testcase classname={quoteattr(suite)} name={quoteattr(name)} time=\"{t:.2f}\">{body}</testcase>")
    head = (f"<testsuite name={quoteattr(suite)} tests=\"{len(results)}\" failures=\"{counts['FAIL']}\" "
            f"errors=\"{counts['ERROR']}\" skipped=\"{counts['SKIP']}\" time=\"{total_time:.2f}\">")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + head + "\n" + "\n".join(cases) + "\n</testsuite>\n"
