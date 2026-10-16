"""
test_coverage_goals.py — deterministic coverage templates + status-list matching
================================================================================
These lock in that the high-value security/validation classes are covered by
model-derived deterministic tests, and that a security test can accept any of
several secure statuses (403 OR 404).

Run with:
    pytest tests/test_coverage_goals.py -v
"""

from execution.api_runner import APIRunner
from services import coverage_goals as cg
from services.results import AppModel


def _model() -> AppModel:
    return AppModel(endpoints=[
        {"method": "POST", "path": "/auth/register", "request_fields": ["email", "password"]},
        {"method": "POST", "path": "/auth/login", "response_fields": ["access_token"]},
        {"method": "GET", "path": "/todos", "auth_required": True, "auth_explicit": True,
         "response_fields": ["items", "total", "total_pages"]},
        {"method": "POST", "path": "/todos", "auth_required": True, "request_fields": ["title"]},
        {"method": "GET", "path": "/todos/{todo_id}", "auth_required": True, "auth_explicit": True},
        {"method": "DELETE", "path": "/todos/{todo_id}", "auth_required": True},
    ])


# ── status-list matching in the runner ───────────────────────────────────────

def test_status_matches_single_and_list():
    assert APIRunner._status_matches(200, 200) is True
    assert APIRunner._status_matches(200, 404) is False
    assert APIRunner._status_matches([403, 404], 404) is True
    assert APIRunner._status_matches([403, 404], 200) is False


# ── auth-bypass ──────────────────────────────────────────────────────────────

def test_auth_bypass_covers_protected_endpoints_only():
    tpls = cg.auth_bypass_templates(_model())
    titles = " ".join(t["title"] for t in tpls)
    assert "GET /todos requires authentication" in titles
    assert "GET /todos/{todo_id} requires authentication" in titles
    # each calls with NO token and expects rejection
    for t in tpls:
        step = t["api_steps"][0]
        assert "use_auth" not in step
        assert step["expected_status"] == [401, 403]
    # public endpoints (register/login) are not included
    assert "/auth/register" not in titles


def test_auth_bypass_excludes_conventionally_public_endpoints():
    # A spec with global security marks even root/health/docs as auth_required;
    # asserting auth on those is a false positive, so they must be excluded.
    model = AppModel(endpoints=[
        {"method": "GET", "path": "/", "auth_required": True},
        {"method": "GET", "path": "/health", "auth_required": True},
        {"method": "GET", "path": "/api-docs", "auth_required": True},
        {"method": "GET", "path": "/openapi.json", "auth_required": True},
        {"method": "GET", "path": "/users/", "auth_required": True, "auth_explicit": True},
    ])
    titles = " ".join(t["title"] for t in cg.auth_bypass_templates(model))
    assert "/users/" in titles
    for public in (" / requires", "/health", "/api-docs", "/openapi.json"):
        assert public not in titles
    assert cg._is_conventionally_public("/") and cg._is_conventionally_public("/v1/health")
    assert not cg._is_conventionally_public("/api/users")


def test_auth_bypass_skips_global_only_auth_get_but_keeps_writes():
    # A GET whose auth is only INHERITED from a global spec security (auth_explicit
    # False) is a common public list read (drf-yasg marks everything) — asserting auth
    # there cries wolf (the real /api/articles FP). Writes are still asserted.
    model = AppModel(endpoints=[
        {"method": "GET", "path": "/articles", "auth_required": True},            # global-only → skip
        {"method": "GET", "path": "/user", "auth_required": True, "auth_explicit": True},  # explicit → assert
        {"method": "POST", "path": "/articles", "auth_required": True},            # write → assert
    ])
    titles = " ".join(t["title"] for t in cg.auth_bypass_templates(model))
    assert "GET /articles requires" not in titles          # public-list FP avoided
    assert "GET /user requires" in titles                  # explicitly protected GET still asserted
    assert "POST /articles requires" in titles             # writes still asserted


# ── IDOR (two named tokens, secure = 403 or 404) ─────────────────────────────

def test_idor_template_uses_two_users_and_secure_status():
    tpls = cg.idor_templates(_model())
    assert tpls, "expected IDOR templates for /todos/{todo_id}"
    steps = tpls[0]["api_steps"]
    saves = [s.get("save", {}) for s in steps]
    assert any("token_a" in s for s in saves) and any("token_b" in s for s in saves)
    # userA creates, userB attacks with the OTHER token and expects to be blocked
    attack = steps[-1]
    assert attack["use_auth"] == "token_b"
    assert attack["expected_status"] == [403, 404]
    assert "{{rid}}" in attack["path"]


# ── validation (empty required field must be rejected) ───────────────────────

def test_validation_template_sends_empty_required_field():
    tpls = cg.validation_templates(_model())
    assert tpls
    last = tpls[0]["api_steps"][-1]
    assert last["method"] == "POST" and last["path"] == "/todos"
    assert last["body"] == {"title": ""}
    assert last["expected_status"] == [422, 400]


def test_validation_template_no_auth_skips_login_setup():
    # A no-auth create endpoint: the template must post directly, with NO
    # register/login setup (which would 404 and skip the assertion).
    model = AppModel(endpoints=[
        {"method": "POST", "path": "/api/items", "request_fields": ["title"]},  # no auth_required
    ])
    tpls = cg.validation_templates(model)
    assert tpls
    steps = tpls[0]["api_steps"]
    assert len(steps) == 1  # no register+login prepended
    assert steps[0]["method"] == "POST" and steps[0]["path"] == "/api/items"
    assert steps[0]["body"] == {"title": ""} and "use_auth" not in steps[0]


def test_validation_template_with_auth_uses_named_token():
    tpls = cg.validation_templates(_model())  # /todos is auth_required
    steps = tpls[0]["api_steps"]
    assert len(steps) == 3  # register + login + assertion
    assert steps[-1]["use_auth"] == "token"


def test_all_templates_groups_applicable_goals():
    groups = cg.all_templates(_model())
    assert set(groups) == {"auth-bypass", "broken-authz", "validation"}
    assert all(isinstance(v, list) and v for v in groups.values())


def test_no_goals_when_nothing_applies():
    empty = AppModel(endpoints=[{"method": "GET", "path": "/health"}])
    assert cg.all_templates(empty) == {}


# ── supplied-token mode (Gitea, 2026-10-15): run the authenticated coverage ───
#    layer against apps QAX can't self-register into, by using supplied tokens.

def test_validation_template_supplied_skips_register_login():
    # With supplied=True the auth'd create posts directly with the seeded `token`
    # and prepends NO register/login (which would 404 on a token-only API).
    tpls = cg.validation_templates(_model(), supplied=True)
    steps = tpls[0]["api_steps"]
    assert len(steps) == 1
    assert steps[0]["method"] == "POST" and steps[0]["path"] == "/todos"
    assert steps[0]["use_auth"] == "token" and steps[0]["body"] == {"title": ""}


def test_idor_template_supplied_skips_registration():
    tpls = cg.idor_templates(_model(), supplied=True)
    assert tpls
    steps = tpls[0]["api_steps"]
    # create(token_a) + [GET only: listing control(token_b)] + attack(token_b) — no four register/login steps.
    assert len(steps) == 3 and steps[1].get("listed_id")
    assert steps[0]["use_auth"] == "token_a" and steps[-1]["use_auth"] == "token_b"
    assert steps[-1]["expected_status"] == [403, 404]


def test_all_templates_threads_supplied_flag():
    g_self = cg.all_templates(_model())
    g_supp = cg.all_templates(_model(), supplied=True)
    # self-register path prepends steps; supplied path does not → fewer steps.
    assert (len(g_supp["broken-authz"][0]["api_steps"])
            < len(g_self["broken-authz"][0]["api_steps"]))


# ── G3: only assert on a field the spec marks REQUIRED ────────────────────────

def test_validation_skips_endpoint_with_no_required_field():
    # Spec lists request fields but marks none required → don't cry "missing
    # validation" on a field the API legitimately accepts empty.
    model = AppModel(endpoints=[
        {"method": "POST", "path": "/orgs", "request_fields": ["description", "website"],
         "required_fields": []},
    ])
    assert cg.validation_templates(model) == []


def test_validation_uses_required_field_not_first_field():
    model = AppModel(endpoints=[
        {"method": "POST", "path": "/orgs",
         "request_fields": ["description", "username"], "required_fields": ["username"]},
    ])
    tpls = cg.validation_templates(model)
    assert tpls and tpls[0]["api_steps"][-1]["body"] == {"username": ""}


def test_validation_falls_back_when_no_required_info():
    # Source-only model (no required_fields key) keeps the old behaviour.
    model = AppModel(endpoints=[
        {"method": "POST", "path": "/items", "request_fields": ["title"]},
    ])
    tpls = cg.validation_templates(model)
    assert tpls and tpls[0]["api_steps"][-1]["body"] == {"title": ""}


# ── runner: supplied auth scheme/header + seeded token variables (G1) ─────────

class _CaptureSession:
    """Records the headers of the last request; returns a 200."""
    def __init__(self):
        self.headers = {}
        self.last_headers = None

    def request(self, method, url, json=None, headers=None, timeout=None):
        from unittest.mock import MagicMock
        self.last_headers = headers or {}
        return MagicMock(status_code=200, text="{}", json=lambda: {})


def _runner_with_session():
    r = APIRunner()
    r.setup(base_url="http://localhost:8911", auth_scheme="token",
            seed_variables={"token": "SEEDED"})
    r.session = _CaptureSession()
    return r


def test_runner_applies_supplied_scheme_to_seeded_token():
    r = _runner_with_session()
    r.execute_api_step({"step_number": 1, "method": "GET", "path": "/x",
                        "use_auth": "token", "expected_status": [200]},
                       dict(r.seed_variables))
    assert r.session.last_headers.get("Authorization") == "token SEEDED"


def test_runner_empty_scheme_sends_raw_token():
    r = APIRunner()
    r.setup(base_url="http://localhost:8911", auth_scheme="", auth_header="X-API-Key")
    r.session = _CaptureSession()
    r.execute_api_step({"step_number": 1, "method": "GET", "path": "/x",
                        "use_auth": "token", "expected_status": [200]}, {"token": "K"})
    assert r.session.last_headers.get("X-API-Key") == "K"


def test_runner_seeds_variables_into_every_test_case():
    r = APIRunner()
    r.setup(base_url="http://localhost:8911", auth_scheme="token",
            seed_variables={"token": "SEEDED", "token_b": "B"})
    r.session = _CaptureSession()
    res = r.execute_test_case({"test_case_id": "T1", "title": "t", "api_steps": [
        {"step_number": 1, "method": "GET", "path": "/x", "use_auth": "token_b",
         "expected_status": [200]}]})
    assert res["status"] == "PASS"
    assert r.session.last_headers.get("Authorization") == "token B"


# ── G2: a coverage template that fails at SETUP is inconclusive, not a FAIL ────

def test_coverage_setup_failure_classifier():
    tc = {"attack": "coverage-template", "api_steps": [
        {"step_number": 1}, {"step_number": 2}]}
    setup_fail = {"steps": [{"step_number": 1, "status": "FAIL"}]}
    assert_fail = {"steps": [{"step_number": 2, "status": "FAIL"}]}
    assert APIRunner._is_coverage_setup_failure(tc, setup_fail) is True
    assert APIRunner._is_coverage_setup_failure(tc, assert_fail) is False
    # a non-coverage test is never a "setup failure"
    assert APIRunner._is_coverage_setup_failure({"attack": "redteam"}, setup_fail) is False


def test_setup_failed_coverage_test_not_counted_as_failed():
    # One coverage template whose create (step 1 of 2) fails → batch reports it
    # under `setup`, not `failed`; pass_rate ignores it.
    r = APIRunner()
    r.setup(base_url="http://localhost:8911")

    class _FailFirst:
        headers = {}
        def request(self, method, url, json=None, headers=None, timeout=None):
            from unittest.mock import MagicMock
            # the create step (POST) fails; nothing else should run after a FAIL
            return MagicMock(status_code=403, text="forbidden", json=lambda: {})
    r.session = _FailFirst()
    batch = r.execute_batch([{"test_case_id": "C1", "title": "cov", "attack": "coverage-template",
                              "api_steps": [
                                  {"step_number": 1, "method": "POST", "path": "/a",
                                   "expected_status": [201], "use_auth": "token_a"},
                                  {"step_number": 2, "method": "GET", "path": "/a/1",
                                   "expected_status": [403, 404], "use_auth": "token_b"}]}])
    assert batch["setup"] == 1 and batch["failed"] == 0


def test_get_idor_template_has_a_listing_control_before_the_attack():
    from services.coverage_goals import idor_templates
    from services.results import AppModel
    m = AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/api/posts", "request_fields": ["text"], "auth_required": True},
        {"method": "GET", "path": "/api/posts/{id}", "auth_required": True},
        {"method": "PUT", "path": "/api/posts/{id}", "request_fields": ["text"], "auth_required": True}])
    t = {x["title"]: x for x in idor_templates(m, supplied=True)}
    get_steps = t["Coverage: GET /api/posts/{id} blocks another user (IDOR)"]["api_steps"]
    ctrl = [s for s in get_steps if s.get("listed_id")]
    assert ctrl and ctrl[0]["use_auth"] == "token_b" and ctrl[0]["path"] == "/api/posts"
    assert get_steps[-1]["expected_status"] == [403, 404]          # the attack is still the LAST step
    put_steps = t["Coverage: PUT /api/posts/{id} blocks another user (IDOR)"]["api_steps"]
    assert not [s for s in put_steps if s.get("listed_id")]        # writing someone else's data is never "shared"


def test_shared_by_design_idor_is_not_confirmed_but_a_private_one_is():
    from services.orchestrator_service import OrchestratorService as O
    class Ex:
        def __init__(self, listed): self.results = [{"test_case_id": "COV-IDOR-0001", "title": "Coverage: GET /p/{id} blocks another user (IDOR)",
            "status": "FAIL", "steps": [{"step_number": 3, "status": "PASS", "response_code": 200, "listed": listed},
                                        {"step_number": 4, "status": "FAIL", "response_code": 200, "error": "Expected status [403, 404], got 200"}]}]
    tc = {"test_case_id": "COV-IDOR-0001", "title": "Coverage: GET /p/{id} blocks another user (IDOR)", "attack": "coverage-template",
          "api_steps": [{"step_number": 3, "method": "GET", "path": "/p", "listed_id": "{{rid}}"},
                        {"step_number": 4, "method": "GET", "path": "/p/{{rid}}", "expected_status": [403, 404]}]}
    assert O._confirmed_from_templates(Ex(True), [tc], []) == []                       # listed -> shared by design
    assert len(O._confirmed_from_templates(Ex(False), [tc], [])) == 1                   # not listed -> private -> bug
