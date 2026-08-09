"""
test_orchestrator.py — OrchestratorService coverage logic
=========================================================
The orchestration is heavy (composes every service), so these tests target the
part that carries the phase's value: the coverage loop that guarantees the
pagination boundary is tested rather than left to a lucky generation.

Run with:
    pytest tests/test_orchestrator.py -v
"""

from services.orchestrator_service import OrchestratorService
from services.results import AppModel


def _paginated_model() -> AppModel:
    return AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/todos", "response_fields": ["items", "total", "total_pages"]},
        {"method": "POST", "path": "/todos", "request_fields": ["title"]},
    ])


# ── endpoint discovery ───────────────────────────────────────────────────────

def test_finds_paginated_endpoint_and_create_sibling():
    pag = OrchestratorService._paginated_endpoint(_paginated_model())
    assert pag["list"] == "/todos"
    assert pag["create"] == "/todos"
    assert pag["pages_field"] == "total_pages"   # derived from the response fields


def test_no_pagination_when_absent():
    m = AppModel(root="/x", endpoints=[{"method": "GET", "path": "/health",
                                        "response_fields": ["status"]}])
    assert OrchestratorService._paginated_endpoint(m) is None


# ── the boundary-test detector (aligned counts must NOT count) ───────────────

def _pag():
    return {"list": "/todos", "create": "/todos"}


def _boundary_test(create_count: int, page_size: int) -> dict:
    steps = [{"step_number": i + 1, "method": "POST", "path": "/todos",
              "body": {"title": f"t{i}"}, "expected_status": 201} for i in range(create_count)]
    steps.append({"step_number": create_count + 1, "method": "GET",
                  "path": f"/todos?page_size={page_size}", "expected_status": 200,
                  "expect_json": {"total_pages": 2}})
    return {"api_steps": steps}


def test_aligned_count_is_not_boundary_coverage():
    # 2 items with page_size 2 → total_pages same for floor and ceil → hides the bug.
    tests = [_boundary_test(create_count=2, page_size=2)]
    assert OrchestratorService._has_pagination_boundary_test(tests, _pag()) is False


def test_non_multiple_count_is_boundary_coverage():
    tests = [_boundary_test(create_count=3, page_size=2)]
    assert OrchestratorService._has_pagination_boundary_test(tests, _pag()) is True


def test_missing_total_pages_assertion_is_not_coverage():
    steps = [{"step_number": 1, "method": "POST", "path": "/todos", "expected_status": 201},
             {"step_number": 2, "method": "GET", "path": "/todos?page_size=2",
              "expected_status": 200}]  # no expect_json total_pages
    assert OrchestratorService._has_pagination_boundary_test([{"api_steps": steps}], _pag()) is False


# ── the deterministic template that fills the gap ────────────────────────────

def test_template_creates_non_multiple_count_and_asserts_ceil():
    from services.context import QAXContext
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    tpl = svc._pagination_boundary_template(_pag(), _paginated_model())
    assert tpl is not None
    creates = [s for s in tpl["api_steps"] if s["method"] == "POST" and s["path"] == "/todos"]
    assert len(creates) == 3                      # 3 is not a multiple of page_size 2
    get = next(s for s in tpl["api_steps"] if s["method"] == "GET")
    assert "page_size=2" in get["path"]
    assert get["expect_json"] == {"total": 3, "total_pages": 2}   # ceil(3/2)=2
    # and the template itself satisfies the coverage check
    assert OrchestratorService._has_pagination_boundary_test([tpl], _pag()) is True


def test_measure_and_fill_injects_template_when_uncovered():
    from services.context import QAXContext
    from services.progress import NullProgressReporter
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    tests: list[dict] = []           # generation produced nothing for pagination
    coverage = svc._measure_and_fill(_paginated_model(), tests, NullProgressReporter())
    goal = "pagination-boundary:/todos"
    assert coverage.get(goal) == "template"
    assert any(t.get("attack") == "coverage-template" for t in tests)   # injected
