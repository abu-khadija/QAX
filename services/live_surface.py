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
    # Gitea/Gogs serve their Swagger 2.0 spec here (found scanning Gitea, 2026-10-15).
    "/swagger.v1.json",
    # apifairy / APIFlask / Flask-Smorest serve theirs here (found scanning microblog-api, 2026-10-17).
    "/apispec.json", "/api/apispec.json", "/api/swagger.json", "/api/openapi.json",
    # flask-restx (CTFd) serves it under the API prefix (found scanning CTFd, 2026-10-17).
    "/api/v1/swagger.json", "/api/v1/openapi.json", "/api/v2/swagger.json", "/api/v2/openapi.json",
    "/api/v3/openapi.json", "/api/v1/docs/openapi.json",
]


def parse_endpoint_lines(text: str) -> list[dict]:
    """
    Parse a hand-written endpoint list (the --endpoints file) for apps that serve no spec and whose routes
    cannot be guessed (PocketBase: /api/collections/<name>/records). One endpoint per line:

        GET    /api/collections/notes/records
        POST   /api/collections/notes/records   title,body
        DELETE /api/collections/notes/records/{id}

    The optional last column lists request fields (comma-separated; append ! to mark one required: title!).
    Blank lines and `#` comments are ignored; bad lines are skipped. Never raises.
    """
    out: list[dict] = []
    for raw in str(text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE") or not parts[1].startswith("/"):
            continue
        fields = [f for f in (parts[2].split(",") if len(parts) > 2 else []) if f.strip()]
        req = [f.rstrip("!") for f in fields if f.endswith("!")]
        names = [f.rstrip("!") for f in fields]
        out.append({"method": parts[0].upper(), "path": normalize_path_params(parts[1]), "auth_required": True,
                    "request_fields": names, "required_fields": req, "request_field_types": {},
                    "response_fields": [], "source": "supplied"})
    return out


def parse_examples(text: str) -> dict:
    """
    Parse the --examples file: JSON mapping "METHOD /path" to a request body QAX may send as a VALID create/update,
    e.g. {"POST /api/v1/pages": {"title": "T {{unique}}", "route": "r-{{unique}}", "link_target": "_blank"}}.
    `{{unique}}` (also `{{random}}`) is replaced by a fresh value per request. Returns {(METHOD, path): body}.
    Bad entries are ignored; never raises.
    """
    import json as _json
    out: dict = {}
    try:
        data = _json.loads(text)
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    for k, body in data.items():
        parts = str(k).split(None, 1)
        if len(parts) == 2 and parts[0].upper() in ("POST", "PUT", "PATCH") and isinstance(body, dict):
            out[(parts[0].upper(), normalize_path_params(parts[1].strip()))] = body
    return out


def apply_examples(endpoints: list[dict], examples: dict) -> list[dict]:
    """
    Attach each example body to its endpoint (`example_body`). An example for an endpoint QAX does not know is ADDED
    (the operator is telling us it exists), with its keys as request fields.
    """
    if not examples:
        return endpoints
    by = {(str(e.get("method", "")).upper(), normalize_path_params(str(e.get("path", "")))): e
          for e in endpoints if isinstance(e, dict)}
    out = list(endpoints)
    for key, body in examples.items():
        e = by.get(key)
        if e is None:
            e = {"method": key[0], "path": key[1], "auth_required": True, "request_fields": list(body),
                 "required_fields": [], "request_field_types": {}, "response_fields": [], "source": "supplied"}
            out.append(e)
        e["example_body"] = body
    return out


def apply_supplied_endpoints(base_url: str, supplied: list[dict], existing: list[dict], sess=None) -> list[dict]:
    """
    Merge the operator's endpoint list into the model: skip ones already known, and mark each GET whether it needs
    a credential (the same GET without one answers 401/403) — the rest default to requiring one.
    """
    sess = sess or requests.Session()
    base = base_url.rstrip("/")
    have = {(str(e.get("method", "")).upper(), normalize_path_params(str(e.get("path", "")))) for e in existing}
    anon = requests.Session()
    out = list(existing)
    for e in supplied:
        key = (e["method"], e["path"])
        if key in have:
            continue
        if e["method"] == "GET" and "{" not in e["path"]:
            try:
                r = anon.get(base + e["path"], timeout=10, allow_redirects=False)
                e["auth_required"] = r.status_code in (401, 403)
            except Exception:
                pass
        out.append(e)
        have.add(key)
    return out


def authed_session(auth: dict | None = None) -> requests.Session:
    """
    A requests.Session that carries the operator's credentials (token header and/or cookie), for the READ-ONLY
    discovery requests. Without it, discovery was blind to every endpoint that answers 401 anonymously — i.e. most
    of a real API (found on linkding: /api/bookmarks/ was invisible, 6 of ~15 endpoints known).
    """
    sess = requests.Session()
    auth = auth or {}
    token = auth.get("token")
    if token:
        header = auth.get("header") or "Authorization"
        scheme = auth.get("scheme", "Bearer") if "scheme" in auth else "Bearer"
        sess.headers[header] = f"{scheme} {token}".strip() if scheme else str(token)
    from core.session_auth import apply_extras
    return apply_extras(sess, auth)


def _spec_from_override(spec_url: str, base: str, sess) -> dict | None:
    """A user-supplied --spec: an absolute URL, a path on the app ("/apispec.json"), or a local file."""
    import json as _json
    import os as _os
    try:
        if "://" not in spec_url and _os.path.isfile(spec_url):
            text = open(spec_url, encoding="utf-8").read()
        else:
            url = spec_url if "://" in spec_url else base + ("" if spec_url.startswith("/") else "/") + spec_url
            r = sess.get(url, timeout=15)
            if r.status_code != 200:
                return None
            text = r.text
        try:
            spec = _json.loads(text)
        except Exception:
            import yaml  # optional
            spec = yaml.safe_load(text)
        if isinstance(spec, dict) and (spec.get("openapi") or spec.get("swagger")) and spec.get("paths"):
            return spec
    except Exception:
        pass
    return None


def fetch_openapi(base_url: str, *, session: requests.Session | None = None,
                  spec_url: str | None = None) -> dict | None:
    """
    Fetch and parse the app's OpenAPI/Swagger spec, or None if none is served. `spec_url` (the --spec
    flag) overrides the search: a URL, a path on the app, or a local file — for specs in unusual places.
    """
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    if spec_url:
        got = _spec_from_override(spec_url, base, sess)
        if got:
            return got
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


def _sendable(fields: list, types: dict) -> list:
    """
    The request fields a client may actually SEND: drop the ones the spec marks `readOnly` (server-owned:
    id, created_at, avatar_url, has_password ...). OpenAPI says a readOnly property "SHOULD NOT be sent in
    a request"; strict apps reject it ("Unknown field"). Found on microblog-api: QAX sent every field,
    self-registration got HTTP 400, and four probes (mass-assignment, rate-limit, BOLA, persistence)
    silently could not run. The readOnly flag stays in `request_field_types` for the probes that need it.
    """
    ro = {k for k, v in (types or {}).items() if isinstance(v, dict) and v.get("readOnly")}
    return [f for f in (fields or []) if (f if isinstance(f, str) else (f or {}).get("name")) not in ro]


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
                "request_fields": _sendable(_request_fields(op, spec), _request_field_types(op, spec)),
                "required_fields": _required_request_fields(op, spec),
                "request_field_types": _request_field_types(op, spec),
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


def _schema_field_types(schema: dict, spec: dict) -> dict:
    """
    {field: {type, format, readOnly, enum, min_items, items_type, max_length}} for the
    top-level properties of a (possibly $ref'd) object schema. Only keys the spec
    actually states are present, so absence means "unknown", never a guess. Nested
    objects are typed "object" but not descended into (probes build flat bodies).
    """
    if not isinstance(schema, dict):
        return {}
    if "$ref" in schema:
        schema = _resolve(schema["$ref"], spec)
    props = schema.get("properties")
    if not isinstance(props, dict):
        return {}
    out: dict = {}
    for name, ps in props.items():
        if not isinstance(ps, dict):
            continue
        if "$ref" in ps:
            ps = _resolve(ps["$ref"], spec)
        info = {"type": ps.get("type"), "format": ps.get("format"),
                "readOnly": True if ps.get("readOnly") else None,
                "enum": ps.get("enum") if isinstance(ps.get("enum"), list) else None,
                "min_items": ps.get("minItems"), "max_length": ps.get("maxLength")}
        if ps.get("type") == "array":
            items = ps.get("items") or {}
            if isinstance(items, dict):
                if "$ref" in items:
                    items = _resolve(items["$ref"], spec)
                info["items_type"] = items.get("type")
                if isinstance(items.get("enum"), list):
                    info["items_enum"] = items["enum"]
        out[str(name)] = {k: v for k, v in info.items() if v is not None}
    return out


def _request_field_types(op: dict, spec: dict) -> dict:
    """Types of the request-body fields (OpenAPI 3 requestBody or Swagger 2 body parameter)."""
    body = (op.get("requestBody") or {}).get("content") or {}   # OpenAPI 3
    for media in body.values():
        types = _schema_field_types((media or {}).get("schema") or {}, spec)
        if types:
            return types
    for param in op.get("parameters") or []:                    # Swagger 2
        if isinstance(param, dict) and param.get("in") == "body":
            return _schema_field_types(param.get("schema") or {}, spec)
    return {}


def _schema_required(schema: dict, spec: dict) -> list[str]:
    """The `required` property names of a (possibly $ref'd) object schema."""
    if not isinstance(schema, dict):
        return []
    if "$ref" in schema:
        schema = _resolve(schema["$ref"], spec)
    req = schema.get("required")
    return [str(x) for x in req] if isinstance(req, list) else []


def _required_request_fields(op: dict, spec: dict) -> list[str]:
    """
    Names of the request-body fields the spec marks `required`. Empty means the
    body has no required field (every field is optional) — distinct from a body
    with no field info at all, which simply has no requestBody/body parameter.
    """
    body = (op.get("requestBody") or {}).get("content") or {}   # OpenAPI 3
    for media in body.values():
        schema = (media or {}).get("schema") or {}
        if schema:
            return _schema_required(schema, spec)
    for param in op.get("parameters") or []:                    # Swagger 2
        if isinstance(param, dict) and param.get("in") == "body":
            return _schema_required(param.get("schema") or {}, spec)
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


def ground_endpoints(base_url: str, *, session: requests.Session | None = None,
                     spec_url: str | None = None) -> list[dict]:
    """
    Return the running app's TRUE endpoints from its live OpenAPI spec, or [] if it
    serves none (caller then keeps the code-derived endpoints, ideally after a live
    path check).
    """
    spec = fetch_openapi(base_url, session=session, spec_url=spec_url)
    if not spec:
        return []
    eps = endpoints_from_spec(spec)
    logger.info("live_surface: grounded %d endpoint(s) from the running app's spec", len(eps))
    return eps


# Common REST/GraphQL paths to probe on an app that serves no OpenAPI spec. Covers
# bare and /api-prefixed common resource nouns (the dominant REST convention — e.g.
# a RealWorld backend mounts /api/articles, /api/profiles, /api/tags).
_COMMON_RESOURCES = ("users", "user", "articles", "posts", "comments", "profiles",
                     "tags", "products", "orders", "items", "todos", "accounts",
                     "login", "auth/login", "search", "admin", "me", "health",
                     "status", "graphql",
                     # more nouns real APIs use (found scanning linkding/CTFd/Healthchecks, 2026-10-17)
                     "bookmarks", "links", "notes", "tasks", "projects", "teams", "organizations", "files",
                     "images", "messages", "notifications", "events", "tickets", "customers", "invoices",
                     "payments", "categories", "groups", "roles", "settings", "keys", "tokens", "webhooks",
                     "reports", "assets", "checks", "channels", "challenges", "pages", "feed", "followers",
                     "sessions", "devices", "uploads", "documents", "jobs", "logs", "metrics", "version")
_COMMON_API_PATHS = tuple(dict.fromkeys(
    ["/" + r for r in _COMMON_RESOURCES]
    + ["/api/" + r for r in _COMMON_RESOURCES]
    + ["/api", "/api/v1"]))


# Pages an app commonly serves its own API reference on (read for path mentions when there is no spec).
_DOC_PAGES = ("/", "/docs", "/docs/", "/docs/api", "/docs/api/", "/api/docs", "/api/docs/",
              "/api", "/api/", "/documentation", "/help/api", "/developer", "/developers")
# A path-like token in page text: a leading slash then path chars. Captured, then filtered and verified.
_PATH_IN_TEXT = re.compile(r"(?<![\w.])(/[a-zA-Z0-9][a-zA-Z0-9_./:{}-]{1,120})")
# A doc line like "GET /api/v3/checks/" ties a method to a path — the strongest signal in a docs page.
_METHOD_PATH = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\b[\s`\"']{0,4}(/[a-zA-Z0-9][a-zA-Z0-9_./:{}-]{1,120})")
_STATIC_SUFFIX = (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2",
                  ".ttf", ".map", ".webp", ".mp4", ".pdf", ".xml", ".txt", ".zip")
_MAX_DOC_BYTES = 500_000
_MAX_CANDIDATES = 80


def _looks_like_api_path(p: str) -> bool:
    """A path worth verifying: not a static asset, not a fragment/template artefact, has real segments."""
    pl = p.lower()
    if pl.endswith(_STATIC_SUFFIX) or "//" in p or p.count("/") > 8 or any(c in p for c in "<>$"):
        return False
    segs = [s for s in p.split("/") if s]
    return bool(segs) and any(s.strip("{}.") for s in segs)   # at least one real (non-param, non-dot) segment


def paths_from_pages(base_url: str, model, *, session: requests.Session | None = None) -> list[dict]:
    """
    Read the app's own home and API-docs pages and pull out the paths they mention, then keep the ones
    that are actually live. Many apps that serve no OpenAPI still list their API in a docs page in plain
    text ("GET /api/v3/checks/"); reading it turns a one-endpoint scan into the real surface. GET-only,
    same host, bounded by page count and size. A path is kept only when `_route_exists` confirms it.
    """
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    existing = {normalize_path_params(str(e.get("path", ""))) for e in getattr(model, "endpoints", [])
                if isinstance(e, dict)}
    method_hint: dict[str, str] = {}
    candidates: list[str] = []
    seen_pages: set = set()
    for page in _DOC_PAGES:
        if page in seen_pages:
            continue
        seen_pages.add(page)
        try:
            r = sess.get(base + page, timeout=10, allow_redirects=False)
        except Exception:
            continue
        ct = str(getattr(r, "headers", {}) and r.headers.get("Content-Type", "")).lower()
        if getattr(r, "status_code", 404) >= 400 or ("html" not in ct and "text" not in ct and "json" not in ct):
            continue
        text = (getattr(r, "text", "") or "")[:_MAX_DOC_BYTES]
        for m, p in _METHOD_PATH.findall(text):
            method_hint.setdefault(p.rstrip(".,);"), m.upper())
        for p in _PATH_IN_TEXT.findall(text):
            candidates.append(p.rstrip(".,);"))
    out: list[dict] = []
    found: set = set()
    for p in dict.fromkeys(candidates):
        if len(out) >= _MAX_CANDIDATES:
            break
        np = normalize_path_params(p)
        if np in existing or np in found or not _looks_like_api_path(p):
            continue
        live_p = _live_path(base, p, sess)
        if live_p:
            np = normalize_path_params(live_p)
            if np in found or np in existing:
                continue
            found.add(np)
            out.append({"method": method_hint.get(p, "GET"), "path": np,
                        "auth_required": False, "source": "discovered-docs"})
    return out


def discover_endpoints(base_url: str, model, *, session: requests.Session | None = None) -> list[dict]:
    """
    Spec-less discovery. When the app serves no OpenAPI, (1) read its own home/docs pages for the paths
    they mention (`paths_from_pages`), then (2) probe a small set of common REST/GraphQL paths. Return the
    live ones not already in the model, so the probes get a real surface instead of only code inference.
    Discovery can be generous: the probes have their own zero-false-positive controls, so an extra path
    only costs a request, never a bogus finding.
    """
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    existing = {normalize_path_params(str(e.get("path", "")))
                for e in getattr(model, "endpoints", []) if isinstance(e, dict)}
    out: list[dict] = paths_from_pages(base_url, model, session=sess)
    seen = {d["path"] for d in out} | existing
    for p in _COMMON_API_PATHS:
        if p in seen or p + "/" in seen:
            continue
        found = _live_path(base, p, sess)
        if found:
            seen.add(found)
            out.append({"method": "GET", "path": found, "auth_required": False, "source": "discovered"})
    # "/x" and "/x/" are one resource: keep the slash form (what the app really serves; a POST to the bare
    # form can 500 on Django — APPEND_SLASH cannot redirect a POST).
    have = {d["path"] for d in out}
    out = [d for d in out if not (not d["path"].endswith("/") and d["path"] + "/" in have)]
    # Learn what each discovered resource accepts (OPTIONS is a safe method) and which need a credential.
    try:
        out = enrich_discovered(base, out, sess)
    except Exception as exc:                    # enrichment is a bonus; discovery itself must not fail
        logger.warning("live_surface: enrich_discovered failed: %s", exc)
    return out


_DRF_TYPE = {"integer": "integer", "float": "number", "decimal": "number", "boolean": "boolean",
             "string": "string", "url": "string", "email": "string", "slug": "string", "datetime": "string",
             "date": "string", "list": "array", "nested object": "object", "field": None, "choice": "string"}


def _drf_field_info(meta: dict) -> dict:
    """OPTIONS metadata of one field (Django REST Framework shape) -> the request_field_types shape."""
    t = str(meta.get("type", "")).lower()
    info: dict = {"type": _DRF_TYPE.get(t)}
    if t == "email":
        info["format"] = "email"
    elif t == "url":
        info["format"] = "uri"
    elif t == "datetime":
        info["format"] = "date-time"
    elif t == "date":
        info["format"] = "date"
    if t == "choice" and isinstance(meta.get("choices"), list):
        vals = [c.get("value") for c in meta["choices"] if isinstance(c, dict) and "value" in c]
        if vals:
            info["enum"] = vals
    if meta.get("read_only"):
        info["readOnly"] = True
    if meta.get("max_length"):
        info["max_length"] = meta["max_length"]
    return info


def enrich_discovered(base: str, endpoints: list[dict], sess) -> list[dict]:
    """
    For each endpoint found by guessing (no spec), ask the app itself, using only SAFE methods:
      • is a credential required? (the same GET without it answers 401/403) -> auth_required;
      • OPTIONS: which methods are allowed, and (Django REST Framework) the POST body schema — the fields,
        which are required / read-only, their types and choices -> a POST endpoint with request_fields;
      • a non-empty list response with ids -> the item route `<collection>/{id}` (GET).
    Found on linkding: the guesser saw GET collections only, so the create/read-by-id probes (BOLA, persistence,
    mass-assignment...) had nothing to work on and reported 'not applicable'.
    """
    # The control for "needs auth" is the same GET WITHOUT credentials; meaningless when we have none to drop.
    hdrs = getattr(sess, "headers", None)
    has_creds = isinstance(hdrs, dict) or hasattr(hdrs, "get")
    has_creds = has_creds and any(str(k).lower() in ("authorization", "x-api-key", "cookie", "x-auth-token", "api-key")
                                  for k in (hdrs or {})) or bool(getattr(sess, "cookies", None))
    anon = requests.Session() if has_creds else None
    out: list[dict] = []
    for e in endpoints:
        path = str(e.get("path", ""))
        if "{" in path or e.get("source") not in ("discovered", "discovered-docs"):
            out.append(e)
            continue
        if anon is not None:
            try:
                ar = anon.get(base + path, timeout=10, allow_redirects=False)
                e["auth_required"] = getattr(ar, "status_code", 0) in (401, 403)
            except Exception:
                pass
        out.append(e)
        try:
            orr = sess.options(base + path, timeout=10)
        except Exception:
            orr = None
        if orr is not None and getattr(orr, "status_code", 0) < 400:
            allow = {m.strip().upper() for m in str(orr.headers.get("Allow", "")).split(",") if m.strip()}
            post_meta = None
            try:
                post_meta = ((orr.json() or {}).get("actions") or {}).get("POST")
            except Exception:
                post_meta = None
            if "POST" in allow or post_meta:
                fields = post_meta if isinstance(post_meta, dict) else {}
                types = {k: _drf_field_info(v) for k, v in fields.items() if isinstance(v, dict)}
                out.append({"method": "POST", "path": path, "auth_required": e.get("auth_required", False),
                            "request_fields": [k for k, v in types.items() if not v.get("readOnly")],
                            "required_fields": [k for k, v in fields.items()
                                                if isinstance(v, dict) and v.get("required") and not v.get("read_only")],
                            "request_field_types": types, "response_fields": [], "source": "discovered-options"})
        # An item route, inferred from a real list response.
        try:
            lr = sess.get(base + path, timeout=10, allow_redirects=False)
            data = lr.json() if getattr(lr, "status_code", 0) == 200 else None
            rows = data if isinstance(data, list) else (data.get("results") if isinstance(data, dict) else None)
            if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "id" in rows[0]:
                item = path.rstrip("/") + "/{id}" + ("/" if path.endswith("/") else "")
                out.append({"method": "GET", "path": item, "auth_required": e.get("auth_required", False),
                            "source": "discovered-list"})
                # Which other methods does the ITEM route allow? (OPTIONS is safe.) Without DELETE here QAX could
                # never clean up the records its own probes created.
                try:
                    real = item.replace("{id}", str(rows[0]["id"]))
                    ir = sess.options(base + real, timeout=10)
                    allowed = {m.strip().upper() for m in str(ir.headers.get("Allow", "")).split(",") if m.strip()}
                except Exception:
                    allowed = set()
                for m in ("PUT", "PATCH", "DELETE"):
                    if m in allowed:
                        out.append({"method": m, "path": item, "auth_required": e.get("auth_required", False),
                                    "source": "discovered-list"})
        except Exception:
            pass
    return out


def _live_path(base: str, path: str, sess) -> str | None:
    """
    The live spelling of `path` ("/api/bookmarks" or "/api/bookmarks/"), or None. Frameworks differ on the
    trailing slash (Django wants it, most others do not); a 3xx that points at path + "/" means the slash form
    is the real one — and a POST to the slash-less form would be redirected and lose its body.
    """
    probe = re.sub(r"\{[^}]+\}|:([A-Za-z_]+)", "1", path)
    try:
        r = _get_patiently(sess, base + probe)
    except Exception:
        return None
    code = getattr(r, "status_code", 404)
    if 300 <= code < 400:
        loc = str(getattr(r, "headers", {}).get("Location", "")).split("?")[0].rstrip()
        if loc.endswith(probe + "/"):
            return path + "/" if _route_exists(base, path + "/", sess) else path
    if code not in (401, 403, 404):
        return path
    if code == 404 and not path.endswith("/") and _route_exists(base, path + "/", sess):
        return path + "/"
    return None


def _get_patiently(sess, url: str, *, tries: int = 2):
    """GET with ONE bounded wait on HTTP 429 (honouring Retry-After up to 10 s) — discovery must never hammer a
    rate-limited app or read its 'slow down' as 'no such route'."""
    import time as _t
    r = sess.get(url, timeout=10, allow_redirects=False)
    for _ in range(tries - 1):
        if getattr(r, "status_code", 0) != 429:
            break
        try:
            wait = min(10.0, float(r.headers.get("Retry-After", "1")))
        except Exception:
            wait = 1.0
        _t.sleep(max(0.5, wait))
        r = sess.get(url, timeout=10, allow_redirects=False)
    return r


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
        r = _get_patiently(sess, base + probe)
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
