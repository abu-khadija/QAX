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
