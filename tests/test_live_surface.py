"""
test_live_surface.py — spec parsing, especially the base-path prefix
====================================================================
Pure parsing over spec dicts; no network.

Run with:
    pytest tests/test_live_surface.py -v
"""

from unittest.mock import MagicMock

from services.live_surface import (_base_path, _join_base, endpoints_from_spec,
                                    normalize_path_params, recover_base_path)
from services.results import AppModel


# ── path-parameter normalization (framework spellings → {name}) ──────────────

def test_normalize_flask_and_colon_params():
    assert normalize_path_params("/items/<item_id>") == "/items/{item_id}"
    assert normalize_path_params("/users/<int:id>/posts/<slug>") == "/users/{id}/posts/{slug}"
    assert normalize_path_params("/items/:id") == "/items/{id}"           # Express/Gin/Rails
    assert normalize_path_params("/a/<path:p>") == "/a/{p}"
    assert normalize_path_params("/users/{username}") == "/users/{username}"  # OpenAPI passthrough
    assert normalize_path_params("/health") == "/health"                  # no params


# ── base-path recovery (no OpenAPI) ──────────────────────────────────────────

class _PrefixSession:
    """A live app mounted under `mount` (e.g. /api): only those routes are not 404."""
    def __init__(self, mount):
        self.mount = mount

    def get(self, url, timeout=None, allow_redirects=None):
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        path = "/" + path if not path.startswith("/") else path
        exists = path.startswith(self.mount + "/") or path == self.mount
        # 200 = a handler ran (definitive). 401/403/404 would NOT count as existence.
        return MagicMock(status_code=200 if exists else 404)


def test_recover_base_path_prepends_missing_prefix():
    model = AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/users"},
        {"method": "GET", "path": "/articles/{slug}"},
    ])
    pref = recover_base_path(model, "http://x", session=_PrefixSession("/api"))
    assert pref == "/api"
    assert {e["path"] for e in model.endpoints} == {"/api/users", "/api/articles/{slug}"}


def test_recover_base_path_noop_when_paths_already_resolve():
    model = AppModel(root="/x", endpoints=[{"method": "GET", "path": "/users/v1"}])
    pref = recover_base_path(model, "http://x", session=_PrefixSession(""))  # root mount
    # "" mount means every path resolves as-is → no prefix applied
    assert pref == "" and model.endpoints[0]["path"] == "/users/v1"


class _SpringLikeSession:
    """Real routes live at root (200); the security filter answers 401 for anything
    under /api (and for unmatched paths). 401 must NOT be read as 'route exists'."""
    def get(self, url, timeout=None, allow_redirects=None):
        path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
        if path.startswith("/api/"):
            return MagicMock(status_code=401)
        if path in ("/articles", "/tags", "/articles/1"):
            return MagicMock(status_code=200)
        return MagicMock(status_code=404)


def test_recover_base_path_not_fooled_by_401_on_unmatched():
    model = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/articles"}, {"method": "GET", "path": "/tags"}])
    pref = recover_base_path(model, "http://x", session=_SpringLikeSession())
    assert pref == ""                                      # /api 401s are not evidence
    assert {e["path"] for e in model.endpoints} == {"/articles", "/tags"}


# ── base-path extraction ─────────────────────────────────────────────────────

def test_base_path_openapi3_server_path():
    assert _base_path({"servers": [{"url": "/api"}]}) == "/api"


def test_base_path_openapi3_server_full_url():
    assert _base_path({"servers": [{"url": "http://host:3000/api/v2"}]}) == "/api/v2"


def test_base_path_swagger2_basepath():
    assert _base_path({"basePath": "/v1"}) == "/v1"


def test_base_path_none_when_root_or_empty():
    assert _base_path({"servers": [{"url": "/"}]}) == ""
    assert _base_path({"servers": [{"url": ""}]}) == ""   # VAmPI shape
    assert _base_path({}) == ""


# ── joining without doubling ─────────────────────────────────────────────────

def test_join_prepends_base():
    assert _join_base("/api", "/users/") == "/api/users/"


def test_join_root_becomes_base():
    assert _join_base("/api", "/") == "/api"


def test_join_no_double_when_already_prefixed():
    assert _join_base("/api", "/api/users") == "/api/users"


def test_join_empty_base_is_identity():
    assert _join_base("", "/users/v1") == "/users/v1"


# ── end-to-end: endpoints carry the base path ────────────────────────────────

def test_endpoints_from_spec_applies_base_path():
    spec = {
        "openapi": "3.0.0",
        "servers": [{"url": "/api"}],
        "security": [{"bearerAuth": []}],
        "paths": {
            "/users/": {"get": {}, "post": {}},
            "/users/{id}": {"get": {}},
        },
    }
    paths = {(e["method"], e["path"]) for e in endpoints_from_spec(spec)}
    assert ("GET", "/api/users/") in paths
    assert ("POST", "/api/users/") in paths
    assert ("GET", "/api/users/{id}") in paths


def test_endpoints_from_spec_no_base_unchanged():
    spec = {"openapi": "3.0.0", "servers": [{"url": ""}],
            "paths": {"/books/v1": {"get": {}}}}
    assert [e["path"] for e in endpoints_from_spec(spec)] == ["/books/v1"]


def test_endpoints_record_required_fields_swagger2():
    # Swagger 2 body param: `required` on the schema drives validation coverage.
    spec = {
        "swagger": "2.0", "basePath": "/api/v1",
        "paths": {"/orgs": {"post": {"parameters": [
            {"in": "body", "name": "body", "schema": {
                "type": "object",
                "properties": {"username": {"type": "string"},
                               "description": {"type": "string"}},
                "required": ["username"]}}]}}},
    }
    e = endpoints_from_spec(spec)[0]
    assert set(e["request_fields"]) == {"username", "description"}
    assert e["required_fields"] == ["username"]


def test_endpoints_required_fields_empty_when_none_required():
    spec = {"openapi": "3.0.0", "paths": {"/markdown": {"post": {
        "requestBody": {"content": {"application/json": {"schema": {
            "type": "object",
            "properties": {"Context": {"type": "string"}}}}}}}}}}
    e = endpoints_from_spec(spec)[0]
    assert e["request_fields"] == ["Context"] and e["required_fields"] == []


def test_gitea_spec_path_is_discovered():
    # Gitea/Gogs serve their Swagger 2.0 spec at /swagger.v1.json.
    from services.live_surface import _SPEC_PATHS
    assert "/swagger.v1.json" in _SPEC_PATHS


# ── B5: spec-less discovery of live common paths ────────────────────────────────

class _DiscoverySession:
    """/health and /api are live (200); everything else 404s."""
    def get(self, url, timeout=None, allow_redirects=None):
        path = "/" + url.split("://", 1)[-1].split("/", 1)[-1] if "/" in url.split("://", 1)[-1] else "/"
        return MagicMock(status_code=200 if path in ("/health", "/api") else 404)


def test_discover_endpoints_finds_live_commons_excludes_known_and_dead():
    from services.live_surface import discover_endpoints
    model = AppModel(root="/x", endpoints=[{"method": "GET", "path": "/health"}])  # already known
    disc = discover_endpoints("http://x", model, session=_DiscoverySession())
    paths = {d["path"] for d in disc}
    assert "/api" in paths            # live + not already in model → discovered
    assert "/health" not in paths     # already modelled → skipped
    assert "/users" not in paths      # 404 → not live → excluded
    assert all(d["method"] == "GET" and d["source"] == "discovered" for d in disc)


def test_endpoints_record_request_field_types_swagger2():
    spec = {"swagger": "2.0", "paths": {"/things": {"post": {"parameters": [
        {"in": "body", "name": "body", "schema": {"$ref": "#/definitions/Thing"}}]}}},
        "definitions": {"Thing": {"type": "object", "properties": {
            "title": {"type": "string", "maxLength": 50},
            "active": {"type": "boolean"},
            "labels": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "starts_at": {"type": "string", "format": "date-time"},
            "id": {"type": "integer", "readOnly": True},
            "kind": {"type": "string", "enum": ["a", "b"]}}}}}
    t = endpoints_from_spec(spec)[0]["request_field_types"]
    assert t["active"] == {"type": "boolean"}
    assert t["labels"] == {"type": "array", "min_items": 1, "items_type": "string"}
    assert t["starts_at"] == {"type": "string", "format": "date-time"}
    assert t["id"]["readOnly"] is True and t["kind"]["enum"] == ["a", "b"]
    assert t["title"]["max_length"] == 50


def test_request_field_types_empty_without_a_body_schema():
    assert endpoints_from_spec({"openapi": "3.0.0", "paths": {"/x": {"get": {}}}})[0]["request_field_types"] == {}


# ── A3: read the app's own docs pages for endpoints (spec-less) ─────────────────

class _DocsSession:
    """A spec-less app whose /docs/api/ page lists its API in text; the listed paths are live."""
    _DOCS = ("The REST API:\n"
             "GET /api/v3/checks/  list checks\n"
             "POST /api/v3/checks/  create\n"
             "GET /api/v3/channels/  list channels\n"
             "See also /static/app.css and /favicon.ico\n")
    _LIVE = {"/api/v3/checks/", "/api/v3/channels/"}

    def get(self, url, timeout=None, allow_redirects=None, headers=None, params=None):
        from urllib.parse import urlparse
        path = urlparse(url).path
        if path in ("/docs/api/", "/docs/api"):
            return MagicMock(status_code=200, text=self._DOCS, headers={"Content-Type": "text/html"})
        if path == "/":
            return MagicMock(status_code=200, text="<a href='/docs/api/'>API</a>", headers={"Content-Type": "text/html"})
        if path in self._LIVE:
            return MagicMock(status_code=200, text="{}", headers={"Content-Type": "application/json"})
        return MagicMock(status_code=404, text="nope", headers={"Content-Type": "text/html"})


def test_paths_from_pages_reads_listed_live_endpoints_and_skips_assets():
    from services.live_surface import paths_from_pages
    disc = paths_from_pages("http://x", AppModel(root="/x", endpoints=[]), session=_DocsSession())
    paths = {d["path"] for d in disc}
    assert "/api/v3/checks/" in paths and "/api/v3/channels/" in paths
    assert "/static/app.css" not in paths and "/favicon.ico" not in paths   # assets filtered
    checks = next(d for d in disc if d["path"] == "/api/v3/checks/")
    assert checks["method"] == "GET" and checks["source"] == "discovered-docs"   # method taken from the doc line


def test_paths_from_pages_skips_what_is_already_modelled():
    from services.live_surface import paths_from_pages
    model = AppModel(root="/x", endpoints=[{"method": "GET", "path": "/api/v3/checks/"}])
    paths = {d["path"] for d in paths_from_pages("http://x", model, session=_DocsSession())}
    assert "/api/v3/checks/" not in paths and "/api/v3/channels/" in paths


def test_looks_like_api_path_filters():
    from services.live_surface import _looks_like_api_path
    assert _looks_like_api_path("/api/v3/checks/")
    assert not _looks_like_api_path("/x.css") and not _looks_like_api_path("/a//b")
    assert not _looks_like_api_path("/{}/{}")


def test_read_only_fields_are_not_offered_as_request_fields():
    """Regression (microblog-api): server-owned readOnly fields must not be sent, or strict apps 400 and four
    probes silently cannot run."""
    spec = {"openapi": "3.0.0", "paths": {"/users": {"post": {"requestBody": {"content": {"application/json": {
        "schema": {"type": "object", "required": ["username", "password"], "properties": {
            "id": {"type": "integer", "readOnly": True}, "username": {"type": "string"},
            "password": {"type": "string"}, "created_at": {"type": "string", "readOnly": True}}}}}}}}}}
    e = endpoints_from_spec(spec)[0]
    assert e["request_fields"] == ["username", "password"]
    assert e["request_field_types"]["id"]["readOnly"] is True      # kept for the probes that need to know


def test_spec_paths_cover_versioned_api_prefixes():
    from services.live_surface import _SPEC_PATHS
    for p in ("/apispec.json", "/api/v1/swagger.json", "/api/v1/openapi.json", "/swagger.v1.json"):
        assert p in _SPEC_PATHS


# ── authenticated, slash-aware, 429-patient discovery (found on linkding, A4) ──────────────────

class _DjangoLike:
    """/api/bookmarks/ needs a token (401 anonymous) and a trailing slash (the bare path 301s to it)."""
    def __init__(self, token=None): self.headers = {}; self.cookies = {}; self.calls = 0

    def get(self, url, timeout=None, allow_redirects=None, **k):
        from urllib.parse import urlparse
        self.calls += 1
        path = urlparse(url).path
        authed = self.headers.get("Authorization") == "Token abc"
        if path == "/api/bookmarks":
            return MagicMock(status_code=301, headers={"Location": "http://x/api/bookmarks/"}, text="")
        if path == "/api/bookmarks/":
            return MagicMock(status_code=200 if authed else 401, headers={}, text="[]")
        return MagicMock(status_code=404, headers={}, text="")


def test_discovery_finds_token_protected_slash_endpoints_only_with_credentials():
    from services.live_surface import authed_session, discover_endpoints
    anon = _DjangoLike()
    assert "/api/bookmarks/" not in {d["path"] for d in discover_endpoints("http://x", AppModel(root="/x", endpoints=[]), session=anon)}
    s = authed_session({"token": "abc", "header": "Authorization", "scheme": "Token"})
    assert s.headers["Authorization"] == "Token abc"
    authed = _DjangoLike(); authed.headers = s.headers
    got = {d["path"] for d in discover_endpoints("http://x", AppModel(root="/x", endpoints=[]), session=authed)}
    assert "/api/bookmarks/" in got and "/api/bookmarks" not in got      # the slash form that really works


def test_a_429_is_waited_out_once_not_read_as_no_such_route(monkeypatch):
    import services.live_surface as ls
    monkeypatch.setattr("time.sleep", lambda s: None)
    class S:
        def __init__(self): self.n = 0
        def get(self, url, timeout=None, allow_redirects=None, **k):
            if "qax-nonexistent" in url:                         # the catch-all control: an unknown path is a 404
                return MagicMock(status_code=404, headers={}, text="")
            self.n += 1
            return MagicMock(status_code=429 if self.n == 1 else 200, headers={"Retry-After": "1"}, text="[]")
    s = S()
    assert ls._route_exists("http://x", "/api/things", s) is True and s.n == 2


def test_authed_session_carries_a_cookie():
    from services.live_surface import authed_session
    assert authed_session({"cookie": "sessionid=abc; csrftoken=xyz"}).cookies.get("sessionid") == "abc"


class _DrfLike:
    """A DRF collection: needs a token, OPTIONS describes the POST body, the list has items with ids."""
    def __init__(self): self.headers = {"Authorization": "Token abc"}; self.cookies = {}
    def get(self, url, timeout=None, allow_redirects=None, **k):
        from urllib.parse import urlparse
        p = urlparse(url).path
        if p == "/api/bookmarks/":
            ok = self.headers.get("Authorization") == "Token abc"
            return MagicMock(status_code=200 if ok else 401, headers={}, text="[]",
                             json=lambda: [{"id": 1, "url": "https://a"}])
        return MagicMock(status_code=404, headers={}, text="", json=lambda: None)
    def options(self, url, timeout=None, **k):
        meta = {"actions": {"POST": {"id": {"type": "integer", "read_only": True},
                                     "url": {"type": "url", "required": True},
                                     "notes": {"type": "string", "required": False},
                                     "unread": {"type": "boolean", "required": False}}}}
        return MagicMock(status_code=200, headers={"Allow": "GET, POST, HEAD, OPTIONS"}, json=lambda: meta)


def test_enrichment_learns_post_schema_item_route_and_auth_from_the_app_itself(monkeypatch):
    import services.live_surface as ls
    class Anon:                                          # the credential-free control session
        def get(self, url, **k): return MagicMock(status_code=401)
    monkeypatch.setattr(ls.requests, "Session", lambda: Anon())
    found = ls.enrich_discovered("http://x", [{"method": "GET", "path": "/api/bookmarks/",
                                               "auth_required": False, "source": "discovered"}], _DrfLike())
    by = {(e["method"], e["path"]): e for e in found}
    assert by[("GET", "/api/bookmarks/")]["auth_required"] is True            # 401 without the token
    post = by[("POST", "/api/bookmarks/")]
    assert post["request_fields"] == ["url", "notes", "unread"] and "id" not in post["request_fields"]   # read-only dropped
    assert post["required_fields"] == ["url"] and post["request_field_types"]["unread"]["type"] == "boolean"
    assert ("GET", "/api/bookmarks/{id}/") in by                                # item route from the list


def test_item_route_methods_come_from_options_so_cleanup_can_delete(monkeypatch):
    import services.live_surface as ls
    class S(_DrfLike):
        def options(self, url, timeout=None, **k):
            if url.endswith("/api/bookmarks/1/"):
                return MagicMock(status_code=200, headers={"Allow": "GET, PUT, PATCH, DELETE, OPTIONS"}, json=lambda: {})
            return super().options(url, timeout=timeout)
    class Anon:
        def get(self, url, **k): return MagicMock(status_code=401)
    monkeypatch.setattr(ls.requests, "Session", lambda: Anon())
    found = ls.enrich_discovered("http://x", [{"method": "GET", "path": "/api/bookmarks/",
                                               "auth_required": False, "source": "discovered"}], S())
    assert ("DELETE", "/api/bookmarks/{id}/") in {(e["method"], e["path"]) for e in found}


def test_slash_and_bare_twins_collapse_to_the_slash_form():
    """A POST to /api/bookmarks (no slash) 500s on Django; only the slash form may survive discovery."""
    import services.live_surface as ls
    class S:
        headers = {}; cookies = {}
        def get(self, url, timeout=None, allow_redirects=None, **k):
            from urllib.parse import urlparse
            p = urlparse(url).path
            if p == "/api/bookmarks":
                return MagicMock(status_code=301, headers={"Location": "http://x/api/bookmarks/"}, text="")
            if p == "/api/bookmarks/":
                return MagicMock(status_code=200, headers={}, text="[]", json=lambda: [])
            return MagicMock(status_code=404, headers={}, text="")
        def options(self, *a, **k): return MagicMock(status_code=405, headers={})
    got = [d["path"] for d in ls.discover_endpoints("http://x", AppModel(root="/x", endpoints=[]), session=S())]
    assert "/api/bookmarks/" in got and "/api/bookmarks" not in got


def test_endpoint_list_parser_reads_methods_paths_fields_and_required_marks():
    from services.live_surface import parse_endpoint_lines
    eps = parse_endpoint_lines("""
        # notes
        GET    /api/collections/notes/records
        post   /api/collections/notes/records   title!,body     # create
        DELETE /api/collections/notes/records/:id
        FETCH  /nope
        GET    relative/path
    """)
    by = {(e["method"], e["path"]): e for e in eps}
    assert set(by) == {("GET", "/api/collections/notes/records"), ("POST", "/api/collections/notes/records"),
                       ("DELETE", "/api/collections/notes/records/{id}")}           # :id normalised, junk skipped
    post = by[("POST", "/api/collections/notes/records")]
    assert post["request_fields"] == ["title", "body"] and post["required_fields"] == ["title"]
    assert all(e["source"] == "supplied" for e in eps)


# ── catch-all origins (a single-page app answers every path with the same page) ──────────────────

class _SpaLike:
    """Every path is the same index.html (200) — except /api/things, a real JSON route."""
    INDEX = "<!doctype html><html><div id=root></div></html>"
    def __init__(self): self.headers = {}; self.cookies = {}
    def get(self, url, timeout=None, allow_redirects=None, **k):
        from urllib.parse import urlparse
        p = urlparse(url).path
        if p == "/api/users":
            return MagicMock(status_code=200, headers={"Content-Type": "application/json"}, text='[{"id": 1}]',
                             json=lambda: [{"id": 1}])
        return MagicMock(status_code=200, headers={"Content-Type": "text/html; charset=utf-8"}, text=self.INDEX)
    def options(self, *a, **k): return MagicMock(status_code=405, headers={})


def test_a_catch_all_page_is_not_an_endpoint_but_a_real_json_route_still_is():
    import services.live_surface as ls
    sess = _SpaLike()
    got = {d["path"] for d in ls.discover_endpoints("http://x", AppModel(root="/x", endpoints=[]), session=sess)}
    assert got == {"/api/users", "/api/users/{id}"}     # the real route + its item route; not 126 look-alikes
    assert sess._qax_catchall is not None               # the orchestrator reports "every path is the same page"


def test_a_normal_404_origin_has_no_catch_all():
    import services.live_surface as ls
    class S:
        def get(self, url, timeout=None, allow_redirects=None, **k): return MagicMock(status_code=404, headers={}, text="")
    assert ls.catchall_baseline("http://x", S()) is None
