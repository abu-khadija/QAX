"""ui/presenters.py — pure HTML/formatting helpers for the Streamlit UI (S8).

No Streamlit import, so this is unit-testable without the UI runtime. ui/app.py
imports these instead of defining them inline.
"""
from __future__ import annotations

import os


def badge(text: str, cls: str = "purple") -> str:
    """Render a coloured badge as an HTML string (cls: purple/green/red/amber/teal/gray)."""
    return f'<span class="badge badge-{cls}">{text}</span>'


def confidence_bar(score: float) -> str:
    """HTML confidence bar; score 0–10 → 0–100% width, colour by band."""
    pct = min(100, max(0, score * 10))
    if score >= 8.5:
        colour = "#22c55e"
    elif score >= 7.0:
        colour = "#14b8a6"
    elif score >= 5.0:
        colour = "#f59e0b"
    else:
        colour = "#ef4444"
    return (f'<div class="conf-bar">'
            f'<div class="conf-fill" style="width:{pct}%;background:{colour}"></div>'
            f'</div>')


def risk_badge_class(risk: str) -> str:
    """Map a risk level string to a badge CSS class."""
    return {"critical": "red", "high": "amber", "medium": "teal", "low": "gray"}.get(
        (risk or "").lower(), "gray")


def score_badge_class(score: float) -> str:
    """Map a confidence score to a badge CSS class."""
    if score >= 8.5:
        return "green"
    if score >= 7.0:
        return "teal"
    if score >= 5.0:
        return "amber"
    return "red"


def count_todos(path: str) -> int:
    """Count TODO occurrences in a file (0 when it doesn't exist)."""
    if not os.path.exists(path):
        return 0
    return open(path).read().count("TODO")


def mask_secret(value: str) -> str:
    """Masked secret for display: first 4 chars + bullets (e.g. "sk-a••••")."""
    if not value:
        return "(not set)"
    return value[:4] + "•" * min(12, len(value) - 4)


def build_markdown_export(test_cases: list[dict]) -> str:
    """Markdown for a list of test cases — core.markdown_reporter, else a fallback."""
    try:
        from core.markdown_reporter import generate_pr_comment
        return generate_pr_comment(test_cases, [])
    except Exception:
        lines = [f"# QAX — Test Cases ({len(test_cases)} generated)\n"]
        for tc in test_cases:
            score = tc.get("confidence_score", 0)
            lines.append(f"## {tc.get('test_case_id','?')} — {tc.get('title','Untitled')}")
            lines.append(f"**Module:** {tc.get('module','N/A')} | **Type:** {tc.get('test_type','N/A')} | **Confidence:** {score}/10\n")
            for step in tc.get("manual_steps", []):
                lines.append(f"{step.get('step_number','?')}. {step.get('action','')}")
            lines.append("")
        return "\n".join(lines)
