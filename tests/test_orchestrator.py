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


def test_failed_template_at_assertion_is_a_proven_bug():
    tc = {"test_case_id": "COV-VAL-0001", "title": "Coverage: POST /todos rejects empty 'title'",
          "attack": "coverage-template",
          "api_steps": [{"step_number": 1}, {"step_number": 2}, {"step_number": 3}]}

    class Ex:
        results = [{"test_case_id": "COV-VAL-0001",
                    "title": "Coverage: POST /todos rejects empty 'title'", "status": "FAIL",
                    "steps": [{"step_number": 3, "status": "FAIL", "response_code": 201,
                               "error": "Expected status [422, 400], got 201"}]}]
    out = OrchestratorService._confirmed_from_templates(Ex(), [tc], already=[])
    assert len(out) == 1
    assert out[0]["severity"] == "P3" and out[0]["source"] == "coverage"


def test_404_at_assertion_is_not_a_bug():
    # auth-bypass template hit a wrong/nonexistent path → 404, NOT a real bypass.
    tc = {"test_case_id": "COV-AUTH-0001", "title": "Coverage: GET /users/me requires auth",
          "attack": "coverage-template", "api_steps": [{"step_number": 1}]}

    class Ex:
        results = [{"test_case_id": "COV-AUTH-0001", "title": "Coverage: GET /users/me requires auth",
                    "status": "FAIL",
                    "steps": [{"step_number": 1, "status": "FAIL", "response_code": 404,
                               "error": "Expected status [401, 403], got 404"}]}]
    assert OrchestratorService._confirmed_from_templates(Ex(), [tc], already=[]) == []


def test_2xx_at_assertion_is_a_real_bug():
    # auth-bypass got a 200 with no token → a genuine bypass.
    tc = {"test_case_id": "COV-AUTH-0002", "title": "Coverage: GET /secret requires auth",
          "attack": "coverage-template", "api_steps": [{"step_number": 1}]}

    class Ex:
        results = [{"test_case_id": "COV-AUTH-0002", "title": "Coverage: GET /secret requires auth",
                    "status": "FAIL",
                    "steps": [{"step_number": 1, "status": "FAIL", "response_code": 200,
                               "error": "Expected status [401, 403], got 200"}]}]
    out = OrchestratorService._confirmed_from_templates(Ex(), [tc], already=[])
    assert len(out) == 1 and out[0]["severity"] == "P1"


def test_failed_template_at_setup_is_not_a_bug():
    tc = {"test_case_id": "COV-IDOR-0001", "title": "Coverage: GET /todos/{id} blocks another user",
          "attack": "coverage-template",
          "api_steps": [{"step_number": 1}, {"step_number": 2}, {"step_number": 6}]}

    class Ex:
        results = [{"test_case_id": "COV-IDOR-0001",
                    "title": "Coverage: GET /todos/{id} blocks another user", "status": "FAIL",
                    "steps": [{"step_number": 2, "status": "FAIL", "error": "login 401"}]}]
    # failed at step 2 (login setup), not the assertion (step 6) → not a proven bug
    assert OrchestratorService._confirmed_from_templates(Ex(), [tc], already=[]) == []


def test_measure_and_fill_injects_template_when_uncovered():
    from services.context import QAXContext
    from services.progress import NullProgressReporter
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    tests: list[dict] = []           # generation produced nothing for pagination
    coverage = svc._measure_and_fill(_paginated_model(), tests, NullProgressReporter())
    goal = "pagination-boundary:/todos"
    assert coverage.get(goal) == "template"
    assert any(t.get("attack") == "coverage-template" for t in tests)   # injected


# ── functional-generation budget: rank routers by risk (coverage honesty) ──────

def test_router_score_ranks_sensitive_over_trivial():
    m = AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/payments/charge", "auth_required": True},
        {"method": "GET", "path": "/payments/{id}", "auth_required": True},
        {"method": "GET", "path": "/health", "auth_required": False},
    ])
    s = {f: OrchestratorService._router_score(f, m) for f in
         ("routers/payments.py", "routers/health.py", "routers/about.py")}
    assert s["routers/payments.py"] > s["routers/health.py"]
    assert s["routers/payments.py"] > s["routers/about.py"]
    assert s["routers/health.py"] < 0 and s["routers/about.py"] < 0   # low-risk demoted


# ── family expansion: confirmed class → sweep siblings (never from suspects) ────

def test_expand_families_sweeps_confirmed_class_and_dedupes(monkeypatch):
    import execution.probes as P
    from services.context import QAXContext
    from services.results import ProbeFinding
    called = []

    def fake_bola(model, base_url, *, session=None, auth=None, limit=4):
        called.append(("bola", limit))
        return [ProbeFinding(probe="bola", title="BOLA /tasks/{id}", target="/tasks/{id}",
                             exploited=True, severity="P1"),
                ProbeFinding(probe="bola", title="BOLA /projects/{id}", target="/projects/{id}",
                             exploited=True, severity="P1")]

    def fake_sqli(*a, **k):
        called.append(("sqli", None))
        return []

    monkeypatch.setattr(P, "bola_probe", fake_bola)
    monkeypatch.setattr(P, "sqli_probe", fake_sqli)
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    confirmed = [{"probe": "bola", "target": "/tasks/{id}", "title": "BOLA"}]   # bola is confirmed, sqli is NOT
    out = svc._expand_families(confirmed, AppModel(root="/x"), "http://x")
    assert [c[0] for c in called] == ["bola"]          # only the confirmed class ran
    assert called[0][1] >= 50                            # cap lifted for the sweep
    assert len(out) == 1 and out[0]["target"] == "/projects/{id}"   # sibling only, known one deduped


def test_expand_families_noop_without_confirmed_family_class():
    from services.context import QAXContext
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    assert svc._expand_families([{"probe": "security-headers", "target": "/"}], AppModel(root="/x"), "http://x") == []


# ── round 2: re-attack suspects left unresolved (orphans + setup-broken) ────────

def test_unresolved_suspects_excludes_confirmed_ones():
    from services.results import Finding
    findings = [Finding(title="IDOR tasks", where="/tasks/{id}"),
                Finding(title="IDOR projects", where="/projects/{id}"),
                Finding(title="weak jwt", where="auth.py")]
    confirmed = [{"title": "Broken object-level authorization in GET /tasks/{id}", "target": "/tasks/{id}"}]
    out = OrchestratorService._unresolved_suspects(findings, confirmed)
    wheres = {f.where for f in out}
    assert "/tasks/{id}" not in wheres                         # resolved → dropped
    assert "/projects/{id}" in wheres and "auth.py" in wheres  # unresolved → re-attack


def test_unresolved_suspects_empty_when_all_resolved():
    from services.results import Finding
    assert OrchestratorService._unresolved_suspects(
        [Finding(title="x", where="/a")], [{"target": "/a", "title": "bug on /a"}]) == []
