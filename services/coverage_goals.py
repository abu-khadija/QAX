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


def _required_create_field(endpoint: dict, model, create_path: str) -> str | None:
    """
    The REQUIRED field to send empty for a validation test, or None to skip.

    When the endpoint carries a `required_fields` list (spec-grounded), pick a
    required one (prefer a title/name-like field); an empty list means the spec
    marks nothing required → return None (don't assert). When the key is absent
    (source-only model with no spec), fall back to the first create field so
    those apps still get a validation test.
    """
    if "required_fields" in endpoint:
        req = field_names(endpoint.get("required_fields"))
        if not req:
            return None
        for pref in ("title", "name", "label", "text"):
            if pref in req:
                return pref
        return req[0]
    return _create_field(model, create_path)


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


def _asserts_auth(e: dict) -> bool:
    """
    Whether to assert that this endpoint rejects a no-token request. A write
    (POST/PUT/PATCH/DELETE) or an endpoint with its OWN explicit security must reject.
    A GET whose auth is only INHERITED from a global spec security is NOT asserted —
    frameworks (e.g. drf-yasg) slap a global security on everything, including public
    list reads, and asserting there cries wolf on a legitimate public 200.
    """
    if str(e.get("method", "")).upper() != "GET":
        return True                      # writes should always require auth
    return bool(e.get("auth_explicit"))  # GET: only when it declares its own security


def auth_bypass_templates(model, recipe: dict | None = None, cap: int = 8) -> list[dict]:
    """One test per protected endpoint: calling it with NO token must be rejected."""
    protected = [e for e in getattr(model, "endpoints", [])
                 if isinstance(e, dict) and e.get("auth_required")
                 and _asserts_auth(e)
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


def idor_templates(model, recipe: dict | None = None, cap: int = 6,
                   *, supplied: bool = False) -> list[dict]:
    """
    Two-user tests: userB must not reach a resource userA created. One per
    single-resource protected endpoint (GET/PUT/DELETE/PATCH with a path param).

    When ``supplied`` is True the caller pre-seeds token_a/token_b, so no
    register→login preamble is emitted — the create+attack run directly with
    the seeded identities (lets this layer work on apps QAX can't register into).
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

        if supplied:
            steps, n = [], 1
        else:
            steps = _auth_steps(recipe, model, "a", "token_a", 1) + _auth_steps(recipe, model, "b", "token_b", 3)
            n = 5
        # userA creates a resource
        steps.append({"step_number": n, "method": "POST", "path": collection,
                      "body": {create_field: "owned by A"}, "expected_status": [200, 201],
                      "use_auth": "token_a", "save": {"rid": "id"}})
        # userB attacks it — the secure response is 403 or 404, never 2xx
        attack = {"step_number": n + 1, "method": method,
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


def validation_templates(model, recipe: dict | None = None, cap: int = 6,
                         *, supplied: bool = False) -> list[dict]:
    """
    One test per create endpoint: an empty REQUIRED field must be rejected.

    Only a field the spec marks required is asserted — a spec that lists its
    request fields but marks this one optional (e.g. `description`) is skipped,
    so we don't cry "missing validation" on a field the API legitimately
    accepts empty. When the model carries no required-field info (source-only,
    no spec), the first create field is used as before.

    When ``supplied`` is True the caller pre-seeds `token`, so no register→login
    preamble is emitted for auth'd creates — the POST runs with the seeded token.
    """
    recipe = recipe or derive_auth_recipe(model)
    creates = [e for e in getattr(model, "endpoints", [])
               if isinstance(e, dict) and str(e.get("method", "")).upper() == "POST"
               and "{" not in str(e.get("path", "")) and e.get("request_fields")
               and "auth" not in str(e.get("path", "")).lower()]
    out = []
    for e in creates[:cap]:
        path = str(e.get("path", ""))
        create_field = _required_create_field(e, model, path)
        if create_field is None:
            continue  # no required field to assert on — skip rather than cry wolf
        # Only register+login when the create endpoint actually requires auth.
        # A no-auth app (or a public create) has no login flow — prepending one
        # 404s at setup and the validation assertion never runs. Post directly then.
        if e.get("auth_required") and not supplied:
            steps = _auth_steps(recipe, model, "v", "token", 1)
            steps.append({"step_number": 3, "method": "POST", "path": path,
                          "body": {create_field: ""}, "expected_status": [422, 400],
                          "use_auth": "token"})
        else:
            step = {"step_number": 1, "method": "POST", "path": path,
                    "body": {create_field: ""}, "expected_status": [422, 400]}
            if e.get("auth_required"):   # supplied: use the seeded token
                step["use_auth"] = "token"
            steps = [step]
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


def all_templates(model, recipe: dict | None = None, *, supplied: bool = False) -> dict:
    """
    Build every deterministic coverage template, grouped by goal.

    Args:
        supplied: True when the caller pre-seeds auth tokens into execution
            (token/token_a/token_b). The templates then skip their own
            register→login preamble and rely on the seeded tokens, so the
            authenticated coverage layer runs against apps QAX can't
            self-register into (external IdP, token-only APIs like Gitea).

    Returns:
        {goal: [test_case, ...]} for the goals that apply to this app.
    """
    recipe = recipe or derive_auth_recipe(model)
    groups = {
        "auth-bypass": auth_bypass_templates(model, recipe),
        "broken-authz": idor_templates(model, recipe, supplied=supplied),
        "validation": validation_templates(model, recipe, supplied=supplied),
    }
    return {g: tests for g, tests in groups.items() if tests}
