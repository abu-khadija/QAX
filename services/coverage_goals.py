"""
services/coverage_goals.py — deterministic coverage templates
=============================================================
The high-value test classes a QA lead ALWAYS covers, built as deterministic,
model-derived templates so their coverage is guaranteed rather than left to a
lucky generation. The orchestrator injects these when the generated/red-team tests
don't already cover a goal.

Each builder reads the AppModel + auth recipe and returns standard QAX test cases
(api_steps), so they flow straight into execute → critic → investigator. Every
template is written so it FAILS when the app is vulnerable.

Goals covered here:
  - auth-bypass    : a protected endpoint must reject a request with no token.
  - broken-authz   : userB must not reach userA's resource (IDOR).
  - validation     : a create must reject empty required input.
(pagination-boundary lives in the orchestrator, close to its coverage check.)
"""

from __future__ import annotations

import re

from core.auth_recipe import derive_auth_recipe, field_names


def _concrete(path: str, value: str = "999999") -> str:
    """Replace path params ({id}, {todo_id}) with a concrete value."""
    return re.sub(r"\{[^}]+\}", str(value), str(path))


def _collection_of(path: str) -> str:
    """The collection path for a single-resource path: /todos/{id}/x → /todos."""
    return re.sub(r"/\{[^}]+\}.*$", "", str(path)) or str(path)


def _create_field(model, create_path: str) -> str:
    """The field a create needs — prefer a title/name-like text field."""
    for e in getattr(model, "endpoints", []):
        if (isinstance(e, dict) and str(e.get("method", "")).upper() == "POST"
                and str(e.get("path", "")).rstrip("/") == create_path.rstrip("/")):
            names = field_names(e.get("request_fields"))
            for pref in ("title", "name", "label", "text"):
                if pref in names:
                    return pref
            if names:
                return names[0]
    return "title"


def _auth_steps(recipe: dict, model, tag: str, token_var: str, start: int) -> list[dict]:
    """Register + login one user, saving their token under `token_var`."""
    from core.auth_recipe import register_body
    email = f"{tag}_{{{{random}}}}@example.com"
    full = register_body(recipe, model, email)
    login = {recipe["id_field"]: email, recipe["secret_field"]: "Secret123!"}
    return [
        {"step_number": start, "method": "POST", "path": recipe["register_path"],
         "body": full, "expected_status": 201},
        {"step_number": start + 1, "method": "POST", "path": recipe["login_path"],
         "body": login, "expected_status": 200, "save": {token_var: recipe["token_field"]}},
    ]


_PUBLIC_SEGMENTS = {
    "health", "healthz", "livez", "readyz", "ready", "live", "ping", "status",
    "metrics", "favicon.ico", "docs", "redoc", "swagger", "api-docs",
    "openapi", "openapi.json", "openapi.yaml", "version", "info",
}


def _is_conventionally_public(path: str) -> bool:
    """
    Root/health/docs/spec endpoints are public by convention even when a spec
    declares global `security`. Asserting auth on them produces false positives
    (e.g. a root landing page returning 200 HTML flagged as an 'auth bypass') —
    don't cry wolf on them.
    """
    p = str(path).split("?")[0].rstrip("/").lower()
    if p in ("", "/"):
        return True
    return p.rsplit("/", 1)[-1] in _PUBLIC_SEGMENTS


def auth_bypass_templates(model, recipe: dict | None = None, cap: int = 8) -> list[dict]:
    """One test per protected endpoint: calling it with NO token must be rejected."""
    protected = [e for e in getattr(model, "endpoints", [])
                 if isinstance(e, dict) and e.get("auth_required")
                 and not _is_conventionally_public(e.get("path", ""))]
    out = []
    for e in protected[:cap]:
        method = str(e.get("method", "GET")).upper()
        path = _concrete(str(e.get("path", "")))
        step = {"step_number": 1, "method": method, "path": path,
                "expected_status": [401, 403]}
        if method in ("POST", "PUT", "PATCH"):
            step["body"] = {}
        out.append({
            "test_case_id": f"COV-AUTH-{len(out)+1:04d}",
            "title": f"Coverage: {method} {e.get('path')} requires authentication",
            "test_type": "security", "module": "coverage", "attack": "coverage-template",
            "api_steps": [step]})
    return out


def idor_templates(model, recipe: dict | None = None, cap: int = 6) -> list[dict]:
    """
    Two-user tests: userB must not reach a resource userA created. One per
    single-resource protected endpoint (GET/PUT/DELETE/PATCH with a path param).
    """
    recipe = recipe or derive_auth_recipe(model)
    owned = [e for e in getattr(model, "endpoints", [])
             if isinstance(e, dict) and e.get("auth_required")
             and "{" in str(e.get("path", ""))
             and str(e.get("method", "")).upper() in ("GET", "PUT", "DELETE", "PATCH")]
    out = []
    for e in owned[:cap]:
        method = str(e.get("method", "GET")).upper()
        path = str(e.get("path", ""))
        collection = _collection_of(path)
        create_field = _create_field(model, collection)

        steps = _auth_steps(recipe, model, "a", "token_a", 1) + _auth_steps(recipe, model, "b", "token_b", 3)
        # userA creates a resource
        steps.append({"step_number": 5, "method": "POST", "path": collection,
                      "body": {create_field: "owned by A"}, "expected_status": [200, 201],
                      "use_auth": "token_a", "save": {"rid": "id"}})
        # userB attacks it — the secure response is 403 or 404, never 2xx
        attack = {"step_number": 6, "method": method,
                  "path": path.replace("{" + _param_name(path) + "}", "{{rid}}") if _param_name(path) else path,
                  "expected_status": [403, 404], "use_auth": "token_b"}
        if method in ("PUT", "PATCH"):
            attack["body"] = {create_field: "hacked by B"}
        steps.append(attack)
        out.append({
            "test_case_id": f"COV-IDOR-{len(out)+1:04d}",
            "title": f"Coverage: {method} {path} blocks another user (IDOR)",
            "test_type": "security", "module": "coverage", "attack": "coverage-template",
            "api_steps": steps})
    return out


def validation_templates(model, recipe: dict | None = None, cap: int = 6) -> list[dict]:
    """One test per create endpoint: an empty required field must be rejected."""
    recipe = recipe or derive_auth_recipe(model)
    creates = [e for e in getattr(model, "endpoints", [])
               if isinstance(e, dict) and str(e.get("method", "")).upper() == "POST"
               and "{" not in str(e.get("path", "")) and e.get("request_fields")
               and "auth" not in str(e.get("path", "")).lower()]
    out = []
    for e in creates[:cap]:
        path = str(e.get("path", ""))
        create_field = _create_field(model, path)
        steps = _auth_steps(recipe, model, "v", "token", 1)
        steps.append({"step_number": 3, "method": "POST", "path": path,
                      "body": {create_field: ""}, "expected_status": [422, 400],
                      "use_auth": True})
        out.append({
            "test_case_id": f"COV-VAL-{len(out)+1:04d}",
            "title": f"Coverage: POST {path} rejects empty '{create_field}'",
            "test_type": "negative", "module": "coverage", "attack": "coverage-template",
            "api_steps": steps})
    return out


def _param_name(path: str) -> str:
    """The first {param} name in a path, or '' if none."""
    m = re.search(r"\{([^}]+)\}", str(path))
    return m.group(1) if m else ""


def all_templates(model, recipe: dict | None = None) -> dict:
    """
    Build every deterministic coverage template, grouped by goal.

    Returns:
        {goal: [test_case, ...]} for the goals that apply to this app.
    """
    recipe = recipe or derive_auth_recipe(model)
    groups = {
        "auth-bypass": auth_bypass_templates(model, recipe),
        "broken-authz": idor_templates(model, recipe),
        "validation": validation_templates(model, recipe),
    }
    return {g: tests for g, tests in groups.items() if tests}
