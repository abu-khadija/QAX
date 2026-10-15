"""
test_probes.py — active exploit probes + ProbeService
=====================================================
The JWT signer is checked against a real verifier when available; the network
probes run against a fake in-process app so no live server is needed.

Run with:
    pytest tests/test_probes.py -v
"""

from unittest.mock import MagicMock

import pytest

import json as _json

from execution.probes import _auth_token as _auth_token_fn  # noqa: F401
from execution.probes import (_auth_headers, _resolve_token, auth_error_probe, bola_probe,
                               command_injection_probe,
                               cookie_flags_probe, cors_probe, crash_fuzz_probe,
                               debug_disclosure_probe, error_handling_probe, excessive_data_probe,
                               forge_jwt_probe, injection_probe, jwt_flaws_probe,
                               malformed_input_probe, mass_assignment_probe, nosql_probe,
                               open_redirect_probe, path_traversal_probe, persistence_probe,
                               rate_limit_probe, search_robustness_probe, security_headers_probe,
                               sign_jwt_hs, sqli_probe, unpaginated_list_probe)
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


class _BlindAuthSqlSession:
    """Auth-bypass blind SQLi: a TRUE tautology flips the status to 200; benign and a
    matched FALSE tautology both return 403 (same body length, so only status differs)."""
    def get(self, url, headers=None, timeout=None):
        if "'1'='1" in url:                                   # TRUE tautology
            return MagicMock(status_code=200, text='{"result":"ok____"}')
        return MagicMock(status_code=403, text='{"result":"no_____"}')  # benign & FALSE

    def post(self, *a, **k):
        return MagicMock(status_code=200, text="{}")


def test_sqli_blind_status_differential():
    out = sqli_probe(_model_lookup(), "http://x", session=_BlindAuthSqlSession())
    assert len(out) == 1 and out[0].severity == "P1" and out[0].probe == "sqli"
    assert "injection" in out[0].title.lower()


def test_sqli_blind_quiet_when_status_stable():
    # every response is 403 regardless of the boolean → no differential → no finding
    class _Stable:
        def get(self, url, headers=None, timeout=None):
            return MagicMock(status_code=403, text='{"result":"no_____"}')
        def post(self, *a, **k):
            return MagicMock(status_code=200, text="{}")
    assert sqli_probe(_model_lookup(), "http://x", session=_Stable()) == []


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


def test_error_handling_finding_identity_is_stable_across_runs():
    """The random probe path must not leak into the target — it feeds the Jira fingerprint / dedupe."""
    class Sess:
        def get(self, url, headers=None, timeout=None):
            return MagicMock(status_code=500, text="boom")
    a = error_handling_probe(AppModel(root="/x"), "http://x", session=Sess())[0]
    b = error_handling_probe(AppModel(root="/x"), "http://x", session=Sess())[0]
    assert a.target == b.target and "qax_nonexistent" not in a.target
    assert "qax_nonexistent" in a.evidence


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


# ── injection probe (query params + request bodies) ──────────────────────────

def test_injection_probe_flags_query_param_sqli():
    model = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/select", "auth_required": False, "query_params": ["username"]}])

    class Sess:
        def get(self, url, params=None, headers=None, timeout=None):
            if str((params or {}).get("username", "")).endswith("'"):
                return MagicMock(status_code=500, text="sqlite3.OperationalError: unrecognized token")
            return MagicMock(status_code=200, text="[]")

    out = injection_probe(model, "http://x", session=Sess())
    assert any(f.probe == "sqli" and "query param 'username'" in f.title and f.severity == "P1"
               for f in out)


def test_injection_probe_flags_body_field_sqli():
    model = AppModel(root="/x", endpoints=[
        {"method": "PUT", "path": "/user", "auth_required": False,
         "request_fields": ["name", "email", "address"]}])

    class Sess:
        def request(self, method, url, json=None, headers=None, timeout=None):
            # a quote in any non-email string field breaks the query
            if any(isinstance(v, str) and v.endswith("'") for v in (json or {}).values()):
                return MagicMock(status_code=500, text="sqlalchemy.exc / near syntax error")
            return MagicMock(status_code=200, text="{}")

    out = injection_probe(model, "http://x", session=Sess())
    assert any(f.probe == "sqli" and "body field" in f.title and f.severity == "P1" for f in out)


def test_injection_probe_quiet_when_parameterized():
    model = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/select", "auth_required": False, "query_params": ["username"]},
        {"method": "POST", "path": "/user", "auth_required": False, "request_fields": ["name"]}])

    class Sess:
        def get(self, url, params=None, headers=None, timeout=None):
            return MagicMock(status_code=200, text="[]")

        def request(self, method, url, json=None, headers=None, timeout=None):
            return MagicMock(status_code=200, text="{}")

    assert injection_probe(model, "http://x", session=Sess()) == []


# ── nosql probe (operator injection) ─────────────────────────────────────────

def _model_find() -> AppModel:
    # Raw-body POST keyed like the GET filter's query param (the vfapi shape).
    return AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/find", "auth_required": False, "query_params": ["username"]},
        {"method": "POST", "path": "/find", "auth_required": False},
    ])


def _is_op(v):
    return isinstance(v, dict) and any(k.startswith("$") for k in v)


def test_nosql_flags_bypass_p1():
    class Sess:
        def request(self, method, url, json=None, headers=None, timeout=None):
            v = (json or {}).get("username")
            if isinstance(v, dict) and v.get("$ne"):          # TRUE operator → all rows
                return MagicMock(status_code=200, text="X" * 5000)
            return MagicMock(status_code=200, text="[]")       # benign / FALSE → empty
    out = nosql_probe(_model_find(), "http://x", session=Sess())
    assert any(f.probe == "nosql" and f.severity == "P1" and "/find" in f.title for f in out)


def test_nosql_flags_error_signature_p1():
    class Sess:
        def request(self, method, url, json=None, headers=None, timeout=None):
            if _is_op((json or {}).get("username")):
                return MagicMock(status_code=500, text="pymongo.errors.OperationFailure")
            return MagicMock(status_code=200, text="[]")
    out = nosql_probe(_model_find(), "http://x", session=Sess())
    assert any(f.probe == "nosql" and f.severity == "P1" for f in out)


def test_nosql_generic_500_is_p3_lead():
    class Sess:
        def request(self, method, url, json=None, headers=None, timeout=None):
            if _is_op((json or {}).get("username")):
                return MagicMock(status_code=500, text="Internal Server Error")
            return MagicMock(status_code=200, text="[]")
    out = nosql_probe(_model_find(), "http://x", session=Sess())
    assert any(f.probe == "nosql-lead" and f.severity == "P3" for f in out)
    assert not any(f.severity == "P1" for f in out)


def test_nosql_quiet_when_operator_handled():
    class Sess:
        def request(self, method, url, json=None, headers=None, timeout=None):
            return MagicMock(status_code=200, text="[]")  # operator safely returns nothing
    assert nosql_probe(_model_find(), "http://x", session=Sess()) == []


# ── malformed-input probe (unhandled 500 / debug on type-violating input) ────

def _model_create():
    return AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/assets", "auth_required": False,
         "request_fields": ["name", "service_time"]}])


_GARBAGE = "qax-not-a-valid-value-!@#"


class _MalformedSession:
    def __init__(self, mode):
        self.mode = mode  # 'debugger' | 'plain500' | 'secure'

    def request(self, method, url, json=None, headers=None, timeout=None):
        bad = any(v == _GARBAGE for v in (json or {}).values())
        if not bad:
            return MagicMock(status_code=201, text="{}")        # valid baseline accepted
        if self.mode == "debugger":
            return MagicMock(status_code=500, text="<title>x // Werkzeug Debugger</title>")
        if self.mode == "plain500":
            return MagicMock(status_code=500, text="Internal Server Error")
        return MagicMock(status_code=422, text="validation error")  # secure: rejects malformed


def test_malformed_input_debugger_is_p1():
    out = malformed_input_probe(_model_create(), "http://x", session=_MalformedSession("debugger"))
    assert len(out) == 1 and out[0].probe == "malformed-input" and out[0].severity == "P1"
    assert "debugger" in out[0].title.lower()


def test_malformed_input_plain_500_is_p2():
    out = malformed_input_probe(_model_create(), "http://x", session=_MalformedSession("plain500"))
    assert len(out) == 1 and out[0].severity == "P2"


def test_malformed_input_quiet_when_validated():
    assert malformed_input_probe(_model_create(), "http://x", session=_MalformedSession("secure")) == []


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


def test_auth_token_falls_back_to_oauth2_form_login():
    recipe = {"register_path": "/auth/register", "login_path": "/users/login",
              "identity_path": "/me", "id_field": "email", "secret_field": "password",
              "token_field": "access_token"}
    model = AppModel(endpoints=[
        {"method": "POST", "path": "/users/login", "request_fields": ["username", "password"]}])

    class Sess:
        def post(self, url, json=None, data=None, timeout=None):
            if "register" in url:
                return MagicMock(status_code=200, json=lambda: {"status": "ok"})
            if "login" in url:
                if data and data.get("username"):          # OAuth2 form login works
                    return MagicMock(status_code=200,
                                     json=lambda: {"access_token": "TOK", "token_type": "bearer"})
                return MagicMock(status_code=400, json=lambda: {"detail": "unsupported"})  # JSON rejected
            return MagicMock(status_code=404, json=lambda: {})

    assert _auth_token_fn(Sess(), "http://x", recipe, model, "a@b.com") == "TOK"


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


# ── jwt-flaws probe (alg=none / signature not verified) ──────────────────────

def _jwt_model():
    return AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/me", "auth_required": True}])


_VALID_JWT = sign_jwt_hs("the-secret", "HS256", {"sub": "a@b.com", "exp": 9999999999})


class _JwtSession:
    """A protected endpoint. `mode` decides which forged tokens it (wrongly) accepts."""
    def __init__(self, mode):
        self.mode = mode  # 'secure' | 'algnone' | 'nosig'

    def get(self, url, headers=None, timeout=None):
        tok = (headers or {}).get("Authorization", "").replace("Bearer ", "")
        if not tok:
            return MagicMock(status_code=401, text="no")
        if tok == _VALID_JWT:
            return MagicMock(status_code=200, text="ok")
        parts = tok.split(".")
        alg = ""
        try:
            import base64 as _b, json as _j
            alg = _j.loads(_b.urlsafe_b64decode(parts[0] + "===")).get("alg", "")
        except Exception:
            pass
        if self.mode == "algnone" and alg == "none":
            return MagicMock(status_code=200, text="ok")      # wrongly accepts alg=none
        if self.mode == "nosig":
            return MagicMock(status_code=200, text="ok")       # never checks the signature
        return MagicMock(status_code=401, text="bad")          # secure: rejects forged tokens

    def post(self, *a, **k):
        return MagicMock(status_code=200, text="{}")


def test_jwt_flaws_detects_alg_none():
    out = jwt_flaws_probe(_jwt_model(), "http://x", session=_JwtSession("algnone"),
                          auth={"token": _VALID_JWT})
    assert any(f.probe == "jwt-flaws" and "alg=none" in f.title and f.severity == "P1" for f in out)


def test_jwt_flaws_detects_unverified_signature():
    out = jwt_flaws_probe(_jwt_model(), "http://x", session=_JwtSession("nosig"),
                          auth={"token": _VALID_JWT})
    assert any(f.probe == "jwt-flaws" and "signature not verified" in f.title for f in out)


def test_jwt_flaws_quiet_on_secure_server():
    out = jwt_flaws_probe(_jwt_model(), "http://x", session=_JwtSession("secure"),
                          auth={"token": _VALID_JWT})
    assert out == []


# ── ProbeService ─────────────────────────────────────────────────────────────

def test_service_requires_model_and_url():
    assert not ProbeService(_ctx()).run(AppModel(error="bad"), "http://x").success
    assert not ProbeService(_ctx()).run(_model_with_secret(), "").success


# ── path traversal ───────────────────────────────────────────────────────────

class _TraversalSession:
    """A lookup that serves /etc/passwd when fed a traversal payload (vulnerable)."""
    def __init__(self, vulnerable=True):
        self.vulnerable = vulnerable

    def get(self, url, headers=None, timeout=None):
        if self.vulnerable and ("etc/passwd" in url or "etc%2fpasswd" in url.lower()):
            return MagicMock(status_code=200, text="root:x:0:0:root:/root:/bin/bash\n")
        return MagicMock(status_code=404, text="not found")

    def post(self, *a, **k):
        return MagicMock(status_code=200, text="{}")


def test_path_traversal_confirms_on_passwd_signature():
    out = path_traversal_probe(_model_lookup(), "http://x", session=_TraversalSession(vulnerable=True))
    assert len(out) == 1 and out[0].exploited and out[0].severity == "P1"
    assert out[0].probe == "path-traversal"


def test_path_traversal_quiet_when_not_vulnerable():
    assert path_traversal_probe(_model_lookup(), "http://x", session=_TraversalSession(vulnerable=False)) == []


class _QueryTraversalSession:
    """A file-read endpoint that serves /etc/passwd when a file-ish QUERY param asks."""
    def get(self, url, params=None, headers=None, timeout=None):
        val = str((params or {}).get("filename", ""))
        if "etc/passwd" in val or val == "/etc/passwd":
            return MagicMock(status_code=200, text="root:*:0:0:System Administrator:/var/root:/bin/sh\n")
        return MagicMock(status_code=200, text="{}")


def test_path_traversal_via_query_param():
    model = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/api/vul/read", "query_params": ["filename"], "auth_required": False}])
    out = path_traversal_probe(model, "http://x", session=_QueryTraversalSession())
    assert len(out) == 1 and out[0].severity == "P1" and "query param" in out[0].title


class _CmdInjectionSession:
    """A ping-style endpoint that reflects shell output — so `;echo TOKEN` comes back."""
    def get(self, url, params=None, headers=None, timeout=None):
        ip = str((params or {}).get("ip", ""))
        out = f"PING {ip}"
        # emulate the shell running the injected `echo TOKEN` after a separator
        for sep in (";", "&&", "|"):
            if f"{sep}echo " in ip:
                out += "\n" + ip.split("echo ", 1)[1]
        return MagicMock(status_code=200, text='{"success":"' + out + '"}')


def test_command_injection_output_based():
    model = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/api/vul/cmd1", "query_params": ["ip"], "auth_required": False}])
    out = command_injection_probe(model, "http://x", session=_CmdInjectionSession())
    assert len(out) == 1 and out[0].severity == "P1" and out[0].probe == "command-injection"


def test_command_injection_quiet_without_cmd_params():
    model = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/articles", "query_params": ["title"], "auth_required": False}])
    assert command_injection_probe(model, "http://x", session=_CmdInjectionSession()) == []


class _XssSession:
    """Reflects the 'name' param into HTML. `encode=True` HTML-encodes it (safe)."""
    def __init__(self, encode=False, html=True):
        self.encode = encode
        self.html = html

    def get(self, url, params=None, headers=None, timeout=None):
        v = str((params or {}).get("name", ""))
        if self.encode:
            v = v.replace("<", "&lt;").replace(">", "&gt;")
        ct = "text/html; charset=utf-8" if self.html else "application/json"
        return MagicMock(status_code=200, text=f"<h1>Hello {v}</h1>", headers={"Content-Type": ct})


def test_xss_reflected_unencoded_html():
    from execution.probes import xss_probe
    model = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/api/vul/xss", "query_params": ["name"], "auth_required": False}])
    out = xss_probe(model, "http://x", session=_XssSession(encode=False, html=True))
    assert len(out) == 1 and out[0].probe == "xss" and out[0].severity == "P2"


def test_xss_quiet_when_encoded_or_not_html():
    from execution.probes import xss_probe
    model = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/api/vul/xss", "query_params": ["name"], "auth_required": False}])
    assert xss_probe(model, "http://x", session=_XssSession(encode=True, html=True)) == []   # encoded
    assert xss_probe(model, "http://x", session=_XssSession(encode=False, html=False)) == []  # JSON


class _StoredXssSession:
    """Stores 'content' raw and renders it in HTML on read; 'username' is escaped (safe)."""
    def __init__(self, escape=False):
        self.escape = escape
        self.store = []

    def request(self, method, url, data=None, json=None, headers=None, timeout=None):
        payload = data or json or {}
        c = str(payload.get("content", ""))
        if c:
            self.store.append(c.replace("<", "&lt;").replace(">", "&gt;") if self.escape else c)
        return MagicMock(status_code=200, text="")

    def get(self, url, headers=None, timeout=None):
        html = "<table>" + "".join(f"<td>{c}</td>" for c in self.store) + "</table>"
        return MagicMock(status_code=200, text=html, headers={"Content-Type": "text/html"})


def test_stored_xss_write_then_read():
    from execution.probes import stored_xss_probe
    model = AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/api/vul/addcomments", "request_fields": ["username", "content"], "auth_required": False},
        {"method": "GET", "path": "/api/vul/getcomments", "auth_required": False}])
    out = stored_xss_probe(model, "http://x", session=_StoredXssSession(escape=False))
    assert len(out) == 1 and out[0].probe == "stored-xss" and out[0].severity == "P1"
    assert "content" in out[0].title


def test_stored_xss_quiet_when_encoded():
    from execution.probes import stored_xss_probe
    model = AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/api/vul/addcomments", "request_fields": ["content"], "auth_required": False},
        {"method": "GET", "path": "/api/vul/getcomments", "auth_required": False}])
    assert stored_xss_probe(model, "http://x", session=_StoredXssSession(escape=True)) == []


class _SsrfSession:
    """A server that FETCHES the URL in the 'q' param (vulnerable) — hits QAX's listener."""
    def __init__(self, vulnerable=True):
        self.vulnerable = vulnerable

    def get(self, url, params=None, headers=None, timeout=None):
        target = str((params or {}).get("q", ""))
        if self.vulnerable and target.startswith("http"):
            try:
                import urllib.request
                urllib.request.urlopen(target, timeout=2).read()
            except Exception:
                pass
        return MagicMock(status_code=200, text="{}")


def test_ssrf_confirmed_by_oob_callback():
    from execution.probes import ssrf_probe
    model = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/api/vul/ssrf", "query_params": ["q"], "auth_required": False}])
    out = ssrf_probe(model, "http://x", session=_SsrfSession(vulnerable=True))
    assert len(out) == 1 and out[0].probe == "ssrf" and out[0].severity == "P2"


def test_ssrf_quiet_when_not_fetching():
    from execution.probes import ssrf_probe
    model = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/api/vul/ssrf", "query_params": ["q"], "auth_required": False}])
    assert ssrf_probe(model, "http://x", session=_SsrfSession(vulnerable=False)) == []


class _EndpointCorsSession:
    """Reflects an arbitrary Origin ONLY on a specific non-root endpoint."""
    def __init__(self, reflecting_path):
        self.reflecting_path = reflecting_path

    def get(self, url, headers=None, timeout=None):
        origin = (headers or {}).get("Origin", "")
        path = "/" + url.split("://", 1)[-1].split("/", 1)[-1] if "://" in url else url
        h = {"Access-Control-Allow-Origin": origin} if (origin and path == self.reflecting_path) else {}
        return MagicMock(status_code=200, text="{}", headers=h)


def test_cors_detected_on_non_root_endpoint():
    model = AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/api/vul/cors2", "auth_required": False}])
    out = cors_probe(model, "http://x", session=_EndpointCorsSession("/api/vul/cors2"))
    assert len(out) == 1 and out[0].probe == "cors" and out[0].target == "/api/vul/cors2"


# ── CORS ─────────────────────────────────────────────────────────────────────

class _CorsSession:
    def __init__(self, reflect=True, creds=False):
        self.reflect = reflect
        self.creds = creds

    def get(self, url, headers=None, timeout=None):
        origin = (headers or {}).get("Origin", "")
        h = {}
        if self.reflect and origin:
            h["Access-Control-Allow-Origin"] = origin
            if self.creds:
                h["Access-Control-Allow-Credentials"] = "true"
        return MagicMock(status_code=200, text="{}", headers=h)


def test_cors_reflection_with_credentials_is_p2():
    out = cors_probe(AppModel(root="/x"), "http://x", session=_CorsSession(reflect=True, creds=True))
    assert len(out) == 1 and out[0].severity == "P2" and out[0].probe == "cors"


def test_cors_reflection_without_credentials_is_p3():
    out = cors_probe(AppModel(root="/x"), "http://x", session=_CorsSession(reflect=True, creds=False))
    assert len(out) == 1 and out[0].severity == "P3"


def test_cors_quiet_when_not_reflected():
    assert cors_probe(AppModel(root="/x"), "http://x", session=_CorsSession(reflect=False)) == []


# ── security headers ─────────────────────────────────────────────────────────

class _HeadersSession:
    def __init__(self, headers):
        self._h = headers

    def get(self, url, headers=None, timeout=None):
        return MagicMock(status_code=200, text="ok", headers=dict(self._h))


def test_security_headers_flags_missing_set():
    out = security_headers_probe(AppModel(root="/x"), "http://x", session=_HeadersSession({}))
    assert out and any(f.probe == "security-headers" for f in out)
    titles = " ".join(f.title for f in out)
    assert "X-Content-Type-Options" in titles


def test_security_headers_flags_hsts_on_https():
    out = security_headers_probe(AppModel(root="/x"), "https://x", session=_HeadersSession({}))
    assert any(f.severity == "P2" and "HSTS" in f.title for f in out)


def test_security_headers_quiet_when_all_present():
    full = {
        "Content-Security-Policy": "default-src 'self'",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Strict-Transport-Security": "max-age=63072000",
    }
    assert security_headers_probe(AppModel(root="/x"), "https://x", session=_HeadersSession(full)) == []


# ── mass assignment ──────────────────────────────────────────────────────────

class _MassAssignSession:
    def __init__(self, reflect=True):
        self.reflect = reflect

    def post(self, url, json=None, timeout=None):
        if "register" in url:
            body = {"username": (json or {}).get("username", "u")}
            if self.reflect:
                body["admin"] = True            # echoes the smuggled privileged field
            return MagicMock(status_code=201, json=lambda b=body: b, text="{}")
        return MagicMock(status_code=200, json=lambda: {}, text="{}")


def test_mass_assignment_confirms_on_echoed_privilege():
    out = mass_assignment_probe(_model_with_secret(), "http://x", session=_MassAssignSession(reflect=True))
    assert len(out) == 1 and out[0].severity == "P2" and out[0].probe == "mass-assignment"
    assert "admin" in out[0].title.lower()


def test_mass_assignment_quiet_when_field_stripped():
    assert mass_assignment_probe(_model_with_secret(), "http://x", session=_MassAssignSession(reflect=False)) == []


# ── open redirect ────────────────────────────────────────────────────────────

class _RedirectSession:
    def __init__(self, vulnerable=True):
        self.vulnerable = vulnerable

    def get(self, url, headers=None, allow_redirects=None, timeout=None):
        if self.vulnerable and "qax-redirect.example" in url:
            return MagicMock(status_code=302, text="",
                             headers={"Location": "https://qax-redirect.example/evil"})
        return MagicMock(status_code=200, text="ok", headers={})


def test_open_redirect_confirms_on_external_location():
    out = open_redirect_probe(AppModel(root="/x"), "http://x", session=_RedirectSession(vulnerable=True))
    assert len(out) == 1 and out[0].severity == "P2" and out[0].probe == "open-redirect"


def test_open_redirect_quiet_when_not_redirecting():
    assert open_redirect_probe(AppModel(root="/x"), "http://x", session=_RedirectSession(vulnerable=False)) == []


# ── rate limiting ────────────────────────────────────────────────────────────

class _RateLimitSession:
    def __init__(self, throttle_after=None):
        self.throttle_after = throttle_after
        self.n = 0

    def post(self, url, json=None, timeout=None):
        if "register" in url:
            return MagicMock(status_code=201, json=lambda: {}, text="{}")
        self.n += 1
        if self.throttle_after and self.n > self.throttle_after:
            return MagicMock(status_code=429, text="too many requests")
        return MagicMock(status_code=401, text="bad credentials")


def test_rate_limit_flags_when_never_throttled():
    out = rate_limit_probe(_model_with_secret(), "http://x", session=_RateLimitSession(throttle_after=None))
    assert len(out) == 1 and out[0].severity == "P3" and out[0].probe == "rate-limit"


def test_rate_limit_quiet_when_throttled():
    assert rate_limit_probe(_model_with_secret(), "http://x", session=_RateLimitSession(throttle_after=3)) == []


# ── test-data cleanup ────────────────────────────────────────────────────────

from execution.probes import _collection_of, _record_created, cleanup_artifacts  # noqa: E402


def test_collection_of_strips_action_segment():
    assert _collection_of("/users/v1/register") == "/users/v1"
    assert _collection_of("/auth/signup") == "/auth"
    assert _collection_of("/books/v1") == "/books/v1"


def test_cleanup_deletes_only_recorded_records():
    """cleanup_artifacts DELETEs the keys QAX recorded, matched to a DELETE endpoint."""
    model = AppModel(root="/x", endpoints=[
        {"method": "DELETE", "path": "/users/v1/{username}", "auth_required": True},
    ])
    deleted = []

    class _Sess:
        def delete(self, url, headers=None, timeout=None):
            deleted.append(url)
            return MagicMock(status_code=204, text="")

    sess = _Sess()
    sess._qax_created = []
    _record_created(sess, "/users/v1", {"username": "qax_u1", "email": "a@b.c"}, {"Authorization": "Bearer t"})
    _record_created(sess, "/books/v1", {"book_title": "qax_b1"}, {})   # no DELETE endpoint → skipped
    cleaned = cleanup_artifacts(model, "http://x", sess)
    assert cleaned == 1
    assert deleted == ["http://x/users/v1/qax_u1"]


def test_cleanup_noop_without_registry():
    assert cleanup_artifacts(AppModel(root="/x"), "http://x", MagicMock(spec=[])) == 0


# ── persistence probe (silent data loss on save) ─────────────────────────────

def _model_persist() -> AppModel:
    return AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/notes", "auth_required": False,
         "request_fields": ["title", "body", "category"]},
        {"method": "GET", "path": "/notes/{title}", "auth_required": False},
    ])


class _PersistSession:
    """
    Create + read-back. `drop` names a field the server silently discards on save
    (returning success anyway); `echo_none` makes GET echo no fields at all.
    """
    def __init__(self, drop=None, echo_none=False):
        self.drop = drop
        self.echo_none = echo_none
        self.store = {}

    def post(self, url, json=None, headers=None, timeout=None):
        rec = dict(json or {})
        if self.drop:
            rec.pop(self.drop, None)
        self.store[rec.get("title")] = rec
        return MagicMock(status_code=201, json=lambda: {"success": True}, text='{"success": true}')

    def get(self, url, headers=None, timeout=None):
        title = url.rsplit("/", 1)[-1]
        rec = self.store.get(title)
        if rec is None:
            return MagicMock(status_code=404, text="not found")
        body = {} if self.echo_none else rec
        return MagicMock(status_code=200, text=_json.dumps(body))


@pytest.fixture(autouse=True)
def _no_readback_wait(monkeypatch):
    """The read-back retry sleeps between reads; tests don't need the wall-clock delay."""
    import execution.probes as P
    monkeypatch.setattr(P, "_READBACK_WAIT", 0)


class _LaggySession(_PersistSession):
    """An async write: the dropped field only becomes visible on the SECOND read (replica lag)."""
    def __init__(self, visible_after=2):
        super().__init__(drop=None)
        self.reads, self.visible_after, self.full = 0, visible_after, {}

    def post(self, url, json=None, headers=None, timeout=None):
        r = super().post(url, json=json, headers=headers, timeout=timeout)
        rec = dict(json or {})
        self.full[rec.get("title")] = rec
        # At first only the key + `category` are visible (a positive control); `body` lags.
        self.store[rec.get("title")] = {k: v for k, v in rec.items() if k in ("title", "category")}
        return r

    def get(self, url, headers=None, timeout=None):
        self.reads += 1
        title = url.rsplit("/", 1)[-1]
        if self.reads >= self.visible_after and title in self.full:
            self.store[title] = self.full[title]
        return super().get(url, headers=headers, timeout=timeout)


def test_persistence_does_not_cry_wolf_when_the_write_just_lags():
    # First read shows only the key (looks like a drop); the next read has everything.
    s = _LaggySession(visible_after=2)
    assert persistence_probe(_model_persist(), "http://x", session=s) == []
    assert s.reads >= 2                      # it DID re-read before deciding


def test_persistence_still_flags_a_drop_that_survives_every_re_read():
    s = _PersistSession(drop="body")
    out = persistence_probe(_model_persist(), "http://x", session=s)
    assert len(out) == 1 and "body" in out[0].evidence


def test_persistence_flags_silent_drop():
    out = persistence_probe(_model_persist(), "http://x", session=_PersistSession(drop="body"))
    assert len(out) == 1 and out[0].probe == "persistence" and out[0].severity == "P2"
    assert "body" in out[0].evidence


def test_persistence_quiet_when_all_fields_persist():
    assert persistence_probe(_model_persist(), "http://x", session=_PersistSession(drop=None)) == []


def test_persistence_quiet_when_read_echoes_nothing():
    # All markers absent = inconclusive (GET may not echo fields) — never cry wolf.
    assert persistence_probe(_model_persist(), "http://x",
                             session=_PersistSession(drop=None, echo_none=True)) == []


# ── auth-error probe (500 where 403 belongs) ─────────────────────────────────

def _model_authed() -> AppModel:
    return AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/me", "auth_required": True},
        {"method": "POST", "path": "/items", "auth_required": True, "request_fields": ["name"]},
    ])


class _AuthErrSession:
    def __init__(self, crash=True):
        self.crash = crash

    def _deny(self):
        return MagicMock(status_code=500 if self.crash else 403, text="x")

    def get(self, url, headers=None, timeout=None):
        return self._deny()

    def request(self, method, url, json=None, headers=None, timeout=None):
        return self._deny()


def test_auth_error_flags_5xx_on_forbidden():
    out = auth_error_probe(_model_authed(), "http://x", session=_AuthErrSession(crash=True))
    assert out and all(f.severity == "P2" and f.probe == "auth-error" for f in out)


def test_auth_error_quiet_on_clean_403():
    assert auth_error_probe(_model_authed(), "http://x", session=_AuthErrSession(crash=False)) == []


# ── search-robustness probe (405/5xx on an ordinary character) ───────────────

def _model_search() -> AppModel:
    return AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/search", "auth_required": False, "query_params": ["q"]},
    ])


class _SearchSession:
    def __init__(self, reject_specials=True):
        self.reject_specials = reject_specials

    def get(self, url, params=None, headers=None, timeout=None):
        q = (params or {}).get("q", "")
        if self.reject_specials and any(c in q for c in '@"()/|~^-'):
            return MagicMock(status_code=405, text="method not allowed")
        return MagicMock(status_code=200, text="[]")


def test_search_robustness_flags_405_on_special_char():
    out = search_robustness_probe(_model_search(), "http://x", session=_SearchSession(reject_specials=True))
    assert len(out) == 1 and out[0].probe == "search-robustness" and out[0].severity == "P3"


def test_search_robustness_quiet_when_tolerant():
    assert search_robustness_probe(_model_search(), "http://x",
                                   session=_SearchSession(reject_specials=False)) == []


# ── unpaginated-list probe ───────────────────────────────────────────────────

def _model_collection() -> AppModel:
    return AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/items", "auth_required": False},
    ])


class _ListSession:
    def __init__(self, total=150, honour_limit=False):
        self.total = total
        self.honour_limit = honour_limit

    def get(self, url, params=None, headers=None, timeout=None):
        params = params or {}
        limit = next((int(v) for k, v in params.items() if "limit" in k.lower()
                      or "size" in k.lower() or k.lower() in ("count", "top", "per_page")), None)
        n = self.total
        if limit is not None and self.honour_limit:
            n = min(limit, self.total)
        return MagicMock(status_code=200, json=lambda n=n: [{"i": i} for i in range(n)],
                         text="[]")


def test_unpaginated_list_flags_unbounded():
    out = unpaginated_list_probe(_model_collection(), "http://x",
                                 session=_ListSession(total=150, honour_limit=False))
    assert len(out) == 1 and out[0].probe == "unpaginated-list" and out[0].severity == "P3"


def test_unpaginated_list_quiet_when_paginated():
    assert unpaginated_list_probe(_model_collection(), "http://x",
                                  session=_ListSession(total=150, honour_limit=True)) == []


def test_unpaginated_list_quiet_when_small():
    assert unpaginated_list_probe(_model_collection(), "http://x",
                                  session=_ListSession(total=10, honour_limit=False)) == []


# ── item 6: forged-token replay across the protected surface ───────────────────

def _replay_model():
    return AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/me", "auth_required": True},            # control
        {"method": "GET", "path": "/admin/users", "auth_required": True},   # sibling
        {"method": "GET", "path": "/billing/invoices", "auth_required": True},  # sibling
        {"method": "GET", "path": "/health", "auth_required": False},       # public
    ])


class _ReplaySession:
    """Protected endpoints 401 without a token; `mode` decides forged-token handling."""
    def __init__(self, mode):
        self.mode = mode  # 'nosig' (accepts any sig) | 'secure' (rejects forged)

    def get(self, url, headers=None, timeout=None):
        tok = (headers or {}).get("Authorization", "").replace("Bearer ", "")
        if "/health" in url:
            return MagicMock(status_code=200, text="ok")      # public
        if not tok:
            return MagicMock(status_code=401, text="no")      # protected
        if tok == _VALID_JWT:
            return MagicMock(status_code=200, text="ok")
        return MagicMock(status_code=200 if self.mode == "nosig" else 401, text="x")

    def post(self, *a, **k):
        return MagicMock(status_code=200, text="{}")


def test_forged_token_replay_sweeps_siblings_but_not_control():
    from execution.probes import forged_token_replay_probe
    out = forged_token_replay_probe(_replay_model(), "http://x",
                                    session=_ReplaySession("nosig"), auth={"token": _VALID_JWT})
    paths = {f.target for f in out}
    assert paths == {"/admin/users", "/billing/invoices"}   # siblings only; /me is the control
    assert all(f.probe == "forged-token-replay" and f.exploited and f.severity == "P1" for f in out)


def test_forged_token_replay_quiet_on_secure_server():
    from execution.probes import forged_token_replay_probe
    assert forged_token_replay_probe(_replay_model(), "http://x",
                                     session=_ReplaySession("secure"), auth={"token": _VALID_JWT}) == []


def test_forged_token_replay_quiet_without_siblings():
    from execution.probes import forged_token_replay_probe
    out = forged_token_replay_probe(_jwt_model(), "http://x",      # only /me exists → no siblings
                                    session=_ReplaySession("nosig"), auth={"token": _VALID_JWT})
    assert out == []


# ── BFLA (OWASP API5): ordinary user reaching an admin-namespaced function ──────

def _bfla_model():
    return AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/admin/users", "auth_required": True},
        {"method": "GET", "path": "/admin/public", "auth_required": True},  # actually public
        {"method": "GET", "path": "/me", "auth_required": True}])


class _BflaSession:
    def __init__(self, mode):  # 'vuln' = no role check | 'secure' = role enforced
        self.mode = mode

    def get(self, url, headers=None, timeout=None):
        tok = (headers or {}).get("Authorization", "")
        if "/admin/users" in url:
            if not tok:
                return MagicMock(status_code=401, text="no")
            return MagicMock(status_code=200 if self.mode == "vuln" else 403, text="x")
        if "/admin/public" in url:
            return MagicMock(status_code=200, text="public")   # not access-controlled
        return MagicMock(status_code=200, text="ok")

    def post(self, *a, **k):
        return MagicMock(status_code=200, text="{}")


def test_bfla_detects_ordinary_user_reaching_admin():
    from execution.probes import bfla_probe
    # The ORDINARY user is the secondary (token_b), never the primary token.
    out = bfla_probe(_bfla_model(), "http://x", session=_BflaSession("vuln"), auth={"token_b": _VALID_JWT})
    assert len(out) == 1 and out[0].probe == "bfla" and out[0].severity == "P1"
    assert out[0].target == "/admin/users"


def test_bfla_quiet_when_role_enforced():
    from execution.probes import bfla_probe
    assert bfla_probe(_bfla_model(), "http://x", session=_BflaSession("secure"), auth={"token_b": _VALID_JWT}) == []


def test_bfla_ignores_public_admin_path():
    from execution.probes import bfla_probe
    out = bfla_probe(_bfla_model(), "http://x", session=_BflaSession("vuln"), auth={"token_b": _VALID_JWT})
    assert all(f.target != "/admin/public" for f in out)   # no-token 200 → not access-controlled


def test_bfla_never_uses_privileged_primary_token():
    """
    Regression (Gitea, 2026-10-15): the primary --token is routinely a privileged
    admin/service token. BFLA must NOT treat it as an ordinary user — doing so made
    every admin endpoint return 2xx and fabricated 12 P1s on a perfectly secure app.
    With only a primary token (and no self-registration available), BFLA stays silent.
    """
    from execution.probes import bfla_probe
    out = bfla_probe(_bfla_model(), "http://x", session=_BflaSession("vuln"), auth={"token": _VALID_JWT})
    assert out == []


# ── SSTI: template expression evaluated server-side (differential control) ──────

def _ssti_model():
    return AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/greet", "query_params": ["name"]}])


class _SstiSession:
    def __init__(self, mode):  # 'vuln' evaluates the expression | 'reflect' echoes literally
        self.mode = mode

    def get(self, url, params=None, headers=None, timeout=None):
        import re as _re
        val = (params or {}).get("name", "")
        if self.mode == "vuln":
            val = _re.sub(r"[{$#]\{(\d+)\*(\d+)\}\}?",
                          lambda m: str(int(m.group(1)) * int(m.group(2))), val)
        return MagicMock(status_code=200, text=f"<html>Hello {val}</html>",
                         headers={"Content-Type": "text/html"})

    def post(self, *a, **k):
        return MagicMock(status_code=200, text="{}")


def test_ssti_detects_server_side_evaluation():
    from execution.probes import ssti_probe
    out = ssti_probe(_ssti_model(), "http://x", session=_SstiSession("vuln"))
    assert len(out) == 1 and out[0].probe == "ssti" and out[0].severity == "P1"
    assert out[0].target == "/greet"


def test_ssti_quiet_when_input_only_reflected():
    from execution.probes import ssti_probe
    assert ssti_probe(_ssti_model(), "http://x", session=_SstiSession("reflect")) == []


# ── host-header injection: forged Host used to build a URL ──────────────────────

def _hhi_model():
    return AppModel(root="/x", endpoints=[{"method": "GET", "path": "/reset-link"}])


class _HostSession:
    def __init__(self, mode):  # 'vuln' trusts the Host header | 'secure' fixed host
        self.mode = mode

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        host = "app.example.com" if self.mode == "secure" else (headers or {}).get("Host", "app.example.com")
        return MagicMock(status_code=200, text=f"<html>Reset at https://{host}/reset</html>", headers={})

    def post(self, *a, **k):
        return MagicMock(status_code=200, text="{}")


def test_host_header_injection_detected_in_url_context():
    from execution.probes import host_header_injection_probe
    out = host_header_injection_probe(_hhi_model(), "http://x", session=_HostSession("vuln"))
    assert len(out) == 1 and out[0].probe == "host-header-injection" and out[0].severity == "P2"
    assert out[0].target == "/reset-link"


def test_host_header_injection_quiet_with_fixed_host():
    from execution.probes import host_header_injection_probe
    assert host_header_injection_probe(_hhi_model(), "http://x", session=_HostSession("secure")) == []


# ── XXE: external-entity file read, proven by the /etc/passwd signature ─────────

def _xxe_model():
    return AppModel(root="/x", endpoints=[{"method": "POST", "path": "/import"}])


class _XxeSession:
    def __init__(self, mode):  # 'vuln' resolves the entity | 'secure' does not
        self.mode = mode

    def request(self, method, url, data=None, headers=None, timeout=None):
        if self.mode == "vuln" and 'SYSTEM "file:///etc/passwd"' in (data or ""):
            return MagicMock(status_code=200, text="<result>root:x:0:0:root:/root:/bin/bash</result>")
        return MagicMock(status_code=200, text="<result>ok</result>")

    def post(self, *a, **k):
        return MagicMock(status_code=200, text="{}")


def test_xxe_detected_by_passwd_signature():
    from execution.probes import xxe_probe
    out = xxe_probe(_xxe_model(), "http://x", session=_XxeSession("vuln"))
    assert len(out) == 1 and out[0].probe == "xxe" and out[0].severity == "P1"


def test_xxe_quiet_when_entities_not_resolved():
    from execution.probes import xxe_probe
    assert xxe_probe(_xxe_model(), "http://x", session=_XxeSession("secure")) == []


# ── GraphQL introspection left enabled ─────────────────────────────────────────

def _gql_model():
    return AppModel(root="/x", endpoints=[{"method": "POST", "path": "/graphql"}])


class _GqlSession:
    def __init__(self, mode):  # 'vuln' returns a schema | 'secure' errors
        self.mode = mode

    def post(self, url, json=None, headers=None, timeout=None):
        q = str((json or {}).get("query", ""))
        if "__schema" in q and self.mode == "vuln":
            return MagicMock(status_code=200,
                             json=lambda: {"data": {"__schema": {"queryType": {"name": "Query"}}}})
        return MagicMock(status_code=200, json=lambda: {"errors": [{"message": "disabled"}]})


def test_graphql_introspection_detected_when_schema_returned():
    from execution.probes import graphql_introspection_probe
    out = graphql_introspection_probe(_gql_model(), "http://x", session=_GqlSession("vuln"))
    assert len(out) == 1 and out[0].probe == "graphql-introspection" and out[0].severity == "P2"
    assert out[0].target == "/graphql"


def test_graphql_introspection_quiet_when_disabled():
    from execution.probes import graphql_introspection_probe
    assert graphql_introspection_probe(_gql_model(), "http://x", session=_GqlSession("secure")) == []


# ── unrestricted file upload (upload HTML → retrieve → served as HTML) ──────────

def _upload_model():
    return AppModel(root="/x", endpoints=[{"method": "POST", "path": "/upload"}])


class _UploadSession:
    def __init__(self, mode):  # 'vuln' stores+serves html | 'secure' rejects non-images
        self.mode = mode
        self.store = {}

    def request(self, method, url, files=None, headers=None, timeout=None):
        field, (fname, content, ct) = next(iter((files or {}).items()))
        if self.mode == "secure" and not fname.lower().endswith((".png", ".jpg")):
            return MagicMock(status_code=400, text='{"error":"only images"}')
        self.store[fname] = content
        return MagicMock(status_code=201, text='{"url":"/uploads/' + fname + '"}')

    def get(self, url, headers=None, timeout=None):
        name = url.rsplit("/", 1)[-1]
        data = self.store.get(name)
        if data is None:
            return MagicMock(status_code=404, text="nf", headers={})
        ct = "application/octet-stream" if self.mode == "secure" else "text/html"
        return MagicMock(status_code=200, text=data, headers={"Content-Type": ct})

    def post(self, *a, **k):
        return MagicMock(status_code=201, text="{}")


def test_file_upload_detected_when_html_served_back():
    from execution.probes import file_upload_probe
    out = file_upload_probe(_upload_model(), "http://x", session=_UploadSession("vuln"))
    assert len(out) == 1 and out[0].probe == "file-upload" and out[0].severity == "P1"
    assert out[0].target == "/upload"


def test_file_upload_quiet_when_extension_rejected():
    from execution.probes import file_upload_probe
    assert file_upload_probe(_upload_model(), "http://x", session=_UploadSession("secure")) == []


def test_file_upload_is_labelled_mutating():
    from execution.probes import MUTATING_PROBES
    assert "file-upload" in MUTATING_PROBES


# ── business logic: negative quantity accepted + stored ─────────────────────────

def _bl_model():
    return AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/orders", "request_fields": ["item", "quantity"]}])


class _BlSession:
    def __init__(self, mode):  # 'vuln' accepts any qty | 'secure' rejects qty < 1
        self.mode = mode

    def post(self, url, json=None, headers=None, timeout=None):
        qty = (json or {}).get("quantity")
        if self.mode == "secure" and isinstance(qty, int) and qty < 1:
            return MagicMock(status_code=400, text='{"error":"quantity must be positive"}')
        return MagicMock(status_code=201, text='{"id":1,"quantity":%s}' % qty)

    def get(self, *a, **k):
        return MagicMock(status_code=200, text="{}")


def test_business_logic_detects_negative_quantity_stored():
    from execution.probes import business_logic_probe
    out = business_logic_probe(_bl_model(), "http://x", session=_BlSession("vuln"))
    assert len(out) == 1 and out[0].probe == "business-logic" and out[0].severity == "P2"
    assert out[0].target == "/orders"


def test_business_logic_quiet_when_negative_rejected():
    from execution.probes import business_logic_probe
    assert business_logic_probe(_bl_model(), "http://x", session=_BlSession("secure")) == []


# ── Access revocation (business logic): stale access after an owner revokes ─────

def _revoke_model():
    from services.results import AppModel
    return AppModel(root="/x", endpoints=[
        {"method": "GET", "path": "/repos/{owner}/{repo}", "auth_required": True},
        {"method": "PUT", "path": "/repos/{owner}/{repo}/collaborators/{collaborator}", "auth_required": True},
        {"method": "DELETE", "path": "/repos/{owner}/{repo}/collaborators/{collaborator}", "auth_required": True},
    ])


class _RevokeSession:
    """
    Two-user collaboration API. mode 'secure' blocks B after revoke; mode 'stale'
    keeps B's access. Reads the Bearer token to tell user A ('A') from user B ('B').
    """
    def __init__(self, mode="secure"):
        self.mode, self.granted, self.calls = mode, False, []

    def _who(self, headers):
        return (headers or {}).get("Authorization", "").replace("Bearer ", "")

    def get(self, url, headers=None, timeout=None):
        who = self._who(headers)
        if url.endswith("/user"):                                   # whoami
            return MagicMock(status_code=200, json=lambda: {"login": "bob" if who == "B" else "alice"})
        if url.endswith("/user/repos"):                             # A's owned resource
            return MagicMock(status_code=200, json=lambda: [{"owner": {"login": "alice"}, "name": "secret"}])
        if url.endswith("/repos/alice/secret"):                     # B reads the resource
            if who != "B":
                return MagicMock(status_code=200, json=lambda: {})
            ok = self.granted or self.mode == "stale"
            return MagicMock(status_code=200 if ok else 404, json=lambda: {})
        return MagicMock(status_code=404, json=lambda: {})

    def request(self, method, url, headers=None, timeout=None, **kw):
        self.calls.append((method, url))
        if method == "GET":
            return self.get(url, headers=headers)
        if url.endswith("/collaborators/bob"):
            if method == "PUT":
                self.granted = True
                return MagicMock(status_code=204, json=lambda: {})
            if method == "DELETE":
                self.granted = False                                 # row removed...
                return MagicMock(status_code=204, json=lambda: {})
        return MagicMock(status_code=404, json=lambda: {})


def test_access_revocation_fires_on_stale_access():
    from execution.probes import access_revocation_probe
    out = access_revocation_probe(_revoke_model(), "http://x",
                                  session=_RevokeSession("stale"), auth={"token": "A", "token_b": "B"})
    assert len(out) == 1 and out[0].probe == "access-revocation" and out[0].severity == "P1"
    assert out[0].target == "/repos/{owner}/{repo}"


def test_access_revocation_silent_when_revoke_works():
    from execution.probes import access_revocation_probe
    assert access_revocation_probe(_revoke_model(), "http://x",
                                   session=_RevokeSession("secure"), auth={"token": "A", "token_b": "B"}) == []


def test_access_revocation_needs_two_tokens():
    from execution.probes import access_revocation_probe
    assert access_revocation_probe(_revoke_model(), "http://x",
                                   session=_RevokeSession("stale"), auth={"token": "A"}) == []


def test_access_revocation_silent_without_grant_revoke_pair():
    from execution.probes import access_revocation_probe
    from services.results import AppModel
    # Only a GET membership listing — no PUT/DELETE grant pair → nothing to test.
    m = AppModel(root="/x", endpoints=[{"method": "GET", "path": "/repos/{owner}/{repo}/collaborators"}])
    assert access_revocation_probe(m, "http://x", session=_RevokeSession("stale"),
                                   auth={"token": "A", "token_b": "B"}) == []


# ── Not-tested ledger: a probe that can't run must SAY so (not return [] silently) ─

class _LedgerSession(_RevokeSession):
    """A fake app session that also carries the not-tested ledger ProbeService opens."""
    def __init__(self, mode="secure"):
        super().__init__(mode)
        self._qax_skips = []


def test_skip_helper_is_noop_without_a_ledger():
    from execution.probes import _skip
    _skip(object(), "x", "y")                     # no attribute → silently nothing
    _skip(MagicMock(), "x", "y")                  # a non-list attribute → ignored, never raises


def test_skip_helper_dedupes():
    from execution.probes import _skip
    s = _LedgerSession()
    _skip(s, "bfla", "r"); _skip(s, "bfla", "r")
    assert s._qax_skips == [{"probe": "bfla", "reason": "r", "kind": "missing-prerequisite"}]


def test_access_revocation_reports_missing_second_identity():
    from execution.probes import access_revocation_probe
    s = _LedgerSession("stale")
    assert access_revocation_probe(_revoke_model(), "http://x", session=s, auth={"token": "A"}) == []
    assert [(k["probe"], k["kind"]) for k in s._qax_skips] == [("access-revocation", "missing-prerequisite")]
    assert "--token-b" in s._qax_skips[0]["reason"]


def test_access_revocation_na_is_not_a_gap_when_app_has_no_grant_pair():
    from execution.probes import access_revocation_probe
    from services.results import AppModel
    s = _LedgerSession("stale")
    m = AppModel(root="/x", endpoints=[{"method": "GET", "path": "/items"}])
    assert access_revocation_probe(m, "http://x", session=s, auth={"token": "A"}) == []
    assert s._qax_skips[0]["kind"] == "not-applicable"       # informational, not a coverage gap


def test_access_revocation_reports_missing_whoami():
    from execution.probes import access_revocation_probe

    class _NoWhoami(_LedgerSession):
        def get(self, url, headers=None, timeout=None):
            if url.endswith("/user"):
                return MagicMock(status_code=404, json=lambda: {})
            return super().get(url, headers=headers, timeout=timeout)

    s = _NoWhoami("stale")
    assert access_revocation_probe(_revoke_model(), "http://x", session=s,
                                   auth={"token": "A", "token_b": "B"}) == []
    assert "whoami" in s._qax_skips[0]["reason"]


def test_bfla_reports_when_no_ordinary_identity():
    from execution.probes import bfla_probe

    class _NoReg(_LedgerSession):
        def post(self, *a, **k):
            return MagicMock(status_code=404, text="", json=lambda: {})

    s = _NoReg()
    out = bfla_probe(_bfla_model(), "http://x", session=s, auth={"token": _VALID_JWT})  # primary only
    assert out == []
    assert any(k["probe"] == "bfla" and k["kind"] == "missing-prerequisite" for k in s._qax_skips)


def test_probe_result_and_orchestrator_result_carry_gaps():
    from services.results import OrchestratorResult, ProbeResult
    skipped = [{"probe": "bola", "reason": "needs two users", "kind": "missing-prerequisite"},
               {"probe": "xxe", "reason": "no xml endpoint", "kind": "not-applicable"}]
    assert ProbeResult(skipped=skipped).skipped == skipped
    r = OrchestratorResult(not_tested=skipped)
    assert [g["probe"] for g in r.coverage_gaps] == ["bola"]          # n/a is NOT a gap
    assert r.to_dict()["not_tested"] == skipped


# ── Data-state ledger: what QAX created and could NOT remove ────────────────────

def test_cleanup_reports_exactly_what_it_left_and_why():
    from execution.probes import _record_created, cleanup_artifacts, leftover_artifacts
    model = AppModel(root="/x", endpoints=[
        {"method": "DELETE", "path": "/users/v1/{username}", "auth_required": True},
        {"method": "DELETE", "path": "/orgs/{name}", "auth_required": True}])

    class _Sess:
        def delete(self, url, headers=None, timeout=None):
            return MagicMock(status_code=403 if "/orgs/" in url else 204, text="")

    s = _Sess(); s._qax_created = []
    _record_created(s, "/users/v1", {"username": "qax_ok", "password": "Secret123!"}, {"Authorization": "Bearer SECRET"})
    _record_created(s, "/orgs", {"name": "qax_org"}, {})                      # delete refused (403)
    _record_created(s, "/books/v1", {"title": "qax_book"}, {})                # no DELETE endpoint
    assert cleanup_artifacts(model, "http://x", s) == 1
    left = leftover_artifacts(s)
    assert {(l["collection"], l["key"]) for l in left} == {("/orgs", "qax_org"), ("/books/v1", "qax_book")}
    why = {l["key"]: l["why"] for l in left}
    assert "refused (HTTP 403)" in why["qax_org"] and "no DELETE endpoint" in why["qax_book"]
    blob = str(left)
    assert "Secret123!" not in blob and "SECRET" not in blob and "Bearer" not in blob   # no secrets, no headers


def test_leftover_lists_everything_when_the_api_has_no_delete_at_all():
    from execution.probes import _record_created, cleanup_artifacts, leftover_artifacts
    s = MagicMock(spec=["_qax_created", "delete"]); s._qax_created = []
    _record_created(s, "/things", {"name": "qax_t1"}, {})
    assert cleanup_artifacts(AppModel(root="/x", endpoints=[]), "http://x", s) == 0
    assert [l["why"] for l in leftover_artifacts(s)] == ["the API has no DELETE endpoint for it"]


def test_leftover_falls_back_to_the_registry_when_cleanup_never_ran():
    from execution.probes import _record_created, leftover_artifacts
    s = MagicMock(spec=["_qax_created"]); s._qax_created = []
    _record_created(s, "/things", {"name": "qax_t1"}, {})
    out = leftover_artifacts(s, "cleanup was disabled")
    assert out == [{"collection": "/things", "key": "qax_t1", "why": "cleanup was disabled"}]
    assert leftover_artifacts(object()) == []                                  # never raises


def test_result_types_carry_the_leftover_ledger():
    from services.results import OrchestratorResult, ProbeResult
    left = [{"collection": "/x", "key": "k", "why": "w"}]
    assert ProbeResult(leftover=left).leftover == left
    assert OrchestratorResult(left_on_target=left).to_dict()["left_on_target"] == left


# ── A probe that CRASHES is a coverage gap (not a clean result) ─────────────────

def test_a_crashing_probe_lands_in_the_not_tested_ledger(monkeypatch):
    import execution.probes as P
    from services.context import QAXContext
    from services.probe_service import ProbeService

    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(P, "bfla_probe", boom)
    # Nothing listens on port 1: every other probe fails fast and quietly.
    res = ProbeService(QAXContext(anthropic_key="k", project="")).run(
        AppModel(root="/x", endpoints=[{"method": "GET", "path": "/admin/users"}]), "http://127.0.0.1:1")
    crashed = [s for s in res.skipped if s["probe"] == "bfla"]
    assert crashed and crashed[0]["kind"] == "error" and "kaboom" in crashed[0]["reason"]


def test_an_erroring_probe_counts_as_a_coverage_gap():
    from services.results import OrchestratorResult
    r = OrchestratorResult(not_tested=[{"probe": "x", "reason": "the probe crashed: e", "kind": "error"},
                                       {"probe": "y", "reason": "n/a", "kind": "not-applicable"}])
    assert [g["probe"] for g in r.coverage_gaps] == ["x"]
