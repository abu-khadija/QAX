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
    assert "NOT TESTED" in text and "bola: needs two distinct users" in text
    assert "xxe" not in text                      # n/a stays out of the console (it's in the JSON)


def test_no_findings_and_no_gaps_keeps_the_clean_message():
    from cli.scan_render import render_scan_result
    from services.results import OrchestratorResult
    text = render_scan_result(OrchestratorResult())
    assert "held up to the scan" in text and "NOT TESTED" not in text
