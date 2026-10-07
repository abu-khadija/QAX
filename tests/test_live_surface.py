"""
test_live_surface.py — spec parsing, especially the base-path prefix
====================================================================
Pure parsing over spec dicts; no network.

Run with:
    pytest tests/test_live_surface.py -v
"""

from services.live_surface import _base_path, _join_base, endpoints_from_spec


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
