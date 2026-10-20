"""
core/traceability.py — acceptance criteria ↔ test cases (ISTQB traceability)
============================================================================
A user story's acceptance criteria are the test basis. This module numbers them
(AC-1, AC-2 …) so generation can reference them, then reads the "Covers: AC-n |
Technique: X" line every generated case starts its description with (see
core.istqb_knowledge.TRACEABILITY_RULE) and answers the questions a QA lead asks:
which criterion is covered by which case, which criterion has NO case, and which
techniques were used. No schema change; never raises.
"""

from __future__ import annotations

import re

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)]|AC[-\s]?\d+\s*[:.)-]|Given\b|When\b|Then\b|And\b)\s*", re.I)
_COVERS = re.compile(r"Covers:\s*([A-Z0-9 ,\-]+?)(?:\||·|\.|$)", re.I)
_TECH = re.compile(r"Technique:\s*([^|.·\n]+)", re.I)


def extract_criteria(story: str) -> list:
    """
    Number the acceptance criteria of a story.

    Bulleted / numbered / "AC1:" / Given-When-Then lines become one criterion each;
    a story written as prose is split into sentences that state behaviour.

    Args:
        story (str): User story and acceptance criteria text.

    Returns:
        list[dict]: [{"id": "AC-1", "text": "..."}, ...] (empty for empty input).
    """
    text = (story or "").strip()
    if not text:
        return []
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    bullets = [ln for ln in lines if _BULLET.match(ln)]
    if bullets:
        items = [_BULLET.sub("", ln).strip() for ln in bullets]
    else:
        # prose: every sentence states a behaviour (the "As a user I can …" one is the main flow)
        items = [s.strip() for s in re.split(r"(?<=[.!?])\s+", " ".join(lines)) if s.strip()]
    items = [i for i in items if len(i) > 3]
    return [{"id": f"AC-{n}", "text": t} for n, t in enumerate(items, 1)]


def criteria_block(criteria: list) -> str:
    """The numbered criteria as a prompt section ("" when there are none)."""
    if not criteria:
        return ""
    return "ACCEPTANCE CRITERIA (reference them as Covers: AC-n):\n" + "\n".join(
        f"{c['id']}: {c['text']}" for c in criteria)


def coverage(cases: list, criteria: list) -> dict:
    """
    Requirement coverage and technique mix of a set of cases.

    Args:
        cases (list[dict]): Test cases (description/title/review_note searched).
        criteria (list[dict]): From extract_criteria().

    Returns:
        dict: {"by_criterion": {AC-id: [case ids]}, "uncovered": [AC-ids],
               "untraced_cases": [case ids with no Covers: line],
               "techniques": {name: count}, "percent": int}
    """
    by = {c["id"]: [] for c in criteria or []}
    untraced, techniques = [], {}
    for tc in cases or []:
        cid = str(tc.get("test_case_id") or tc.get("title") or "?")
        text = " ".join(str(tc.get(k) or "") for k in ("description", "title", "review_note"))
        m = _COVERS.search(text)
        ids = re.findall(r"AC-?\s?(\d+)", m.group(1), re.I) if m else []
        if not ids:
            untraced.append(cid)
        for n in ids:
            key = f"AC-{int(n)}"
            if key in by and cid not in by[key]:
                by[key].append(cid)
        t = _TECH.search(text)
        if t:
            name = t.group(1).strip().rstrip(".")
            techniques[name] = techniques.get(name, 0) + 1
    uncovered = [k for k, v in by.items() if not v]
    pct = int(round(100 * (len(by) - len(uncovered)) / len(by))) if by else 0
    return {"by_criterion": by, "uncovered": uncovered, "untraced_cases": untraced,
            "techniques": techniques, "percent": pct}
