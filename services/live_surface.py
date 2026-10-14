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
import re
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# Prefixes a REST API is commonly mounted under when the code-derived paths omit it.
_BASE_PREFIX_CANDIDATES = ("/api", "/api/v1", "/v1", "/api/v2", "/v2", "/rest")

# Path-parameter spellings across frameworks → the canonical {name} the probes expect.
_FLASK_PARAM = re.compile(r"<(?:[A-Za-z_][\w]*:)?([A-Za-z_][\w]*)>")      # Flask/Werkzeug <int:id>, <id>
_COLON_PARAM = re.compile(r"(?<=/):([A-Za-z_][\w]*)")                      # Express/Gin/Rails /:id


def normalize_path_params(path: str) -> str:
    """
    Rewrite a route's path parameters to the canonical `{name}` form the probes use,
    whatever framework spelled them: Flask/Django `<id>` / `<int:id>` / `<path:p>`,
    Express/Gin/Rails `/:id`. OpenAPI paths are already `{id}` and pass through. This
    lets the probes find path params on source-modelled apps that serve no spec.
    """
    p = _FLASK_PARAM.sub(r"{\1}", str(path))
    p = _COLON_PARAM.sub(r"{\1}", p)
    return p

# Common locations an API serves its OpenAPI/Swagger spec.
_SPEC_PATHS = [
    "/openapi.json", "/openapi.yaml", "/swagger.json", "/v2/swagger.json",
    "/v3/api-docs", "/api-docs", "/api/openapi.json", "/docs/openapi.json",
    "/swagger/v1/swagger.json",
    # Django ecosystems: drf-yasg serves the spec at /swagger/?format=openapi,
    # drf-spectacular at /api/schema/ (found scanning a real DRF app, 2026-10-14).
    "/swagger/?format=openapi", "/api/schema/", "/api/schema", "/schema/",
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


def _base_path(spec: dict) -> str:
    """
    The API's base path from the spec: Swagger 2 `basePath`, or the path component
    of OpenAPI 3 `servers[0].url` (which may be a full URL or just a path). Returns
    "" when there is none. Without this, an app that mounts its paths under a prefix
    (e.g. servers:[{url:'/api'}] + path '/users') is probed at the wrong URL.
    """
    bp = spec.get("basePath")  # Swagger 2
    if isinstance(bp, str) and bp.strip("/"):
        return "/" + bp.strip("/")
    servers = spec.get("servers")  # OpenAPI 3
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        url = servers[0].get("url")
        if isinstance(url, str) and url.strip():
            path = urlparse(url).path if "//" in url else url
            if (path or "").strip("/"):
                return "/" + path.strip("/")
    return ""


def _join_base(base: str, path: str) -> str:
    """Join a base path to a spec path, without doubling it when already present."""
    p = path if path.startswith("/") else "/" + path
    if not base:
        return p
    if p == "/":
        return base            # the base root itself
    if p == base or p.startswith(base + "/"):
        return p               # spec already includes the base
    return base + p


def _query_params(op: dict, item: dict, spec: dict) -> list[str]:
    """Names of the query parameters for an operation (path-item + op level, $ref-aware)."""
    names: list[str] = []
    seen: set = set()
    for src in (item, op):
        for p in (src.get("parameters") or []):
            if not isinstance(p, dict):
                continue
            if "$ref" in p:
                p = _resolve(p["$ref"], spec)
            if p.get("in") == "query" and p.get("name") and p["name"] not in seen:
                seen.add(p["name"])
                names.append(str(p["name"]))
    return names


def endpoints_from_spec(spec: dict) -> list[dict]:
    """
    Convert an OpenAPI/Swagger spec into QAX AppModel endpoints — the EXACT paths
    (including the server/base-path prefix), methods, auth requirement, query
    parameters, and request/response field names, from live truth.
    """
    out: list[dict] = []
    has_global_sec = bool(spec.get("security"))
    base = _base_path(spec)
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in ("get", "post", "put", "delete", "patch"):
                continue
            op = op if isinstance(op, dict) else {}
            auth_explicit = "security" in op and bool(op["security"])
            auth_required = auth_explicit or has_global_sec
            out.append({
                "method": method.upper(),
                "path": _join_base(base, str(path)),
                "auth_required": bool(auth_required),
                # True only when THIS operation declares its own security. A global
                # spec `security` (e.g. drf-yasg applies one to everything, including
                # public list reads) sets auth_required but NOT auth_explicit — so the
                # auth-bypass coverage check won't cry wolf on a public GET it covered.
                "auth_explicit": bool(auth_explicit),
                "query_params": _query_params(op, item, spec),
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


# Common REST/GraphQL paths to probe on an app that serves no OpenAPI spec.
_COMMON_API_PATHS = (
    "/api", "/api/v1", "/health", "/status", "/me", "/users", "/api/users",
    "/login", "/auth/login", "/products", "/api/products", "/orders", "/api/orders",
    "/search", "/admin", "/graphql", "/api/graphql",
)


def discover_endpoints(base_url: str, model, *, session: requests.Session | None = None) -> list[dict]:
    """
    Spec-less discovery. When the app serves no OpenAPI, probe a small set of common
    REST/GraphQL paths and return the ones that are actually LIVE (a definitive handler
    response via `_route_exists`) and not already in the model — so the probes get a
    real surface to work on instead of only unverified code inference. Discovery can be
    generous: the probes have their own zero-false-positive controls, so an extra path
    only costs a request, never a bogus finding.
    """
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    existing = {normalize_path_params(str(e.get("path", "")))
                for e in getattr(model, "endpoints", []) if isinstance(e, dict)}
    out: list[dict] = []
    for p in _COMMON_API_PATHS:
        if p in existing or p in {d["path"] for d in out}:
            continue
        if _route_exists(base, p, sess):
            out.append({"method": "GET", "path": p, "auth_required": False, "source": "discovered"})
    return out


def _route_exists(base: str, path: str, sess: requests.Session) -> bool:
    """
    True only when a GET to `path` gets a DEFINITIVE "a handler ran" response.
    404 means no route. 401/403 are ambiguous — some frameworks (e.g. Spring
    Security) answer 401 for unmatched paths too — so they are NOT counted as
    evidence, which would otherwise make a wrong prefix look valid. A real handler
    answers 2xx/3xx or a 4xx/5xx other than 401/403/404.
    """
    probe = re.sub(r"\{[^}]+\}|:([A-Za-z_]+)", "1", path)   # fill path params/{id}/:slug
    try:
        r = sess.get(base + probe, timeout=10, allow_redirects=False)
    except Exception:
        return False
    return getattr(r, "status_code", 404) not in (401, 403, 404)


def recover_base_path(model, base_url: str, *, session: requests.Session | None = None) -> str:
    """
    Grounding fallback for apps with no OpenAPI: the code model can omit the base path
    the API is really mounted under (e.g. it extracts `/users` but the live route is
    `/api/users`). We GET a few modelled paths as-is and under common prefixes; if a
    prefix resolves routes that the bare paths do NOT, we prepend it to every endpoint.

    Only rewrites when a prefix is STRICTLY better than the paths as they are, so a
    correctly-modelled app (paths already resolve) is never touched. Returns the prefix
    applied ("" when none). Best-effort, GET-only (no side effects), never raises.
    """
    eps = [e for e in getattr(model, "endpoints", []) if isinstance(e, dict) and e.get("path")]
    if not eps:
        return ""
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    # A small, de-duplicated sample of paths to vote with.
    sample, seen = [], set()
    for e in eps:
        p = str(e["path"])
        if p not in seen:
            seen.add(p)
            sample.append(p)
        if len(sample) >= 5:
            break

    def score(prefix: str) -> int:
        return sum(1 for p in sample if _route_exists(base, prefix + p, sess))

    as_is = score("")
    best_prefix, best_score = "", as_is
    for pref in _BASE_PREFIX_CANDIDATES:
        # don't double-apply a prefix the paths already carry
        if all(str(e["path"]).startswith(pref + "/") for e in eps):
            continue
        s = score(pref)
        if s > best_score:
            best_prefix, best_score = pref, s

    if best_prefix and best_score > as_is:
        for e in eps:
            p = str(e["path"])
            if not p.startswith(best_prefix + "/"):
                e["path"] = best_prefix + p
        logger.info("live_surface: recovered base path %s (%d/%d routes resolved vs %d)",
                    best_prefix, best_score, len(sample), as_is)
    return best_prefix
