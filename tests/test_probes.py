"""
test_probes.py — active exploit probes + ProbeService
=====================================================
The JWT signer is checked against a real verifier when available; the network
probes run against a fake in-process app so no live server is needed.

Run with:
    pytest tests/test_probes.py -v
"""

from unittest.mock import MagicMock

import json as _json

from execution.probes import (_auth_headers, _resolve_token, bola_probe, cookie_flags_probe,
                               crash_fuzz_probe, debug_disclosure_probe, error_handling_probe,
                               excessive_data_probe, forge_jwt_probe, sign_jwt_hs, sqli_probe)
from services.context import QAXContext
from services.probe_service import ProbeService
from services.results import AppModel, ProbeResult

SECRET = "change-this-to-a-long-random-secret"


def _ctx() -> QAXContext:
    return QAXContext(anthropic_key="k", project="")


def _model_with_secret() -> AppModel:
    return AppModel(
        root="/x",
        endpoints=[{"method": "POST", "path": "/auth/register", "auth_required": False},
                   {"method": "GET", "path": "/auth/me", "auth_required": True}],
        config=[{"key": "jwt_secret", "default": SECRET, "is_secret": True, "risk": "forgeable"},
                {"key": "jwt_algorithm", "default": "HS256", "is_secret": False, "risk": ""}],
    )


# ── the pure-stdlib signer ───────────────────────────────────────────────────

def test_sign_jwt_matches_a_real_verifier():
    token = sign_jwt_hs(SECRET, "HS256", {"sub": "a@b.com", "exp": 9999999999})
    assert token and token.count(".") == 2
    try:
        from jose import jwt  # available in the exec venv; skip if absent
    except Exception:
        import pytest
        pytest.skip("jose not installed in this interpreter")
    decoded = jwt.decode(token, SECRET, algorithms=["HS256"])
    assert decoded["sub"] == "a@b.com"


def test_sign_rejects_unknown_algorithm():
    assert sign_jwt_hs(SECRET, "RS256", {"sub": "x"}) is None


# ── forge_jwt probe against a fake app ───────────────────────────────────────

class _FakeSession:
    """Simulates an app that trusts any token signed with the leaked secret."""
    def __init__(self, secret=SECRET, vulnerable=True):
        self.secret = secret
        self.vulnerable = vulnerable
        self.registered = set()

    def post(self, url, json=None, timeout=None):
        if "register" in url:
            self.registered.add(json["email"])
        return MagicMock(status_code=201, text="{}")

    def get(self, url, headers=None, timeout=None):
        token = (headers or {}).get("Authorization", "").replace("Bearer ", "")
        if not self.vulnerable:
            return MagicMock(status_code=401, text="unauthorized")
        # verify the forged token with the real secret to find the subject
        try:
            from jose import jwt
            sub = jwt.decode(token, self.secret, algorithms=["HS256"]).get("sub")
        except Exception:
            return MagicMock(status_code=401, text="bad")
        ok = sub in self.registered
        return MagicMock(status_code=200 if ok else 401, text=(sub if ok else "no"))


def _skip_without_jose():
    try:
        import jose  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("jose not installed in this interpreter")


def test_forge_jwt_proves_takeover_on_vulnerable_app():
    _skip_without_jose()
    f = forge_jwt_probe(_model_with_secret(), "http://x", session=_FakeSession(vulnerable=True))
    assert f is not None and f.exploited is True
    assert f.severity == "P1"
    assert "forged" in f.evidence.lower()


def test_forge_jwt_fails_on_safe_app():
    _skip_without_jose()
    f = forge_jwt_probe(_model_with_secret(), "http://x", session=_FakeSession(vulnerable=False))
    assert f is not None and f.exploited is False


def test_forge_jwt_none_without_secret():
    model = AppModel(root="/x", config=[{"key": "port", "default": 8000, "is_secret": False}])
    assert forge_jwt_probe(model, "http://x", session=_FakeSession()) is None


# ── crash_fuzz probe ─────────────────────────────────────────────────────────

def test_crash_fuzz_flags_5xx():
    class Sess:
        def post(self, url, json=None, timeout=None):
            return MagicMock(status_code=500, text="err")
    out = crash_fuzz_probe(_model_with_secret(), "http://x", session=Sess())
    assert len(out) == 1 and out[0].exploited and out[0].severity == "P2"


def test_crash_fuzz_quiet_when_handled():
    class Sess:
        def post(self, url, json=None, timeout=None):
            return MagicMock(status_code=422, text="too long")
    assert crash_fuzz_probe(_model_with_secret(), "http://x", session=Sess()) == []


# ── sqli probe (benign-vs-quote control) ─────────────────────────────────────

def _model_lookup() -> AppModel:
    return AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/users/v1/{username}", "auth_required": False},
    ])


class _SqliSession:
    """A lookup where a single quote breaks the query only when vulnerable."""
    def __init__(self, vulnerable=True):
        self.vulnerable = vulnerable

    def get(self, url, headers=None, timeout=None):
        if url.endswith("'"):
            if self.vulnerable:
                return MagicMock(status_code=500, text="sqlite3.OperationalError: near syntax error")
            return MagicMock(status_code=404, text="not found")
        return MagicMock(status_code=404, text="not found")

    def post(self, *a, **k):
        return MagicMock(status_code=200, text="{}")


def test_sqli_flags_quote_that_breaks_the_query():
    out = sqli_probe(_model_lookup(), "http://x", session=_SqliSession(vulnerable=True))
    assert len(out) == 1 and out[0].exploited and out[0].severity == "P1"
    assert "injection" in out[0].title.lower()


def test_sqli_quiet_when_parameterized():
    assert sqli_probe(_model_lookup(), "http://x", session=_SqliSession(vulnerable=False)) == []


class _Quote500NoSqlSession:
    """The quote 500s but with no SQL error text — robustness, not injection."""
    def get(self, url, headers=None, timeout=None):
        if url.endswith("'"):
            return MagicMock(status_code=500, text="Internal Server Error")
        return MagicMock(status_code=404, text="not found")

    def post(self, *a, **k):
        return MagicMock(status_code=200, text="{}")


def test_sqli_500_without_sql_error_is_robustness_not_injection():
    out = sqli_probe(_model_lookup(), "http://x", session=_Quote500NoSqlSession())
    assert len(out) == 1
    assert out[0].probe == "input-robustness" and out[0].severity == "P3"
    assert "not confirmed" in out[0].evidence.lower()


# ── bola probe (two-user control) ────────────────────────────────────────────

def _model_owned_resource() -> AppModel:
    return AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/users/v1/register", "auth_required": False,
         "request_fields": ["username", "password", "email"]},
        {"method": "POST", "path": "/users/v1/login", "auth_required": False,
         "request_fields": ["username", "password"], "response_fields": ["auth_token"]},
        {"method": "POST", "path": "/books/v1", "auth_required": True,
         "request_fields": ["book_title", "secret"]},
        {"method": "GET", "path": "/books/v1/{book_title}", "auth_required": True},
    ])


class _BolaSession:
    """Auth + an owned object; reads ignore ownership only when vulnerable."""
    def __init__(self, vulnerable=True):
        self.vulnerable = vulnerable
        self.books = {}  # title -> {"secret", "owner"}

    def _who(self, headers):
        tok = (headers or {}).get("Authorization", "").replace("Bearer ", "")
        return tok[4:] if tok.startswith("tok:") else None

    def post(self, url, json=None, headers=None, timeout=None):
        if "register" in url:
            return MagicMock(status_code=200, text="{}")
        if "login" in url:
            user = (json or {}).get("username")
            return MagicMock(status_code=200, json=lambda: {"auth_token": f"tok:{user}"}, text="{}")
        if url.rstrip("/").endswith("/books/v1"):
            owner = self._who(headers)
            if owner is None:
                return MagicMock(status_code=401, text="no")
            self.books[(json or {}).get("book_title")] = {"secret": (json or {}).get("secret"), "owner": owner}
            return MagicMock(status_code=200, text="{}")
        return MagicMock(status_code=404, text="no")

    def get(self, url, headers=None, timeout=None):
        who = self._who(headers)
        if who is None:
            return MagicMock(status_code=401, text="no")
        title = url.rsplit("/", 1)[-1]
        b = self.books.get(title)
        if not b:
            return MagicMock(status_code=404, text="not found")
        if self.vulnerable or b["owner"] == who:
            return MagicMock(status_code=200,
                             text=_json.dumps({"book_title": title, "secret": b["secret"], "owner": b["owner"]}))
        return MagicMock(status_code=404, text="not found")


def test_bola_flags_cross_user_read():
    out = bola_probe(_model_owned_resource(), "http://x", session=_BolaSession(vulnerable=True))
    assert len(out) == 1 and out[0].exploited and out[0].severity == "P1"
    assert "authorization" in out[0].title.lower()


def test_bola_quiet_when_ownership_enforced():
    assert bola_probe(_model_owned_resource(), "http://x", session=_BolaSession(vulnerable=False)) == []


# ── excessive-data probe (response-body inspection) ──────────────────────────

def _model_listings() -> AppModel:
    return AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/users/v1/_debug", "auth_required": False},
        {"method": "GET", "path": "/users/v1", "auth_required": False},
    ])


class _EdeSession:
    def __init__(self, leak=True):
        self.leak = leak

    def get(self, url, headers=None, timeout=None):
        if url.endswith("_debug") and self.leak:
            return MagicMock(status_code=200, json=lambda: {
                "users": [{"username": "a", "email": "a@x.com", "password": "pass1"}]})
        return MagicMock(status_code=200, json=lambda: {"users": ["a", "b"]})


def test_excessive_data_flags_password_in_response():
    out = excessive_data_probe(_model_listings(), "http://x", session=_EdeSession(leak=True))
    assert len(out) == 1 and out[0].exploited and out[0].severity == "P1"
    assert "password" in out[0].evidence.lower()
    assert out[0].target == "/users/v1/_debug"


def test_excessive_data_quiet_on_clean_responses():
    assert excessive_data_probe(_model_listings(), "http://x", session=_EdeSession(leak=False)) == []


# ── error-handling probe (unknown route should 404, not 5xx) ─────────────────

def test_error_handling_flags_5xx_on_nonsense_route():
    class Sess:
        def get(self, url, headers=None, timeout=None):
            return MagicMock(status_code=500, text="boom")
    out = error_handling_probe(AppModel(root="/x"), "http://x", session=Sess())
    assert len(out) == 1 and out[0].probe == "error-handling" and out[0].severity == "P3"


def test_error_handling_quiet_on_404():
    class Sess:
        def get(self, url, headers=None, timeout=None):
            return MagicMock(status_code=404, text="not found")
    assert error_handling_probe(AppModel(root="/x"), "http://x", session=Sess()) == []


# ── cookie-flags probe ───────────────────────────────────────────────────────

def _model_cookie_auth() -> AppModel:
    return AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/auth/register", "auth_required": False,
         "request_fields": ["email", "password"]},
        {"method": "POST", "path": "/auth/login", "auth_required": False,
         "request_fields": ["email", "password"]},
    ])


class _CookieSession:
    def __init__(self, set_cookie):
        self._sc = set_cookie

    def post(self, url, json=None, timeout=None):
        headers = {"Set-Cookie": self._sc} if "login" in url else {}
        return MagicMock(status_code=200, headers=headers, text="{}")


def test_cookie_flags_flags_missing_httponly():
    sess = _CookieSession("session=abc123; Path=/; SameSite=Lax")
    out = cookie_flags_probe(_model_cookie_auth(), "https://x", session=sess)
    assert any(f.probe == "cookie-flags" and "HttpOnly" in f.title and f.severity == "P2" for f in out)


def test_cookie_flags_quiet_when_hardened():
    sess = _CookieSession("session=abc123; Path=/; HttpOnly; Secure; SameSite=Lax")
    assert cookie_flags_probe(_model_cookie_auth(), "https://x", session=sess) == []


def test_cookie_flags_noop_on_bearer_api():
    # No Set-Cookie anywhere (token APIs) → nothing to judge.
    sess = _CookieSession("")
    assert cookie_flags_probe(_model_cookie_auth(), "https://x", session=sess) == []


# ── debug-disclosure probe ───────────────────────────────────────────────────

class _DebugSession:
    def __init__(self, body):
        self._body = body

    def get(self, url, headers=None, timeout=None):
        return MagicMock(status_code=500, text=self._body)


def test_debug_disclosure_flags_interactive_debugger_p1():
    body = "<title>OperationalError // Werkzeug Debugger</title><script src='?__debugger__=yes'>"
    out = debug_disclosure_probe(_model_lookup(), "http://x", session=_DebugSession(body))
    assert len(out) == 1 and out[0].severity == "P1" and out[0].probe == "debug-disclosure"


def test_debug_disclosure_flags_stack_trace_p2():
    body = 'Traceback (most recent call last):\n  File "/app/x.py", line 5, in get'
    out = debug_disclosure_probe(_model_lookup(), "http://x", session=_DebugSession(body))
    assert len(out) == 1 and out[0].severity == "P2"


def test_debug_disclosure_quiet_on_generic_error():
    out = debug_disclosure_probe(_model_lookup(), "http://x",
                                 session=_DebugSession('{"error":"Internal Server Error"}'))
    assert out == []


# ── supplied-auth (apps QAX can't self-register into) ────────────────────────

def test_auth_headers_default_and_custom():
    assert _auth_headers("abc") == {"Authorization": "Bearer abc"}
    assert _auth_headers("k", {"header": "X-API-Key", "scheme": ""}) == {"X-API-Key": "k"}
    assert _auth_headers("", {}) == {}


def test_resolve_token_prefers_supplied():
    auth = {"token": "TA", "token_b": "TB"}
    # no network/session needed when a supplied token exists
    assert _resolve_token(None, "", {}, None, "a@x.com", auth=auth, slot="primary") == "TA"
    assert _resolve_token(None, "", {}, None, "b@x.com", auth=auth, slot="secondary") == "TB"


def test_sqli_uses_supplied_token_without_registering():
    # An auth-required lookup; QAX has no register/login but is handed a token.
    model = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/api/users/{id}", "auth_required": True}])
    seen = {}

    class Sess:
        def get(self, url, headers=None, timeout=None):
            seen["auth"] = (headers or {}).get("Authorization")
            if url.endswith("'"):
                return MagicMock(status_code=500, text="sqlite3.OperationalError: syntax")
            return MagicMock(status_code=404, text="nf")

        def post(self, *a, **k):  # must NOT be needed — no self-registration
            raise AssertionError("should not register when a token is supplied")

    out = sqli_probe(model, "http://x", session=Sess(), auth={"token": "TOK"})
    assert seen["auth"] == "Bearer TOK"
    assert len(out) == 1 and out[0].probe == "sqli"


# ── ProbeService ─────────────────────────────────────────────────────────────

def test_service_requires_model_and_url():
    assert not ProbeService(_ctx()).run(AppModel(error="bad"), "http://x").success
    assert not ProbeService(_ctx()).run(_model_with_secret(), "").success
