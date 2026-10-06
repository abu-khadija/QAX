"""
services/live_surface.py — ground truth from the RUNNING app
============================================================
The VAmPI reality check exposed the architecture mistake: QAX trusted the SOURCE
CODE for concrete details (exact endpoint paths) and got them wrong (/users/me vs
/me, /books vs /books/v1), which cascaded into false positives and missed real
bugs. That violated the user's own rule #1: the running app is the source of
truth; the code is only a hypothesis.

This closes it. The code still decides WHAT is risky (QAX's white-box edge); the
running app now provides the exact HOW/WHERE to execute against. For an API that
exposes an OpenAPI/Swagger spec (FastAPI, connexion, Flask-RESTX, springdoc, …),
that spec is the ground truth — fetched live, parsed to exact paths/methods/auth.

Best-effort and network-safe: any failure returns None/[] so the caller falls back
to the code-derived model.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

# Common locations an API serves its OpenAPI/Swagger spec.
_SPEC_PATHS = [
    "/openapi.json", "/openapi.yaml", "/swagger.json", "/v2/swagger.json",
    "/v3/api-docs", "/api-docs", "/api/openapi.json", "/docs/openapi.json",
    "/swagger/v1/swagger.json",
]


def fetch_openapi(base_url: str, *, session: requests.Session | None = None) -> dict | None:
    """Fetch and parse the app's OpenAPI/Swagger spec, or None if none is served."""
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    for p in _SPEC_PATHS:
        try:
            r = sess.get(f"{base}{p}", timeout=10)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        try:
            spec = r.json()
        except Exception:
            try:
                import yaml  # optional
                spec = yaml.safe_load(r.text)
            except Exception:
                continue
        if isinstance(spec, dict) and (spec.get("openapi") or spec.get("swagger")) and spec.get("paths"):
            return spec
    return None


def endpoints_from_spec(spec: dict) -> list[dict]:
    """
    Convert an OpenAPI/Swagger spec into QAX AppModel endpoints — the EXACT paths,
    methods, auth requirement, and request/response field names, from live truth.
    """
    out: list[dict] = []
    has_global_sec = bool(spec.get("security"))
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in ("get", "post", "put", "delete", "patch"):
                continue
            op = op if isinstance(op, dict) else {}
            auth_required = ("security" in op and bool(op["security"])) or has_global_sec
            out.append({
                "method": method.upper(),
                "path": str(path),
                "auth_required": bool(auth_required),
                "request_fields": _request_fields(op, spec),
                "response_fields": _response_fields(op, spec),
            })
    return out


def _resolve(ref: str, spec: dict) -> dict:
    """Resolve a local $ref (#/components/schemas/X or #/definitions/X)."""
    node = spec
    for part in ref.lstrip("#/").split("/"):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return {}
    return node if isinstance(node, dict) else {}


def _schema_fields(schema: dict, spec: dict) -> list[str]:
    """Property names of a (possibly $ref'd) object schema."""
    if not isinstance(schema, dict):
        return []
    if "$ref" in schema:
        schema = _resolve(schema["$ref"], spec)
    props = schema.get("properties")
    return list(props.keys()) if isinstance(props, dict) else []


def _request_fields(op: dict, spec: dict) -> list[str]:
    # OpenAPI 3 requestBody
    body = (op.get("requestBody") or {}).get("content") or {}
    for media in body.values():
        fields = _schema_fields((media or {}).get("schema") or {}, spec)
        if fields:
            return fields
    # Swagger 2 body parameter
    for param in op.get("parameters") or []:
        if isinstance(param, dict) and param.get("in") == "body":
            return _schema_fields(param.get("schema") or {}, spec)
    return []


def _response_fields(op: dict, spec: dict) -> list[str]:
    responses = op.get("responses") or {}
    for code in ("200", "201", 200, 201):
        resp = responses.get(code)
        if not isinstance(resp, dict):
            continue
        content = resp.get("content") or {}
        for media in content.values():
            fields = _schema_fields((media or {}).get("schema") or {}, spec)
            if fields:
                return fields
        if "schema" in resp:  # Swagger 2
            return _schema_fields(resp["schema"], spec)
    return []


def ground_endpoints(base_url: str, *, session: requests.Session | None = None) -> list[dict]:
    """
    Return the running app's TRUE endpoints from its live OpenAPI spec, or [] if it
    serves none (caller then keeps the code-derived endpoints, ideally after a live
    path check).
    """
    spec = fetch_openapi(base_url, session=session)
    if not spec:
        return []
    eps = endpoints_from_spec(spec)
    logger.info("live_surface: grounded %d endpoint(s) from the running app's spec", len(eps))
    return eps
