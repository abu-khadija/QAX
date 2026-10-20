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


# ── Security risks a story implies (deterministic, no AI) ────────────────────
_SEC_RULES = [
    (r"\b(own|my|their|his|her|personal|private|profile|account)\b|\b(edit|update|change|delete|remove|"
     r"archive|cancel)\b", "access control",
     "Another user (or a logged-out visitor) does the same action, or opens the record's URL/id "
     "directly — must be refused, data unchanged and not shown (BOLA/IDOR).", "bola"),
    (r"\b(admin|role|permission|manager|owner|approve|moderat)\w*", "function-level authorization",
     "A lower role calls the same action or page directly — must be refused (BFLA).", "bfla"),
    (r"\b(log ?in|sign ?in|password|reset|session|log ?out|token|2fa|otp)\b", "authentication",
     "Wrong / expired / reused credentials or links are refused; after logout the old session no "
     "longer works; error messages do not reveal which part was wrong.", "auth"),
    (r"\b(comment|note|title|name|description|message|text|search|bio|post)\b", "injection / stored XSS",
     "Hostile input (<script>, a quote, very long text) is stored and shown as plain text; the search "
     "does not error.", "injection"),
    (r"\b(upload|file|image|attachment|import)\b", "file upload",
     "A wrong type, a huge file and a script disguised as an image are refused.", "upload"),
    (r"\b(pay|price|amount|quantity|discount|coupon|balance|transfer|order)\w*", "business logic",
     "Negative, zero and huge amounts, and a changed price in the request, are refused.", "logic"),
]


def security_risks(criteria: list) -> list:
    """
    The security checks each acceptance criterion implies — the same story drives
    functional AND security testing (the scanner can then prove the access-control and
    injection ones on the running app).

    Args:
        criteria (list[dict]): From extract_criteria().

    Returns:
        list[dict]: [{"criterion": "AC-n", "risk", "check", "probe"}] (one per criterion and risk).
    """
    out = []
    for c in criteria or []:
        text = str(c.get("text", "")).lower()
        for pattern, risk, check, probe in _SEC_RULES:
            if re.search(pattern, text):
                out.append({"criterion": c["id"], "risk": risk, "check": check, "probe": probe})
    return out


def security_block(risks: list) -> str:
    """The derived security risks as a prompt section ("" when none)."""
    if not risks:
        return ""
    return ("SECURITY RISKS IN THESE CRITERIA (write a security case for each, traced to its AC):\n"
            + "\n".join(f"{r['criterion']} — {r['risk']}: {r['check']}" for r in risks))


def requirement_verdicts(criteria: list, cov: dict, results: list) -> list:
    """
    One verdict per acceptance criterion from the cases that cover it: FAILED if any
    covering case failed, PASSED only if every covering case ran and passed, NOT COVERED
    when no case covers it, otherwise NOT FULLY RUN (a skip or an error proves nothing).

    Returns:
        list[dict]: [{"id", "text", "verdict", "cases": [{"id", "status"}]}].
    """
    status = {str(r.get("test_case_id")): r.get("status") for r in results or [] if isinstance(r, dict)}
    out = []
    for c in criteria or []:
        ids = (cov or {}).get("by_criterion", {}).get(c["id"], [])
        sts = [status.get(i) for i in ids]
        if not ids:
            v = "NOT COVERED"
        elif "FAIL" in sts:
            v = "FAILED"
        elif sts and all(x == "PASS" for x in sts):
            v = "PASSED"
        else:
            v = "NOT FULLY RUN"
        out.append({"id": c["id"], "text": c.get("text", ""), "verdict": v,
                    "cases": [{"id": i, "status": status.get(i) or "not run"} for i in ids]})
    return out


def verdict_comment(verdicts: list, url: str = "", bugs: "list | None" = None) -> str:
    """The requirement verdicts as a plain-text comment for the story."""
    lines = [f"Functional QA results{(' on ' + url) if url else ''}:"]
    for v in verdicts:
        cases = ", ".join(f"{c['id']} {c['status']}" for c in v["cases"]) or "no case"
        lines.append(f"{v['id']} {v['verdict']} — {v['text']} ({cases})")
    if bugs:
        lines.append("Bugs: " + ", ".join(bugs))
    lines.append("A case that did not fully run is never counted as passed.")
    return "\n".join(lines)
