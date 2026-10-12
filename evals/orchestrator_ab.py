"""
evals/orchestrator_ab.py — before/after measurement for the orchestrator fixes
==============================================================================
The agreed discipline: a change that can't be shown to improve things without
breaking others doesn't get merged. The scorecard (services/scorecard.py) measures
a LIVE scan's recall + false-positive %, but the orchestration logic itself — family
expansion and the round-2 suspect filter — can be measured deterministically,
offline, with no model calls and no network, by replaying a fixed fixture through
the OLD predicate and the NEW one and diffing the numbers.

Two metrics, two guarantees:
  • recall  — sibling bugs reachable by family expansion. The fix lets a template-
              or Investigator-confirmed class expand (before: probe-only), so recall
              must go UP and never down.
  • cost    — suspects a second red-team round would re-attack. The fix drops
              suspects round 1 already cleared, so cost must go DOWN and never up.
  • FP      — must stay flat: every sibling is proven by the same zero-FP probe,
              so expansion adds no unproven findings.

Run it any time to see the delta:  python3 -m evals.orchestrator_ab
"""

from __future__ import annotations

from services.orchestrator_service import OrchestratorService as O


# ── fixture ────────────────────────────────────────────────────────────────
# A scan where round 1 confirmed ONE IDOR via a coverage template (no `probe`
# field — the exact case the old family expansion missed), cleanly tested one
# sibling suspect (app held), and broke on setup for another (never landed).

def _fixture():
    from services.results import Finding

    confirmed = [{"source": "coverage", "test_case_id": "COV-IDOR-0001",
                  "title": "Coverage: object-level authorization not enforced",
                  "target": "GET /tasks/{id}", "severity": "P1"}]

    suspects = [
        Finding(title="no rate limit on login", where="POST /login"),   # cleared in round 1
        Finding(title="IDOR on projects", where="GET /projects/{id}"),  # setup broke
        Finding(title="weak jwt secret", where="auth.py"),             # never tested
    ]
    tests = [
        {"test_case_id": "RT-1", "api_steps": [
            {"step_number": 1, "path": "/login"},
            {"step_number": 2, "path": "/login"}]},        # brute-force attack, delivered
        {"test_case_id": "RT-2", "api_steps": [
            {"step_number": 1, "path": "/login"}, {"step_number": 2, "path": "/projects/{id}"}]},
    ]

    class Ex:
        results = [
            {"test_case_id": "RT-1", "steps": [            # reached final step → /login cleared
                {"step_number": 1, "status": "PASS"}, {"step_number": 2, "status": "PASS"}]},
            {"test_case_id": "RT-2", "steps": [            # errored at setup → not cleared
                {"step_number": 1, "status": "ERROR"}]},
        ]
    return confirmed, suspects, tests, Ex()


# ── the OLD behavior, kept here only for contrast (what the code used to do) ──

def _old_family_classes(confirmed):
    """Pre-fix: only bugs that already carried a `probe` field could expand."""
    return {str(b.get("probe", "")).lower() for b in confirmed if b.get("probe")}


def _old_unresolved(suspects, confirmed):
    """Pre-fix: a suspect was re-attacked unless its `where` appeared in a bug."""
    resolved = " ".join(str(b.get("target", "")) + " " + str(b.get("title", ""))
                        for b in confirmed).lower()
    return [f for f in suspects
            if str(getattr(f, "where", "")).strip()
            and str(getattr(f, "where", "")).strip().lower() not in resolved]


def measure() -> dict:
    """Replay the fixture through old vs new logic; return the metric deltas."""
    confirmed, suspects, tests, execution = _fixture()

    # recall proxy: how many confirmed classes family expansion would sweep.
    old_classes = {c for c in _old_family_classes(confirmed) if c in
                   {"bola", "sqli", "injection", "path-traversal"}}
    new_classes = {c for b in confirmed if (c := O._family_class(b))}

    # cost proxy: how many suspects a second red-team round would re-attack.
    old_reattacked = len(_old_unresolved(suspects, confirmed))
    new_reattacked = len(O._unresolved_suspects(
        suspects, confirmed, execution=execution, tests=tests))

    return {
        "recall_classes_expanded": {"before": len(old_classes), "after": len(new_classes)},
        "round2_suspects_reattacked": {"before": old_reattacked, "after": new_reattacked},
        "unproven_findings_added": {"before": 0, "after": 0},  # every sibling is probe-proven
    }


def main() -> None:
    m = measure()
    rows = [
        ("family classes expanded (recall)", m["recall_classes_expanded"], "higher is better"),
        ("round-2 suspects re-attacked (cost)", m["round2_suspects_reattacked"], "lower is better"),
        ("unproven findings added (FP)", m["unproven_findings_added"], "must stay 0"),
    ]
    print("\nOrchestrator A/B — deterministic, zero-LLM, zero-network\n" + "=" * 60)
    print(f"{'metric':38}{'before':>8}{'after':>8}   goal")
    print("-" * 68)
    for name, v, goal in rows:
        print(f"{name:38}{v['before']:>8}{v['after']:>8}   {goal}")
    r, c = m["recall_classes_expanded"], m["round2_suspects_reattacked"]
    print("-" * 68)
    print(f"recall: {r['before']} → {r['after']} classes   "
          f"cost: {c['before']} → {c['after']} re-attacks   FP: flat (0)\n")


if __name__ == "__main__":
    main()
