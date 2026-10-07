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
        {"method": "GET", "path": "/todos", "auth_required": True,
         "response_fields": ["items", "total", "total_pages"]},
        {"method": "POST", "path": "/todos", "auth_required": True, "request_fields": ["title"]},
        {"method": "GET", "path": "/todos/{todo_id}", "auth_required": True},
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
        {"method": "GET", "path": "/users/", "auth_required": True},
    ])
    titles = " ".join(t["title"] for t in cg.auth_bypass_templates(model))
    assert "/users/" in titles
    for public in (" / requires", "/health", "/api-docs", "/openapi.json"):
        assert public not in titles
    assert cg._is_conventionally_public("/") and cg._is_conventionally_public("/v1/health")
    assert not cg._is_conventionally_public("/api/users")


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


def test_all_templates_groups_applicable_goals():
    groups = cg.all_templates(_model())
    assert set(groups) == {"auth-bypass", "broken-authz", "validation"}
    assert all(isinstance(v, list) and v for v in groups.values())


def test_no_goals_when_nothing_applies():
    empty = AppModel(endpoints=[{"method": "GET", "path": "/health"}])
    assert cg.all_templates(empty) == {}
