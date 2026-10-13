"""
core/failure_patterns.py — Failure-Mode Knowledge Base
======================================================
The concrete failure modes QAX must actively hunt for — distilled from the user's
real QA craft (silent data loss, missing validation, verify-before-file) and a
vulnerability catalog (hardcoded secrets, weak-crypto limits, broken object
authorization, computed off-by-one). This is the knowledge QAX lacked: it knew
generic ISTQB techniques but not "a hardcoded secret means forgeable tokens" or
"bcrypt truncates at 72 bytes".

Each pattern is a named thing to look for in the AppModel. Deterministic detectors
handle the crisp structural ones; a knowledge-guided model pass applies the rest.

Public API:
    from core.failure_patterns import PATTERNS, catalog_block
"""

from __future__ import annotations

# id, name, category, severity (default), look_for (what evidence to seek).
PATTERNS: list[dict] = [
    {"id": "hardcoded-secret", "name": "Hardcoded / placeholder secret",
     "category": "security", "severity": "P1",
     "look_for": "A JWT/signing secret, API key, or password with a hardcoded or placeholder "
                 "default value. It lets anyone forge tokens or authenticate — account takeover."},
    {"id": "weak-crypto-limit", "name": "Input exceeds a crypto library limit",
     "category": "security", "severity": "P2",
     "look_for": "A password/hash input whose allowed length exceeds the hashing library's limit "
                 "(bcrypt truncates or errors past 72 bytes) — silent truncation or a 500 crash."},
    {"id": "broken-object-authz", "name": "Broken object-level authorization (IDOR)",
     "category": "security", "severity": "P1",
     "look_for": "An endpoint that fetches a record by id without checking it belongs to the current "
                 "user — user A can read/modify/delete user B's resource."},
    {"id": "mass-assignment", "name": "Mass assignment / over-posting",
     "category": "security", "severity": "P2",
     "look_for": "A create/update that binds client fields directly to the model, letting a client set "
                 "protected fields (user_id, is_admin, id, order) it shouldn't control."},
    {"id": "injection", "name": "Injection (SQL / XSS)",
     "category": "security", "severity": "P1",
     "look_for": "User input concatenated into a query, or rendered unescaped in the UI "
                 "(dangerouslySetInnerHTML). ORMs parameterize — flag only raw string building."},
    {"id": "missing-input-validation", "name": "Missing input validation (differential)",
     "category": "correctness", "severity": "P3",
     "look_for": "A required input field with NO length/format constraint while a sibling field HAS "
                 "one (e.g. title accepts empty while name requires min_length) — inconsistent, "
                 "lets blank/oversized data in via the API even when the UI blocks it."},
    {"id": "computed-value-offbyone", "name": "Wrong computed value (off-by-one / floor vs ceil)",
     "category": "correctness", "severity": "P2",
     "look_for": "A count/total/page-count computed with integer floor division instead of ceil, or an "
                 "off-by-one in a boundary — e.g. total_pages = total // page_size hides the last page."},
    {"id": "silent-data-loss", "name": "Silent data loss (persistence)",
     "category": "correctness", "severity": "P1",
     "look_for": "A save that returns success but does not actually persist a field (nested/computed "
                 "fields, wrong binding). Test: set → save → reload → read back."},
    {"id": "contract-mismatch", "name": "Frontend/backend contract mismatch",
     "category": "correctness", "severity": "P3",
     "look_for": "The frontend and backend disagree on a field name, a default, a status code, or a "
                 "limit (e.g. default page size differs) — subtle wrong behaviour at the seam."},
    {"id": "error-leakage", "name": "Unhandled error / info leak",
     "category": "robustness", "severity": "P2",
     "look_for": "A valid, well-formed request that can trigger a 500, or an error response that leaks "
                 "a stack trace / internal detail."},
    {"id": "insecure-cors", "name": "Over-permissive CORS",
     "category": "security", "severity": "P3",
     "look_for": "CORS allowing all origins with credentials, or reflecting the Origin — cross-site "
                 "requests can act as the user."},
    {"id": "missing-rate-limit", "name": "No rate limiting on sensitive endpoints",
     "category": "security", "severity": "P3",
     "look_for": "Login/register/password endpoints with no throttling — brute force / enumeration."},
    {"id": "invalid-state-transition", "name": "Unguarded state transition",
     "category": "correctness", "severity": "P3",
     "look_for": "A status/lifecycle change that isn't guarded — acting on a deleted/closed item, or an "
                 "illegal transition, is accepted."},
]

_BY_ID = {p["id"]: p for p in PATTERNS}


def get(pattern_id: str) -> dict:
    """Return a pattern by id (empty dict if unknown)."""
    return _BY_ID.get(pattern_id, {})


def catalog_block() -> str:
    """Render the catalog for a knowledge-guided detection prompt."""
    lines = ["FAILURE-MODE CATALOG — check the app against EACH pattern; report only those with "
             "real evidence in this app:"]
    for p in PATTERNS:
        lines.append(f"- [{p['id']}] {p['name']} ({p['severity']}): {p['look_for']}")
    return "\n".join(lines)
