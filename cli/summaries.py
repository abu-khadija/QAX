"""cli/summaries.py — pure run/push summary formatters extracted from main.py (S8).

String-returning (testable); main.py's thin _print_* wrappers call print() on these.
"""
from __future__ import annotations


def render_push_summary(push_result: dict) -> str:
    """Format an Xray push summary (from XrayClient.push_batch())."""
    summary = (push_result or {}).get("summary", {})
    lines = ["\n  Xray Push Summary:",
             f"    Pushed : {summary.get('pushed_count', 0)}",
             f"    Failed : {summary.get('failed_count', 0)}"]
    if push_result.get("pushed"):
        lines.append("    Jira Keys Created:")
        for item in push_result["pushed"]:
            lines.append(f"      {item['test_case_id']} → {item['jira_key']}")
    if push_result.get("failed"):
        lines.append("    Failed:")
        for item in push_result["failed"]:
            lines.append(f"      {item['test_case_id']}: {str(item.get('error', 'Unknown error'))[:60]}")
    return "\n".join(lines)


def render_run_summary(mode: str, results: dict) -> str:
    """Format a short summary of a run_* result dict."""
    results = results or {}
    if results.get("error"):
        return f"\n  ✗ Run '{mode}' failed: {results['error']}"
    total_valid = results.get("total_valid", results.get("total", 0))
    duration = results.get("duration_seconds", 0)
    output = results.get("output_file", "")
    lines = [f"\n  ✓ Run '{mode}' complete"]
    if results.get("total_generated"):
        lines.append(f"    Generated : {results.get('total_generated', 0)}")
    lines.append(f"    Valid     : {total_valid}")
    if results.get("pushed_count"):
        lines.append(f"    Pushed    : {results['pushed_count']}")
    if duration:
        lines.append(f"    Duration  : {duration:.1f}s")
    if output:
        lines.append(f"    Output    : {output}")
    if results.get("message"):
        lines.append(f"    Note      : {results['message']}")
    return "\n".join(lines)
