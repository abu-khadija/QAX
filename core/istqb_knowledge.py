"""
core/istqb_knowledge.py — ISTQB Knowledge Base (principles + test-design techniques)
====================================================================================
Fixed, foundational QA knowledge distilled from the ISTQB syllabi — the seven
testing principles and the core black-box test-design techniques — expressed as
ACTIONABLE rules QAX applies on every generation. Unlike skill_library.py (which
learns patterns from past runs), this is standing doctrine that does not change.

Why it exists: QAX's coverage was inconsistent — it caught a boundary bug only by
luck. Boundary Value Analysis, applied systematically, turns that into a
guarantee: whenever a number or a count drives behaviour, test at and around the
boundary. The techniques here are what make coverage a method, not a gamble.

Public API:
    from core.istqb_knowledge import prompt_block, PRINCIPLES, TECHNIQUES
    text = prompt_block()          # concise instruction injected into generation
"""

from __future__ import annotations

# The seven ISTQB fundamental testing principles, each with how QAX applies it.
PRINCIPLES: list[dict] = [
    {"name": "Testing shows the presence of defects, not their absence",
     "apply": "Derive the expected result from the contract/spec, not the code; a passing test never proves correctness."},
    {"name": "Exhaustive testing is impossible",
     "apply": "Do not brute-force every input; use the techniques below to pick the few high-value cases that represent the many."},
    {"name": "Early testing (shift left)",
     "apply": "Test the smallest testable unit (endpoint/function) directly, not only through the UI."},
    {"name": "Defects cluster together",
     "apply": "Spend more cases on high-risk areas — authorization, money, state changes, computed values."},
    {"name": "Tests wear out (pesticide paradox)",
     "apply": "Vary data and add negative/boundary cases; do not repeat the same happy path in different words."},
    {"name": "Testing is context dependent",
     "apply": "Match test types to the system — an API is tested differently from a UI or a batch job."},
    {"name": "Absence-of-errors is a fallacy",
     "apply": "A feature with no bugs is still wrong if it does not meet the requirement; check behaviour against intent."},
]

# The core black-box test-design techniques (ISTQB Foundation / Advanced Test
# Analyst), each concrete enough to act on during generation.
TECHNIQUES: list[dict] = [
    {
        "name": "Equivalence Partitioning (EP)",
        "what": "Split each input into classes that should behave the same; test ONE value per class (valid and invalid).",
        "apply": "For every field, cover one valid class and each invalid class (wrong type, out of range, missing, malformed) — one case each, not many.",
    },
    {
        "name": "Boundary Value Analysis (BVA)",
        "what": "Bugs hide at the edges of a range, so test AT and AROUND every boundary.",
        "apply": ("Whenever a NUMBER or a COUNT drives behaviour (length limits, quantities, "
                  "page size, min/max, ranges), test the boundary and boundary±1: e.g. 0, 1, "
                  "min-1/min, max/max+1. For any PAGINATED or count-returning endpoint you MUST "
                  "create a number of items whose count is NOT a multiple of the page size — an "
                  "aligned count (e.g. 2 items with page_size 2) HIDES off-by-one errors and is "
                  "forbidden here. Concretely: create exactly 3 items, list with page_size=2, and "
                  "assert via expect_json that total_pages == 2 (i.e. ceil(3/2)) and total == 3. "
                  "Pick counts where count % page_size != 0. Also cover the empty case (0 items)."),
    },
    {
        "name": "Decision Table Testing",
        "what": "When several conditions combine to decide an outcome, test the meaningful combinations.",
        "apply": "For endpoints gated by multiple conditions (authenticated AND owns resource AND valid state), cover the key true/false combinations, not just the all-true path.",
    },
    {
        "name": "State Transition Testing",
        "what": "For anything with states, test valid transitions AND invalid/rejected ones.",
        "apply": "For status/lifecycle fields (active↔completed, draft→published, open→closed), test each allowed transition and at least one illegal transition (e.g. acting on a deleted item).",
    },
    {
        "name": "Use Case Testing",
        "what": "Test realistic end-to-end user scenarios, including alternate and exception flows.",
        "apply": "Cover the main success scenario and its key alternate/exception paths (the same operation from a different entry point, the undo of an action, the failure branch).",
    },
]


# Experience-based techniques (ISTQB Foundation 4.4): where specs run out.
EXPERIENCE_TECHNIQUES: list[dict] = [
    {"name": "Error Guessing",
     "apply": ("Add cases for the mistakes developers typically make here: empty or whitespace-only "
               "input, duplicates, double submit, very long text, special characters and emoji, "
               "leading/trailing spaces, case sensitivity, dates around midnight and month ends.")},
    {"name": "Exploratory Testing (charters)",
     "apply": ("For an area with no clear spec, state a short charter (Explore <area> with <resources> "
               "to discover <information>) as a case whose steps probe the riskiest behaviours first.")},
    {"name": "Checklist-based Testing",
     "apply": ("Use a standard UI checklist on every screen touched: required-field gates, error "
               "messages next to the field, disabled-until-valid buttons, cancel really cancels, "
               "back/refresh keep or reset state as intended, success feedback after save.")},
]

# White-box techniques (ISTQB Foundation 4.3): only when source code is given.
WHITE_BOX: list[dict] = [
    {"name": "Statement / Branch Coverage",
     "apply": ("When code is given, make every if/else, early return, exception path and loop "
               "(0, 1, many iterations) reachable by at least one case.")},
]

# Combinatorial (ISTQB Advanced Test Analyst): many parameters, too many combinations.
COMBINATORIAL: list[dict] = [
    {"name": "Pairwise (all-pairs) Testing",
     "apply": ("When 3+ independent options combine (role x plan x language x device), cover every "
               "PAIR of values at least once instead of every combination.")},
]

# Test types (ISTQB Foundation 2.2): not only functional.
TEST_TYPES: list[dict] = [
    {"name": "Functional", "apply": "what the system does, against each acceptance criterion"},
    {"name": "Usability and accessibility",
     "apply": ("labels on inputs, keyboard reachability, visible focus, readable error text: at "
               "least one case per screen when a UI is involved")},
    {"name": "Compatibility and basic performance",
     "apply": "responsive/mobile and slow-response behaviour, only where the story implies it"},
    {"name": "Confirmation and regression",
     "apply": ("a fixed bug gets a confirmation case; neighbouring features that share the changed "
               "code get a regression case")},
]

#: How a case records what it covers, without a schema change: its description starts
#: with this line, so a report can compute requirement coverage and the technique mix.
TRACEABILITY_RULE = ("Start every case's description with a traceability line: "
                     "'Covers: AC-<n>[, AC-<m>] | Technique: <technique name>.' Use the acceptance-"
                     "criteria numbers given in the story (AC-1, AC-2 ...); if the story has none, number "
                     "the behaviours you derived. Every acceptance criterion must be covered by at "
                     "least one case.")

RISK_RULE = ("Prioritise by risk = likelihood x impact: money, permissions, data loss and "
             "irreversible actions are critical/high; cosmetic issues are low. Generate the "
             "high-risk cases first.")


SECURITY_FROM_STORY_RULE = (
    "Security from the same story (ISTQB: security is a test type of every feature, not a separate "
    "project): for every criterion where a user creates, changes, deletes or views THEIR OWN data, or "
    "that needs a login or a role, add at least one negative access-control case — another user, a "
    "logged-out visitor or a lower role tries the same action or opens the record's URL/id directly, "
    "and must be refused without the data being shown or changed. For every free-text or file input, "
    "add one case with hostile input (script tag, quote, very long text) that must be stored and shown "
    "as plain text. Mark these test_type 'security' and trace them like the others "
    "(Covers: AC-n | Technique: Error Guessing (access control) / (injection)).")


def prompt_block(with_code: bool = False) -> str:
    """
    Render a concise, actionable ISTQB doctrine block for the generation prompt.

    Returns:
        str: A compact block naming the principles at play and instructing the
            model to apply the five test-design techniques — with BVA spelled out
            so numeric/pagination boundaries are covered every time.
    """
    lines = ["ISTQB TEST-DESIGN DISCIPLINE — apply these techniques systematically, "
             "not by luck. They decide WHICH cases to generate:"]
    for t in TECHNIQUES:
        lines.append(f"- {t['name']}: {t['what']} → {t['apply']}")
    lines.append("Guiding principles: derive the oracle from the spec (a pass never proves "
                 "correctness); spend cases where defects cluster (authz, money, state, "
                 "computed values); every boundary and every invalid class gets a case.")
    for t in EXPERIENCE_TECHNIQUES + COMBINATORIAL:
        lines.append(f"- {t['name']}: {t['apply']}")
    if with_code:
        for t in WHITE_BOX:
            lines.append(f"- {t['name']}: {t['apply']}")
    lines.append("Test types to consider (not only functional): "
                 + "; ".join(f"{t['name']} ({t['apply']})" for t in TEST_TYPES))
    lines.append(RISK_RULE)
    lines.append(SECURITY_FROM_STORY_RULE)
    lines.append(TRACEABILITY_RULE)
    return "\n".join(lines)
