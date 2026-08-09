"""
test_auth_recipe.py — deriving an app's auth flow from the AppModel
===================================================================
These lock in the generalization: the probes/templates must read the auth shape
from the model, not assume TaskFlow's.

Run with:
    pytest tests/test_auth_recipe.py -v
"""

from core.auth_recipe import derive_auth_recipe, field_names, subject_candidates
from services.results import AppModel


def test_field_names_handles_strings_and_dicts():
    assert field_names(["a", {"name": "b"}, {"x": 1}, None]) == ["a", "b"]


def test_derive_taskflow_shape():
    model = AppModel(endpoints=[
        {"method": "POST", "path": "/auth/register",
         "request_fields": ["email", "password"]},
        {"method": "POST", "path": "/auth/login",
         "request_fields": ["email", "password"], "response_fields": ["access_token", "token_type"]},
        {"method": "GET", "path": "/auth/me", "response_fields": ["id", "email"]},
    ])
    r = derive_auth_recipe(model)
    assert r["register_path"] == "/auth/register"
    assert r["login_path"] == "/auth/login"
    assert r["identity_path"] == "/auth/me"
    assert r["id_field"] == "email"
    assert r["secret_field"] == "password"
    assert r["token_field"] == "access_token"


def test_derive_a_different_shape():
    # A different app: username auth, /users/signup, /sessions, jwt field.
    model = AppModel(endpoints=[
        {"method": "POST", "path": "/api/users/signup",
         "request_fields": [{"name": "username"}, {"name": "passwd"}]},
        {"method": "POST", "path": "/api/sessions",
         "response_fields": ["jwt", "expires"]},
        {"method": "GET", "path": "/api/profile", "response_fields": ["username"]},
    ])
    r = derive_auth_recipe(model)
    assert r["register_path"] == "/api/users/signup"
    assert r["login_path"] == "/api/sessions"
    assert r["identity_path"] == "/api/profile"
    assert r["id_field"] == "username"
    assert r["secret_field"] == "passwd"
    assert r["token_field"] == "jwt"


def test_derive_by_secret_field_when_path_is_unconventional():
    # A phone/pin app whose account creation is POST /v2/accounts (no "register").
    model = AppModel(endpoints=[
        {"method": "POST", "path": "/v2/accounts", "request_fields": ["phone", "pin"]},
        {"method": "POST", "path": "/v2/auth/token", "response_fields": ["bearer_token"]},
        {"method": "GET", "path": "/v2/accounts/current", "response_fields": ["phone"]},
    ])
    r = derive_auth_recipe(model)
    assert r["register_path"] == "/v2/accounts"    # found via the "account" keyword
    assert r["id_field"] == "phone"
    assert r["secret_field"] == "pin"
    assert r["token_field"] == "bearer_token"


def test_derive_falls_back_when_model_is_silent():
    r = derive_auth_recipe(AppModel(endpoints=[]))
    assert r["register_path"] == "/auth/register"
    assert r["id_field"] == "email"
    assert r["secret_field"] == "password"
    assert r["token_field"] == "access_token"


def test_subject_candidates_try_email_then_id():
    subs = subject_candidates("v@x.com", {"id": 42, "email": "v@x.com"})
    assert subs[0] == "v@x.com"          # login identifier first
    assert 42 in subs                    # numeric id next
    assert "v" in subs                   # email local-part fallback
