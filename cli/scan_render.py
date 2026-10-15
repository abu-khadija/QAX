"""cli/scan_render.py — render an OrchestratorResult for the console (pure).

Extracted from main.py run_scan_command (S8). Aligned with B1: the output separates
the DETERMINISTIC CONFIRMED block from the non-deterministic AI leads, not by severity.
"""
from __future__ import annotations

_COVERAGE_MARK = {
    "generation": "✓ covered",
    "template": "✓ covered (gap filled by QAX)",
    "uncovered": "✗ uncovered",
}


def render_scan_result(result) -> str:
    """Build the console summary for a scan result. Never raises; returns text."""
    bar = "─" * 66
    lines: list[str] = ["", bar, f"  RESULT — {result.summary()}", bar]

    if getattr(result, "coverage", None):
        lines.append("\n  Coverage goals:")
        for goal, status in result.coverage.items():
            lines.append(f"    · {goal} — {_COVERAGE_MARK.get(status, status)}")

    det = result.confirmed_deterministic
    ai = result.leads_ai
    if det or ai:
        lines.append(f"\n  🐞 CONFIRMED (deterministic, 0-FP): {len(det)}   ·   "
                     f"AI leads (may vary): {len(ai)}")
        for b in det:
            lines.append(f"    [{b.get('severity') or 'P?'}] {b.get('title', '')}  ({b.get('source', '')})")
            if b.get("evidence"):
                lines.append(f"        {str(b['evidence'])[:150]}")
        for b in ai:
            lines.append(f"    [{b.get('severity') or 'P?'}] {b.get('title', '')}  "
                         f"({b.get('source', '')})  ⟨AI lead — may vary⟩")
            if b.get("evidence"):
                lines.append(f"        {str(b['evidence'])[:150]}")
    else:
        gaps = list(getattr(result, "coverage_gaps", []) or [])
        if gaps:
            lines.append(f"\n  No findings — but {len(gaps)} check(s) could NOT run (below), so this "
                         "is NOT a clean bill of health.")
        else:
            lines.append("\n  No findings — the app held up to the scan.")

    gaps = list(getattr(result, "coverage_gaps", []) or [])
    if gaps:
        lines.append("\n  ⚠ NOT TESTED — these checks could not run (a quiet result here means "
                     "\"unknown\", not \"safe\"):")
        for g in gaps:
            lines.append(f"    · {g.get('probe')}: {g.get('reason')}")
    return "\n".join(lines) + "\n"
