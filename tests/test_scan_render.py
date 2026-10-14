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
