"""
response_formatter.py — Natural Language Output Formatter
==========================================================
Converts structured test case lists into a human-friendly Markdown summary
suitable for sharing with product owners, developers, or QA leads who
don't want to read raw JSON.

Activated via the --natural flag on the analyze command.
"""

import logging

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


def to_natural_language(
    test_cases: list,
    file_name: str,
    run_duration: float = 0,
) -> str:
    """
    Convert a list of validated test cases into a Markdown narrative summary.

    Splits test cases into two groups:
      - Ready to push: confidence >= 7, needs_review=False
      - Needs your review: needs_review=True or confidence < 7

    Args:
        test_cases (list): List of validated test case dicts from validate_batch().
        file_name (str): Name of the analyzed source file (for the header).
        run_duration (float): How long the analysis took in seconds.

    Returns:
        str: Formatted Markdown string ready to print or write to a file.
    """
    if not test_cases:
        return (
            f"\n## QAX Results: {file_name}\n\n"
            "No test cases were generated for this file.\n"
            "The file may contain no testable logic, or confidence was too low.\n"
        )

    total = len(test_cases)
    duration_str = f"{run_duration:.1f}s" if run_duration > 0 else "N/A"

    # ── Split into ready and flagged ──────────────────────────────────────────
    ready = [
        tc for tc in test_cases
        if tc.get("needs_review") is not True
        and isinstance(tc.get("confidence_score"), (int, float))
        and tc.get("confidence_score", 0) >= 7
    ]
    flagged = [tc for tc in test_cases if tc not in ready]

    # ── Count auto-accepted ───────────────────────────────────────────────────
    auto_accepted = sum(1 for tc in test_cases if tc.get("auto_accepted") is True)

    # ── Compute average confidence ─────────────────────────────────────────────
    scores = [
        tc["confidence_score"] for tc in test_cases
        if isinstance(tc.get("confidence_score"), (int, float))
    ]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0

    lines = [
        f"\n## QAX Results: {file_name}\n",
        f"I analyzed this file and found **{total} test scenario(s)** in {duration_str}.\n",
    ]

    # ── Ready section ─────────────────────────────────────────────────────────
    if ready:
        lines.append(f"### ✅ Ready to push ({len(ready)} case(s))\n")
        for tc in ready:
            title = tc.get("title", "Untitled")
            desc = tc.get("description", "")
            tc_id = tc.get("test_case_id", "?")
            score = tc.get("confidence_score", "?")
            auto_tag = " *(auto-accepted)*" if tc.get("auto_accepted") else ""
            lines.append(f"**[{tc_id}] {title}**{auto_tag}  ")
            lines.append(f"Confidence: {score}/10  ")
            if desc:
                lines.append(f"{desc}  ")
            lines.append("")

    # ── Needs review section ──────────────────────────────────────────────────
    if flagged:
        lines.append(f"### ⚠️  Needs your review ({len(flagged)} case(s))\n")
        for tc in flagged:
            title = tc.get("title", "Untitled")
            tc_id = tc.get("test_case_id", "?")
            score = tc.get("confidence_score", "?")
            note = tc.get("review_note", "") or "Low confidence — verify UI element names."
            lines.append(f"**[{tc_id}] {title}**  ")
            lines.append(f"Confidence: {score}/10  ")
            lines.append(f"Flagged because: {note}  ")
            lines.append("")

    # ── Footer ────────────────────────────────────────────────────────────────
    lines += [
        "---",
        f"Avg confidence: **{avg_score}/10**  ",
    ]
    if auto_accepted:
        lines.append(f"Auto-accepted: **{auto_accepted}** case(s) (confidence ≥ 8.5)  ")
    lines.append("Run: `python main.py feedback --stats` to see your acceptance rate.")

    return "\n".join(lines) + "\n"
