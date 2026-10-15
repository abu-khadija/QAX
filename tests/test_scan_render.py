"""S8: the extracted scan renderer separates deterministic CONFIRMED from AI leads (B1)."""
from cli.scan_render import render_scan_result
from services.results import OrchestratorResult


def test_render_splits_deterministic_and_ai_and_lists_titles():
    r = OrchestratorResult(confirmed=[
        {"source": "probe", "probe": "bola", "severity": "P1", "title": "BOLA /a", "target": "/a"},
        {"source": "coverage", "severity": "P1", "title": "auth bypass", "target": "GET /b"},
        {"source": "test", "severity": "P1", "title": "LLM SQLi", "target": "/c"},
    ])
    out = render_scan_result(r)
    assert "CONFIRMED (deterministic, 0-FP): 2" in out and "AI leads (may vary): 1" in out
    assert "BOLA /a" in out and "auth bypass" in out
    assert "LLM SQLi" in out and "⟨AI lead" in out   # AI finding is tagged, not mixed in


def test_render_no_findings():
    out = render_scan_result(OrchestratorResult(confirmed=[]))
    assert "No findings" in out


# ── Not-tested ledger rendering ("absence of a finding != a pass") ──────────────

def test_no_findings_with_gaps_is_not_a_clean_bill_of_health():
    from cli.scan_render import render_scan_result
    from services.results import OrchestratorResult
    r = OrchestratorResult(not_tested=[
        {"probe": "bola", "reason": "needs two distinct users", "kind": "missing-prerequisite"},
        {"probe": "xxe", "reason": "no xml endpoint", "kind": "not-applicable"}])
    text = render_scan_result(r)
    assert "NOT a clean bill of health" in text
    assert "held up to the scan" not in text
    assert "NOT TESTED" in text and "bola: needs two distinct users" in text      # (no `needs` key → listed alone)
    assert "xxe" not in text                      # n/a stays out of the console (it's in the JSON)


def test_no_findings_and_no_gaps_keeps_the_clean_message():
    from cli.scan_render import render_scan_result
    from services.results import OrchestratorResult
    text = render_scan_result(OrchestratorResult())
    assert "held up to the scan" in text and "NOT TESTED" not in text


def test_gaps_with_the_same_prerequisite_are_grouped_into_one_decision():
    from cli.scan_render import render_scan_result
    from services.results import OrchestratorResult
    r = OrchestratorResult(not_tested=[
        {"probe": "bfla", "reason": "r1", "kind": "missing-prerequisite", "needs": "token_b"},
        {"probe": "bola", "reason": "r2", "kind": "missing-prerequisite", "needs": "token_b"},
        {"probe": "access-revocation", "reason": "r3", "kind": "missing-prerequisite", "needs": "token_b"},
        {"probe": "jwt-flaws", "reason": "no token", "kind": "missing-prerequisite", "needs": "auth-token"}])
    text = render_scan_result(r)
    assert "access-revocation, bfla, bola" in text                 # ONE line for the three
    assert text.count("--token-b") == 1                            # the ask appears once
    assert "jwt-flaws" in text and "--token (QAX could not self-register" in text
    assert "r1" not in text and "r2" not in text                   # no per-probe repetition


def test_render_lists_records_left_on_the_target():
    from cli.scan_render import render_scan_result
    from services.results import OrchestratorResult
    r = OrchestratorResult(created_records=5, left_on_target=[
        {"collection": "/api/v1/user/repos", "key": "qax_repo1", "why": "no DELETE endpoint for this collection"},
        {"collection": "/orgs", "key": None, "why": "delete was refused (HTTP 403)"}])
    text = render_scan_result(r)
    assert "LEFT ON THE TARGET" in text and "created 5 test record(s) and could not remove 2" in text
    assert "/api/v1/user/repos qax_repo1 — no DELETE endpoint" in text and "/orgs — delete was refused" in text


def test_render_caps_a_long_leftover_list_and_is_silent_when_nothing_is_left():
    from cli.scan_render import render_scan_result
    from services.results import OrchestratorResult
    many = [{"collection": "/c", "key": f"k{i}", "why": "w"} for i in range(20)]
    assert "and 8 more" in render_scan_result(OrchestratorResult(left_on_target=many))
    assert "LEFT ON THE TARGET" not in render_scan_result(OrchestratorResult())
