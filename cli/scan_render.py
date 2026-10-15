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
        lines.extend(_render_gaps(gaps))

    left = list(getattr(result, "left_on_target", []) or [])
    if left:
        created = getattr(result, "created_records", 0) or len(left)
        lines.append(f"\n  ⚠ LEFT ON THE TARGET — QAX created {created} test record(s) and could not remove "
                     f"{len(left)} (remove them by hand):")
        for item in left[:12]:
            ident = f" {item['key']}" if item.get("key") else ""
            lines.append(f"    · {item.get('collection') or '?'}{ident} — {item.get('why')}")
        if len(left) > 12:
            lines.append(f"    · … and {len(left) - 12} more (see left_on_target in the --out JSON)")
    return "\n".join(lines) + "\n"


# What the operator can do about a recurring gap — one decision, not one note per probe.
_NEEDS_HINT = {
    "token_b": "supply a second, ORDINARY user's token with --token-b (and the owner's with --token)",
    "auth-token": "supply a valid token with --token (QAX could not self-register or log in)",
    "valid-body": "QAX could not build a request this app accepts — an example valid request body "
                  "for the create endpoints would unblock it",
}


def _render_gaps(gaps: list) -> list[str]:
    """Group the not-tested gaps by what would unblock them (escalate ONCE)."""
    grouped: dict = {}
    loose: list = []
    for g in gaps:
        (grouped.setdefault(g["needs"], []) if g.get("needs") else loose).append(g)
    out: list[str] = []
    for needs, items in grouped.items():
        probes = ", ".join(sorted({str(i.get("probe")) for i in items}))
        out.append(f"    · {probes}")
        out.append(f"        → to enable: {_NEEDS_HINT.get(needs, needs)}")
    for g in loose:
        out.append(f"    · {g.get('probe')}: {g.get('reason')}")
    return out
