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


# ── item 1: template- and investigator-confirmed bugs expand their family ──────

def _sweep_probes(monkeypatch, ran: list):
    """Patch the family probes to record which classes get swept (no network)."""
    import execution.probes as P
    from services.results import ProbeFinding

    def mk(cid):
        def fn(model, base_url, *, session=None, auth=None, limit=4):
            ran.append(cid)
            return [ProbeFinding(probe=cid, title=f"{cid} /other/{{id}}",
                                 target="/other/{id}", exploited=True, severity="P1")]
        return fn
    for cid, name in (("bola", "bola_probe"), ("sqli", "sqli_probe"),
                      ("injection", "injection_probe"), ("path-traversal", "path_traversal_probe")):
        monkeypatch.setattr(P, name, mk(cid))


def test_expand_families_from_template_confirmed_idor(monkeypatch):
    """A COV-IDOR template bug (no `probe` field) must sweep the bola family."""
    from services.context import QAXContext
    ran: list = []
    _sweep_probes(monkeypatch, ran)
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    confirmed = [{"source": "coverage", "test_case_id": "COV-IDOR-0001",
                  "title": "Coverage: object-level authorization", "target": "GET /tasks/{id}"}]
    out = svc._expand_families(confirmed, AppModel(root="/x"), "http://x")
    assert ran == ["bola"]                               # classified from the template id
    assert out and out[0]["target"] == "/other/{id}"     # sibling found


def test_expand_families_from_investigator_sql_injection(monkeypatch):
    """An Investigator-confirmed 'SQL injection' (source test, no probe) sweeps sqli."""
    from services.context import QAXContext
    ran: list = []
    _sweep_probes(monkeypatch, ran)
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    confirmed = [{"source": "test", "title": "SQL injection in GET /search", "target": "/search"}]
    out = svc._expand_families(confirmed, AppModel(root="/x"), "http://x")
    assert ran == ["sqli"] and out                       # classified from the title


def test_family_class_prefers_sqli_over_generic_injection():
    assert OrchestratorService._family_class({"title": "SQL injection in /x"}) == "sqli"
    assert OrchestratorService._family_class({"title": "OS command injection"}) == "injection"
    assert OrchestratorService._family_class({"title": "accepts an empty title"}) is None


# ── item 2: round 2 skips suspects round 1 already cleared (app held) ───────────

def _round1(exec_results):
    class Ex:
        results = exec_results
    return Ex()


def test_unresolved_suspects_drops_cleared_but_keeps_setup_broken():
    from services.results import Finding
    findings = [Finding(title="IDOR tasks", where="GET /tasks/{id}"),      # attacked, app held
                Finding(title="IDOR projects", where="GET /projects/{id}")]  # test broke on setup
    tests = [
        {"test_case_id": "RT-1", "api_steps": [
            {"step_number": 1, "path": "/login"}, {"step_number": 2, "path": "/tasks/{id}"}]},
        {"test_case_id": "RT-2", "api_steps": [
            {"step_number": 1, "path": "/login"}, {"step_number": 2, "path": "/projects/{id}"}]},
    ]
    execution = _round1([
        # RT-1 reached its final step (2) → /tasks/{id} is cleared
        {"test_case_id": "RT-1", "steps": [
            {"step_number": 1, "status": "PASS"}, {"step_number": 2, "status": "PASS"}]},
        # RT-2 errored at setup (step 1) → final step never ran → NOT cleared
        {"test_case_id": "RT-2", "steps": [{"step_number": 1, "status": "ERROR"}]},
    ])
    out = OrchestratorService._unresolved_suspects(
        findings, confirmed=[], execution=execution, tests=tests)
    wheres = {f.where for f in out}
    assert "GET /tasks/{id}" not in wheres        # cleared in round 1 → don't re-attack
    assert "GET /projects/{id}" in wheres         # setup-broken → still worth round 2


def test_unresolved_suspects_no_execution_keeps_all_unconfirmed():
    from services.results import Finding
    findings = [Finding(title="x", where="/a"), Finding(title="y", where="/b")]
    out = OrchestratorService._unresolved_suspects(findings, confirmed=[])
    assert {f.where for f in out} == {"/a", "/b"}  # no round-1 data → nothing cleared


# ── item 7: the A/B harness proves recall up, cost down, FP flat ───────────────

def test_orchestrator_ab_shows_recall_up_cost_down_fp_flat():
    from evals.orchestrator_ab import measure
    m = measure()
    r, c, fp = (m["recall_classes_expanded"], m["round2_suspects_reattacked"],
                m["unproven_findings_added"])
    assert r["after"] > r["before"]   # family expansion reaches more classes
    assert c["after"] < c["before"]   # round 2 re-attacks fewer suspects
    assert fp["after"] == fp["before"] == 0   # no unproven findings introduced


# ── item 4: richer router ranking (suspects + churn) + endpoint budget ─────────

def test_router_score_boosts_files_with_detector_suspects():
    from services.results import Finding
    m = AppModel(root="/x", endpoints=[{"method": "GET", "path": "/orders/{id}"}])
    det = [Finding(title="IDOR", where="routers/orders.py:42"),
           Finding(title="missing authz", where="routers/orders.py:60")]
    base = OrchestratorService._router_score("routers/orders.py", m)
    boosted = OrchestratorService._router_score("routers/orders.py", m, detection=det)
    assert boosted > base            # suspects pointing at the file raise its rank


def test_router_score_boosts_churn():
    m = AppModel(root="/x", endpoints=[])
    assert (OrchestratorService._router_score("routers/orders.py", m, churn=5)
            > OrchestratorService._router_score("routers/orders.py", m, churn=0))


def test_select_within_budget_spends_endpoints_not_flat_file_count():
    m = AppModel(root="/x", endpoints=(
        [{"method": "GET", "path": f"/orders/{i}"} for i in range(30)]     # orders: dense
        + [{"method": "GET", "path": "/health"}]))                          # health: 1
    ranked = ["routers/orders.py", "routers/health.py", "routers/about.py"]
    chosen, skipped = OrchestratorService._select_within_budget(ranked, m, max_files=4, max_endpoints=10)
    assert "routers/orders.py" in chosen          # highest-cost router still taken (always keep 1)
    assert "routers/about.py" in skipped          # budget spent → later routers skipped
    assert len(chosen) < len(ranked)


def test_select_within_budget_respects_file_ceiling():
    m = AppModel(root="/x", endpoints=[])
    ranked = [f"r{i}.py" for i in range(10)]
    chosen, _ = OrchestratorService._select_within_budget(ranked, m, max_files=3, max_endpoints=999)
    assert len(chosen) == 3                        # file ceiling caps it even with budget to spare


# ── item 5: no-router fallback generates from the endpoint surface ─────────────

def test_group_endpoints_by_segment_ranks_by_risk():
    m = AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/api/orders", "auth_required": True},
        {"method": "GET", "path": "/api/orders/{id}", "auth_required": True},
        {"method": "GET", "path": "/health"},
    ])
    groups = OrchestratorService._group_endpoints(m)
    keys = [k for k, _ in groups]
    assert keys[0] == "orders"         # the /api mount prefix is skipped → keyed by resource
    # a model that groups by file keeps distinct keys
    m2 = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/orders/{id}", "file": "orders.py"},
        {"method": "GET", "path": "/health", "file": "health.py"}])
    g2 = dict(OrchestratorService._group_endpoints(m2))
    assert set(g2) == {"orders.py", "health.py"}


def test_endpoints_as_spec_lists_methods_and_auth():
    spec = OrchestratorService._endpoints_as_spec("orders", [
        {"method": "post", "path": "/orders", "auth_required": True,
         "request_fields": ["item_id", "qty"]}])
    assert "POST /orders" in spec and "[auth required]" in spec and "item_id" in spec


def test_generate_from_endpoints_budgets_and_calls_generation(monkeypatch):
    from services.context import QAXContext
    import services.generation_service as G
    from services.results import GenerationResult
    calls = []

    specs = {}

    class FakeGS:
        def __init__(self, *a, **k): pass
        def generate(self, source, options=None):
            calls.append(source)
            specs[source] = options.content
            return GenerationResult(source=source, test_cases=[{"test_case_id": source}])
    monkeypatch.setattr(G, "GenerationService", FakeGS)

    m = AppModel(root="/x", endpoints=(
        [{"method": "GET", "path": f"/orders/{i}", "file": "orders.py"} for i in range(30)]
        + [{"method": "GET", "path": "/health", "file": "health.py"}]))
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    from services.progress import NullProgressReporter
    tests, covered = svc._generate_from_endpoints(m, max_files=4, max_endpoints=10, prog=NullProgressReporter())
    # Breadth-first: the dense group is CAPPED (≤5 of its 30), leaving budget for health.
    assert covered == ["orders.py", "health.py"]
    assert specs["endpoints://orders.py"].count("Endpoint: ") == 5
    assert tests and tests[0]["test_case_id"].startswith("endpoints://")


# Gitea follow-up (deep scan): every path under /api/v1 fell into one "api" group of
# 537 ops → one oversized prompt → truncated → only /admin/* covered.

def test_group_endpoints_skips_api_and_version_prefixes():
    m = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/api/v1/repos/{owner}/{repo}"},
        {"method": "GET", "path": "/api/v1/admin/users"},
        {"method": "GET", "path": "/rest/v2.1/orgs"},
    ])
    assert set(dict(OrchestratorService._group_endpoints(m))) == {"repos", "admin", "orgs"}


def test_budget_groups_never_sends_one_huge_group_whole():
    big = [{"method": "GET", "path": f"/repos/{i}"} for i in range(500)]
    ranked = [("repos", big), ("admin", [{"method": "GET", "path": "/admin/x"}])]
    chosen = OrchestratorService._budget_groups(ranked, max_files=4, max_endpoints=40)
    assert len(chosen[0][1]) == 10            # 40 // 4 per group, not 500
    assert [k for k, _ in chosen] == ["repos", "admin"]
    assert sum(len(e) for _, e in chosen) <= 40


def test_budget_groups_prefers_risky_endpoints_within_a_group():
    eps = ([{"method": "GET", "path": f"/r/{i}"} for i in range(10)]
           + [{"method": "DELETE", "path": "/r/{id}", "auth_required": True}])
    chosen = OrchestratorService._budget_groups([("r", eps)], max_files=4, max_endpoints=20)
    assert chosen[0][1][0]["method"] == "DELETE"


def test_endpoints_as_spec_supplied_forbids_invented_login():
    eps = [{"method": "GET", "path": "/admin/users", "auth_required": True}]
    self_reg = OrchestratorService._endpoints_as_spec("admin", eps)
    supplied = OrchestratorService._endpoints_as_spec("admin", eps, supplied=True)
    assert "register then log in" in self_reg
    assert "do NOT write any register, login" in supplied and '"token_b"' in supplied
    assert "register then log in" not in supplied


def test_normalize_supplied_auth_drops_login_and_maps_invented_tokens():
    tests = [{"test_case_id": "G1", "api_steps": [
        {"step_number": 1, "method": "POST", "path": "/api/v1/user/login", "body": {}},
        {"step_number": 2, "method": "GET", "path": "/api/v1/admin/users", "use_auth": "admin_token"},
        {"step_number": 3, "method": "GET", "path": "/api/v1/repos/a/b", "use_auth": "attacker_token"},
        {"step_number": 4, "method": "GET", "path": "/api/v1/admin/users"},           # no-token case kept
    ]}]
    out = OrchestratorService._normalize_supplied_auth(tests)
    steps = out[0]["api_steps"]
    assert [s["path"] for s in steps] == ["/api/v1/admin/users", "/api/v1/repos/a/b", "/api/v1/admin/users"]
    assert [s.get("use_auth") for s in steps] == ["token", "token_b", None]
    assert [s["step_number"] for s in steps] == [1, 2, 3]


def test_normalize_supplied_auth_drops_test_that_was_only_login():
    tests = [{"test_case_id": "G2", "api_steps": [
        {"step_number": 1, "method": "POST", "path": "/auth/register"},
        {"step_number": 2, "method": "POST", "path": "/auth/login"}]}]
    assert OrchestratorService._normalize_supplied_auth(tests) == []


# ── item 3: setup repair re-runs broken tests with a known-good preamble ───────

def test_setup_broken_tests_finds_only_pre_attack_failures():
    tests = [
        {"test_case_id": "A", "api_steps": [{"step_number": 1}, {"step_number": 2}]},  # broke at setup
        {"test_case_id": "B", "api_steps": [{"step_number": 1}, {"step_number": 2}]},  # attack landed
    ]
    class Ex:
        results = [
            {"test_case_id": "A", "steps": [{"step_number": 1, "status": "ERROR"}]},          # final (2) never ran
            {"test_case_id": "B", "steps": [{"step_number": 1, "status": "PASS"},
                                             {"step_number": 2, "status": "FAIL"}]},           # final ran
        ]
    broken = OrchestratorService._setup_broken_tests(Ex(), tests)
    assert [t["test_case_id"] for t in broken] == ["A"]


def test_attack_tail_strips_leading_auth_steps():
    recipe = {"register_path": "/auth/register", "login_path": "/auth/login"}
    steps = [{"path": "/auth/register"}, {"path": "/auth/login"},
             {"path": "/orders/1"}, {"path": "/orders/2"}]
    tail = OrchestratorService._attack_tail(steps, recipe)
    assert [s["path"] for s in tail] == ["/orders/1", "/orders/2"]


def test_repair_setup_prepends_good_preamble_and_attaches_token():
    from services.context import QAXContext
    m = AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/auth/register", "request_fields": ["email", "password"]},
        {"method": "POST", "path": "/auth/login", "request_fields": ["email", "password"]},
        {"method": "GET", "path": "/orders/{id}"},
    ])
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    broken = [{"test_case_id": "RT-9", "title": "IDOR orders",
               "api_steps": [{"step_number": 1, "method": "POST", "path": "/auth/login", "body": {}},
                             {"step_number": 2, "method": "GET", "path": "/orders/7"}]}]
    out = svc._repair_setup(broken, m)
    assert len(out) == 1
    steps = out[0]["api_steps"]
    assert steps[0]["path"] == "/auth/register" and steps[1]["path"] == "/auth/login"  # good preamble
    assert "save" in steps[1]                                   # token saved
    attack = steps[-1]
    assert attack["path"] == "/orders/7" and attack["use_auth"] == "token"   # token attached
    assert out[0]["test_case_id"].endswith("-rep")


def test_repair_setup_skips_test_with_no_attack_tail():
    from services.context import QAXContext
    m = AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/auth/register", "request_fields": ["email", "password"]},
        {"method": "POST", "path": "/auth/login", "request_fields": ["email", "password"]}])
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    broken = [{"test_case_id": "RT-x", "api_steps": [{"step_number": 1, "path": "/auth/login"}]}]
    assert svc._repair_setup(broken, m) == []


# Gitea follow-up: on a token-only API, a supplied token IS the good preamble.

def _repair_model():
    return AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/auth/register", "request_fields": ["email", "password"]},
        {"method": "POST", "path": "/auth/login", "request_fields": ["email", "password"]},
        {"method": "GET", "path": "/orders/{id}"},
    ])


def test_repair_setup_supplied_strips_auth_and_adds_no_preamble():
    from services.context import QAXContext
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    broken = [{"test_case_id": "RT-9", "title": "IDOR orders",
               "api_steps": [{"step_number": 1, "method": "POST", "path": "/auth/login", "body": {}},
                             {"step_number": 2, "method": "GET", "path": "/orders/7"}]}]
    out = svc._repair_setup(broken, _repair_model(), auth={"token": "T"})
    assert len(out) == 1
    steps = out[0]["api_steps"]
    assert [s["path"] for s in steps] == ["/orders/7"]          # no register/login at all
    assert steps[0]["step_number"] == 1 and steps[0]["use_auth"] == "token"


def test_repair_setup_supplied_skips_coverage_templates():
    from services.context import QAXContext
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    broken = [{"test_case_id": "COV-IDOR-0001", "attack": "coverage-template",
               "api_steps": [{"step_number": 1, "method": "POST", "path": "/orders", "use_auth": "token_a"},
                             {"step_number": 2, "method": "GET", "path": "/orders/{{rid}}", "use_auth": "token_b"}]}]
    assert svc._repair_setup(broken, _repair_model(), auth={"token": "T", "token_b": "B"}) == []


def test_repair_setup_supplied_skips_identical_rerun():
    from services.context import QAXContext
    svc = OrchestratorService(QAXContext(anthropic_key="k", project=""))
    # Already authenticated, no register/login to strip → re-running changes nothing.
    broken = [{"test_case_id": "RT-2", "api_steps": [
        {"step_number": 1, "method": "POST", "path": "/orders", "use_auth": "token"},
        {"step_number": 2, "method": "GET", "path": "/orders/1", "use_auth": "token"}]}]
    assert svc._repair_setup(broken, _repair_model(), auth={"token": "T"}) == []


# ── B1: deterministic CONFIRMED vs non-deterministic AI leads, stable ordering ──

def test_result_splits_deterministic_from_ai_leads():
    from services.results import OrchestratorResult
    r = OrchestratorResult(confirmed=[
        {"source": "test", "severity": "P1", "title": "LLM says SQLi", "target": "/x"},
        {"source": "probe", "probe": "bola", "severity": "P1", "title": "BOLA", "target": "/a"},
        {"source": "coverage", "severity": "P1", "title": "auth bypass", "target": "GET /b"},
        {"source": "ui", "severity": "P2", "title": "console error"},
    ])
    det = {b["title"] for b in r.confirmed_deterministic}
    ai = {b["title"] for b in r.leads_ai}
    assert det == {"BOLA", "auth bypass"}          # probe + coverage only
    assert ai == {"LLM says SQLi", "console error"}  # test + ui are leads


def test_deterministic_block_is_order_stable():
    from services.results import OrchestratorResult
    a = [{"source": "probe", "probe": "bola", "severity": "P1", "title": "B", "target": "/b"},
         {"source": "probe", "probe": "sqli", "severity": "P1", "title": "A", "target": "/a"},
         {"source": "coverage", "severity": "P2", "title": "C", "target": "/c"}]
    r1 = OrchestratorResult(confirmed=list(a))
    r2 = OrchestratorResult(confirmed=list(reversed(a)))   # same findings, different input order
    k1 = [OrchestratorResult._confirmed_key(b) for b in r1.confirmed_deterministic]
    k2 = [OrchestratorResult._confirmed_key(b) for b in r2.confirmed_deterministic]
    assert k1 == k2                                 # canonical order → reproducible block


# ── B2: silent-pipeline-gap contract ───────────────────────────────────────────

def test_silent_legs_flags_attempted_generation_that_produced_nothing():
    from services.results import OrchestratorResult
    r = OrchestratorResult(legs={"functional_attempted": True, "functional_generated": 0,
                                 "templates_injected": 3, "tests_executed": 3})
    assert any("functional-generation" in s for s in r.silent_legs())


def test_silent_legs_quiet_when_generation_not_attempted():
    from services.results import OrchestratorResult
    r = OrchestratorResult(legs={"functional_attempted": False, "functional_generated": 0,
                                 "templates_injected": 3, "tests_executed": 3})
    assert r.silent_legs() == []            # quick scan: generation legitimately not attempted


def test_silent_legs_flags_built_but_unexecuted_tests():
    from services.results import OrchestratorResult
    r = OrchestratorResult(legs={"functional_attempted": False, "functional_generated": 0,
                                 "templates_injected": 4, "tests_executed": 0})
    assert any("execution" in s for s in r.silent_legs())


# Gitea deep-scan follow-up: a setup step that expects success but lost its token.

def test_normalize_supplied_auth_gives_a_bare_setup_step_the_seeded_token():
    tests = [{"test_case_id": "G3", "api_steps": [
        {"step_number": 1, "method": "POST", "path": "/api/v1/admin/users", "expected_status": 201},
        {"step_number": 2, "method": "GET", "path": "/api/v1/admin/users", "use_auth": "token_b",
         "expected_status": [403]}]}]
    steps = OrchestratorService._normalize_supplied_auth(tests)[0]["api_steps"]
    assert steps[0]["use_auth"] == "token"          # the create can only have meant to succeed
    assert steps[1]["use_auth"] == "token_b"        # untouched


def test_normalize_supplied_auth_leaves_the_final_bare_step_as_the_no_token_probe():
    tests = [{"test_case_id": "G4", "api_steps": [
        {"step_number": 1, "method": "GET", "path": "/api/v1/repos/a/b", "expected_status": 401}]}]
    steps = OrchestratorService._normalize_supplied_auth(tests)[0]["api_steps"]
    assert "use_auth" not in steps[0]               # deliberate unauthenticated request


def test_normalize_supplied_auth_does_not_touch_bare_setup_steps_that_expect_rejection():
    tests = [{"test_case_id": "G5", "api_steps": [
        {"step_number": 1, "method": "POST", "path": "/x", "expected_status": [401, 403]},
        {"step_number": 2, "method": "GET", "path": "/x/1", "use_auth": "token", "expected_status": 200}]}]
    assert "use_auth" not in OrchestratorService._normalize_supplied_auth(tests)[0]["api_steps"][0]


def test_expects_success_parses_ints_strings_and_lists():
    f = OrchestratorService._expects_success
    assert f({"expected_status": 201}) and f({"expected_status": "200"}) and f({"expected_status": [200, 204]})
    assert not f({"expected_status": [200, 404]}) and not f({"expected_status": 401}) and not f({})
