"""
test_probe_honesty.py — every probe must SAY when it could not test (ROADMAP #24)
================================================================================
`verify-fixes` calls a bug FIXED when its probe "ran and reported no gap". That is only as
honest as the probe, so each older probe now records into the not-tested ledger when it
(a) has nothing to test (not-applicable), (b) lacked a token it needed (missing-prerequisite),
or (c) could not reach the app at all. The fourth case guards the other direction: a normal
server that simply answers must NOT produce a gap (no invented gaps).
"""

from unittest.mock import MagicMock

import pytest

import execution.probes as P
from services.results import AppModel


@pytest.fixture(autouse=True)
def _fast_ssrf_wait(monkeypatch):
    """SSRF waits for an out-of-band callback after every injected URL; tests don't need 3 s each."""
    monkeypatch.setattr(P, "_SSRF_WAIT", 0.05)


class _Led:
    """A session that records the not-tested ledger; subclasses decide how the 'server' behaves."""
    def __init__(self):
        self._qax_skips = []

    def _answer(self, *a, **k):
        r = MagicMock(status_code=404, text="not found", headers={}, content=b"")
        r.json.side_effect = ValueError("not json")
        return r


class _Quiet(_Led):
    """A server that answers everything with a plain 404 (reachable, nothing to find)."""
    def get(self, *a, **k): return self._answer()
    def post(self, *a, **k): return self._answer()
    def put(self, *a, **k): return self._answer()
    def delete(self, *a, **k): return self._answer()
    def request(self, *a, **k): return self._answer()


class _Accepting(_Led):
    """A well-behaved server: every request is accepted (200, empty JSON)."""
    def _ok(self, *a, **k):
        r = MagicMock(status_code=200, text="{}", headers={"Content-Type": "application/json"}, content=b"{}")
        r.json.return_value = {}
        return r
    get = post = put = delete = request = _ok


class _Hardened(_Quiet):
    """A reachable app that sets the standard security headers (so security-headers has nothing to say)."""
    def get(self, *a, **k):
        r = self._answer()
        r.status_code = 200
        r.headers = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                     "Content-Security-Policy": "default-src 'self'"}
        return r


class _Dead(_Led):
    """Nothing is listening: every request fails at the network level."""
    def _boom(self, *a, **k): raise ConnectionError("refused")
    get = post = put = delete = request = _boom


# malformed-input / search-robustness need their valid BASELINE accepted before they can say anything
# (a server that rejects everything is a genuine "could not exercise" gap), and security-headers
# reports missing headers — so their "normal server" cases use a server that behaves normally.
_OK = {"malformed-input": _Accepting, "search-robustness": _Accepting, "security-headers": _Hardened}


def _m(*eps):
    return AppModel(root="/x", endpoints=list(eps))


def _model_for(probe, ep):
    """stored-XSS also needs a GET endpoint to read the stored value back from."""
    if probe == "stored-xss":
        return _m(ep, {"method": "GET", "path": "/comments"})
    return _m(ep)


def _gaps(sess, probe):
    return [s for s in sess._qax_skips if s["probe"] == probe]


# probe-id -> (function, an endpoint that gives it work, has_na, needs_token)
#   has_na:       the probe says "not applicable" when the app has nothing for it (cors / open-redirect
#                 always try "/" so they never do)
#   needs_token:  the probe sends authenticated requests, so a missing token is a gap
_CASES = {
    "host-header-injection": (P.host_header_injection_probe, {"method": "GET", "path": "/home"}, True, True),
    "xxe": (P.xxe_probe, {"method": "POST", "path": "/import"}, True, True),
    "file-upload": (P.file_upload_probe, {"method": "POST", "path": "/upload", "request_fields": ["file"]}, True, True),
    "ssti": (P.ssti_probe, {"method": "GET", "path": "/greet", "query_params": ["name"]}, True, True),
    "xss": (P.xss_probe, {"method": "GET", "path": "/greet", "query_params": ["name"]}, True, True),
    "cors": (P.cors_probe, {"method": "GET", "path": "/home"}, False, False),
    "open-redirect": (P.open_redirect_probe, {"method": "GET", "path": "/home"}, False, False),
    "injection": (P.injection_probe, {"method": "GET", "path": "/search", "query_params": ["q"]}, True, True),
    "sqli": (P.sqli_probe, {"method": "GET", "path": "/users/{id}"}, True, True),
    "nosql": (P.nosql_probe, {"method": "POST", "path": "/find", "request_fields": ["name"]}, True, True),
    "command-injection": (P.command_injection_probe, {"method": "GET", "path": "/diag", "query_params": ["host"]}, True, True),
    "ssrf": (P.ssrf_probe, {"method": "GET", "path": "/preview", "query_params": ["url"]}, True, True),
    "path-traversal": (P.path_traversal_probe, {"method": "GET", "path": "/files/{name}"}, True, True),
    "stored-xss": (P.stored_xss_probe, {"method": "POST", "path": "/comments", "request_fields": ["content"]}, True, True),
    "mass-assignment": (P.mass_assignment_probe, {"method": "GET", "path": "/x"}, True, False),
    "rate-limit": (P.rate_limit_probe, {"method": "GET", "path": "/x"}, True, False),
    "malformed-input": (P.malformed_input_probe, {"method": "POST", "path": "/items", "request_fields": ["name"]}, True, True),
    "excessive-data": (P.excessive_data_probe, {"method": "GET", "path": "/items"}, True, True),
    "cookie-flags": (P.cookie_flags_probe, {"method": "GET", "path": "/x"}, True, False),
    "debug-disclosure": (P.debug_disclosure_probe, {"method": "GET", "path": "/items/{id}"}, True, True),
    "error-handling": (P.error_handling_probe, {"method": "GET", "path": "/x"}, False, False),
    "crash-fuzz": (P.crash_fuzz_probe, {"method": "GET", "path": "/x"}, True, False),
    "security-headers": (P.security_headers_probe, {"method": "GET", "path": "/x"}, False, False),
    "auth-error": (P.auth_error_probe, {"method": "GET", "path": "/me", "auth_required": True}, True, False),
    "search-robustness": (P.search_robustness_probe, {"method": "GET", "path": "/search", "query_params": ["q"]}, True, True),
    "unpaginated-list": (P.unpaginated_list_probe, {"method": "GET", "path": "/items"}, True, True),
}
# SSRF's callback listener lives on the scanner's loopback: only a LOCAL target can reach it, so its
# "normal server" case must use a local URL (the remote case has its own test below).
_BASE = {"ssrf": "http://127.0.0.1:1"}


def _base(probe):
    return _BASE.get(probe, "http://x")
_IDS = list(_CASES)
_NA = [k for k, v in _CASES.items() if v[2]]
_TOK = [k for k, v in _CASES.items() if v[3]]


@pytest.mark.parametrize("probe", _NA)
def test_nothing_to_test_is_reported_as_not_applicable_not_a_gap(probe):
    fn = _CASES[probe][0]
    s = _Quiet()
    assert fn(_m({"method": "DELETE", "path": "/x"}), _base(probe), session=s) == []
    g = _gaps(s, probe)
    assert len(g) == 1 and g[0]["kind"] == "not-applicable"


@pytest.mark.parametrize("probe", _TOK)
def test_an_endpoint_needing_auth_without_a_token_is_a_missing_prerequisite(probe):
    fn, ep = _CASES[probe][:2]
    s = _Quiet()
    fn(_model_for(probe, {**ep, "auth_required": True}), _base(probe), session=s)
    g = [x for x in _gaps(s, probe) if x["kind"] == "missing-prerequisite" and x.get("needs") == "auth-token"]
    assert g, _gaps(s, probe)


@pytest.mark.parametrize("probe", _IDS)
def test_a_dead_server_is_reported_as_unreached_never_as_clean(probe):
    fn, ep = _CASES[probe][:2]
    s = _Dead()
    assert fn(_model_for(probe, ep), _base(probe), session=s) == []
    g = [x for x in _gaps(s, probe) if "could be reached" in x["reason"] or "could not exercise" in x["reason"]]
    assert g and g[0]["kind"] == "missing-prerequisite", _gaps(s, probe)


@pytest.mark.parametrize("probe", _IDS)
def test_a_normal_server_that_simply_answers_produces_no_gap(probe):
    fn, ep = _CASES[probe][:2]
    s = _OK.get(probe, _Quiet)()
    assert fn(_model_for(probe, ep), _base(probe), session=s) == []
    # reachable + nothing wrong ≠ a coverage GAP (a "not applicable" note is allowed, e.g. no HTML sink)
    assert [g for g in _gaps(s, probe) if g["kind"] != "not-applicable"] == []


def test_nosql_a_crashing_baseline_means_it_could_not_be_exercised():
    class _Crashy(_Quiet):
        def request(self, *a, **k):
            return MagicMock(status_code=500, text="boom", headers={}, json=MagicMock(side_effect=ValueError()))
    s = _Crashy()
    assert P.nosql_probe(_m(_CASES["nosql"][1]), "http://x", session=s) == []
    g = _gaps(s, "nosql")
    assert len(g) == 1 and "benign baseline request failed" in g[0]["reason"] and g[0]["needs"] == "valid-body"


def test_a_probe_that_finds_something_does_not_also_claim_a_gap():
    # CORS reflecting the Origin is a finding; reachability is obviously fine → no gap entry.
    class _Reflects(_Quiet):
        def get(self, url, headers=None, **k):
            r = MagicMock(status_code=200, text="", headers={"Access-Control-Allow-Origin": (headers or {}).get("Origin", "")})
            return r
    s = _Reflects()
    out = P.cors_probe(_m(), "http://x", session=s)
    assert out and out[0].probe == "cors" and _gaps(s, "cors") == []


def test_graphql_a_dead_server_is_unreached_but_a_plain_404_is_not_a_gap():
    s = _Dead()
    assert P.graphql_introspection_probe(_m(), "http://x", session=s) == []
    assert _gaps(s, "graphql-introspection") and "could be reached" in _gaps(s, "graphql-introspection")[0]["reason"]
    q = _Quiet()
    assert P.graphql_introspection_probe(_m(), "http://x", session=q) == []
    assert _gaps(q, "graphql-introspection") == []     # a non-JSON 404 means "no GraphQL here", i.e. clean


def test_file_upload_accepted_but_unverifiable_is_a_gap():
    class _Accepts(_Quiet):
        def request(self, *a, **k):
            r = MagicMock(status_code=201, text='{"ok": true}', headers={})
            r.json.return_value = {"ok": True}
            return r
    s = _Accepts()
    assert P.file_upload_probe(_m(_CASES["file-upload"][1]), "http://x", session=s) == []
    g = _gaps(s, "file-upload")
    assert len(g) == 1 and "gave no URL to fetch the file back" in g[0]["reason"]


def test_no_ledger_means_no_crash_and_no_output():
    # Probes run in isolation (no ProbeService ledger on the session) must keep working.
    class _Bare:
        def get(self, *a, **k): raise ConnectionError("x")
        post = request = get
    for fn, ep, *_ in _CASES.values():
        assert fn(_m(ep), "http://x", session=_Bare()) == []


# ── batch 3 specifics ────────────────────────────────────────────────────────────

def test_ssrf_remote_target_cannot_reach_the_local_callback_so_it_says_so():
    s = _Quiet()
    assert P.ssrf_probe(_m(_CASES["ssrf"][1]), "https://preview.example.com", session=s) == []
    g = _gaps(s, "ssrf")
    assert len(g) == 1 and "cannot reach QAX's local callback listener" in g[0]["reason"]
    assert g[0]["kind"] == "missing-prerequisite"
    # …but a LOCAL target can, so a quiet answer there is a genuine clean result.
    q = _Quiet()
    assert P.ssrf_probe(_m(_CASES["ssrf"][1]), "http://localhost:9", session=q) == [] and _gaps(q, "ssrf") == []


def test_is_local_target():
    for u in ("http://127.0.0.1:8000", "http://localhost", "http://[::1]:80", "127.0.0.1:9"):
        assert P._is_local_target(u), u
    for u in ("https://app.example.com", "http://10.0.0.5:8080", "http://192.168.1.2"):
        assert not P._is_local_target(u), u


def test_command_injection_and_ssrf_now_send_the_token_to_authenticated_endpoints():
    seen = []

    class _Rec(_Quiet):
        def get(self, url, **k):
            seen.append(k.get("headers") or {})
            return super().get(url)
        def request(self, *a, **k):
            seen.append(k.get("headers") or {})
            return super().request()
    for probe in ("command-injection", "ssrf"):
        seen.clear()
        ep = {**_CASES[probe][1], "auth_required": True}
        P_fn = _CASES[probe][0]
        P_fn(_m(ep), _base(probe), session=_Rec(), auth={"token": "TKN"})
        assert seen and all(h.get("Authorization") == "Bearer TKN" for h in seen), (probe, seen[:2])


def test_stored_xss_json_only_api_is_not_applicable_not_a_gap():
    class _JsonApi(_Quiet):
        def get(self, *a, **k):
            r = MagicMock(status_code=200, text='{"items": []}', headers={"Content-Type": "application/json"})
            return r
        def request(self, *a, **k):
            return MagicMock(status_code=201, text="{}", headers={"Content-Type": "application/json"})
    s = _JsonApi()
    assert P.stored_xss_probe(_model_for("stored-xss", _CASES["stored-xss"][1]), "http://x", session=s) == []
    g = _gaps(s, "stored-xss")
    assert len(g) == 1 and g[0]["kind"] == "not-applicable" and "not an HTML sink" in g[0]["reason"]


def test_stored_xss_without_a_read_endpoint_is_not_applicable():
    s = _Quiet()
    assert P.stored_xss_probe(_m(_CASES["stored-xss"][1]), "http://x", session=s) == []
    assert _gaps(s, "stored-xss")[0]["kind"] == "not-applicable"


# ── batch 4 specifics ────────────────────────────────────────────────────────────

class _Responder(_Led):
    """Scripted server: `rules` maps (method, path-suffix) -> (status, text, headers)."""
    def __init__(self, rules):
        super().__init__()
        self.rules = rules

    def _go(self, method, url, **k):
        for (m, suffix), (code, text, headers) in self.rules.items():
            if m == method and url.endswith(suffix):
                r = MagicMock(status_code=code, text=text, headers=headers)
                try:
                    import json as _j
                    r.json.return_value = _j.loads(text)
                except Exception:
                    r.json.side_effect = ValueError("not json")
                return r
        return self._answer()

    def get(self, url, **k): return self._go("GET", url, **k)
    def post(self, url, **k): return self._go("POST", url, **k)
    def request(self, method, url, **k): return self._go(method.upper(), url, **k)


def test_registration_gaps_are_classified_by_status():
    # 404/405: the app simply has no self-registration endpoint (token-only API) → not applicable.
    for probe, fn in (("mass-assignment", P.mass_assignment_probe), ("rate-limit", P.rate_limit_probe)):
        s = _Responder({("POST", "/auth/register"): (405, "", {})})
        assert fn(_m(), "http://x", session=s) == []
        g = _gaps(s, probe)
        assert len(g) == 1 and g[0]["kind"] == "not-applicable" and "no self-registration endpoint" in g[0]["reason"]
        # 422: the endpoint exists but rejected our body → a real gap QAX cannot fix alone.
        s = _Responder({("POST", "/auth/register"): (422, "{}", {})})
        assert fn(_m(), "http://x", session=s) == []
        g = _gaps(s, probe)
        assert len(g) == 1 and g[0]["kind"] == "missing-prerequisite" and g[0]["needs"] == "valid-body"
        assert "rejected (HTTP 422)" in g[0]["reason"]


def test_mass_assignment_non_json_registration_response_is_a_gap():
    s = _Responder({("POST", "/auth/register"): (201, "created", {})})
    assert P.mass_assignment_probe(_m(), "http://x", session=s) == []
    assert "not JSON" in _gaps(s, "mass-assignment")[0]["reason"]


def test_rate_limit_oddly_behaving_login_is_inconclusive_not_clean():
    s = _Responder({("POST", "/auth/register"): (201, "{}", {}), ("POST", "/auth/login"): (500, "boom", {})})
    assert P.rate_limit_probe(_m(), "http://x", session=s) == []
    g = _gaps(s, "rate-limit")
    assert len(g) == 1 and "did not behave like a normal auth failure" in g[0]["reason"]


def test_rate_limit_throttled_or_unthrottled_login_needs_no_gap():
    s = _Responder({("POST", "/auth/register"): (201, "{}", {}), ("POST", "/auth/login"): (429, "slow down", {})})
    assert P.rate_limit_probe(_m(), "http://x", session=s) == [] and _gaps(s, "rate-limit") == []
    s = _Responder({("POST", "/auth/register"): (201, "{}", {}), ("POST", "/auth/login"): (401, "no", {})})
    out = P.rate_limit_probe(_m(), "http://x", session=s)
    assert out and out[0].probe == "rate-limit" and _gaps(s, "rate-limit") == []


def test_malformed_input_rejected_baseline_is_could_not_exercise():
    s = _Quiet()                                    # every request 404 → the valid baseline never succeeds
    assert P.malformed_input_probe(_m(_CASES["malformed-input"][1]), "http://x", session=s) == []
    g = _gaps(s, "malformed-input")
    assert len(g) == 1 and "valid baseline request was rejected" in g[0]["reason"] and g[0]["needs"] == "valid-body"


def test_excessive_data_endpoint_needing_auth_without_a_token_is_reported_with_its_count():
    s = _Quiet()
    P.excessive_data_probe(_m({"method": "GET", "path": "/a", "auth_required": True},
                              {"method": "GET", "path": "/b", "auth_required": True}), "http://x", session=s)
    g = [x for x in _gaps(s, "excessive-data") if x.get("needs") == "auth-token"]
    assert len(g) == 1 and "2 endpoint(s) need auth" in g[0]["reason"]


def test_cookie_flags_bearer_api_with_no_cookies_is_not_applicable_but_bad_cookies_are_findings():
    s = _Responder({("POST", "/auth/login"): (200, "{}", {})})
    assert P.cookie_flags_probe(_m(), "http://x", session=s) == []
    g = _gaps(s, "cookie-flags")
    assert len(g) == 1 and g[0]["kind"] == "not-applicable" and "bearer-token APIs" in g[0]["reason"]
    s = _Responder({("POST", "/auth/login"): (200, "{}", {"Set-Cookie": "sid=abc; Path=/"})})
    out = P.cookie_flags_probe(_m(), "http://x", session=s)
    assert out and out[0].probe == "cookie-flags" and _gaps(s, "cookie-flags") == []


def test_debug_disclosure_now_sends_the_token_to_authenticated_lookups():
    seen = []

    class _Rec(_Quiet):
        def get(self, url, **k):
            seen.append((k.get("headers") or {}).get("Authorization"))
            return super().get(url)
    P.debug_disclosure_probe(_m({"method": "GET", "path": "/items/{id}", "auth_required": True}), "http://x",
                             session=_Rec(), auth={"token": "TKN"})
    assert seen and set(seen) == {"Bearer TKN"}


# ── batch 5 specifics ────────────────────────────────────────────────────────────

def test_unpaginated_list_with_little_data_is_not_applicable_not_clean():
    s = _Responder({("GET", "/items"): (200, '[{"id": 1}, {"id": 2}]', {})})
    assert P.unpaginated_list_probe(_m({"method": "GET", "path": "/items"}), "http://x", session=s) == []
    g = _gaps(s, "unpaginated-list")
    assert len(g) == 1 and g[0]["kind"] == "not-applicable" and "little data" in g[0]["reason"]


def test_unpaginated_list_a_big_unbounded_collection_is_a_finding_without_a_gap():
    import json as _j
    big = _j.dumps([{"id": i} for i in range(150)])
    s = _Responder({("GET", "/items"): (200, big, {})})
    out = P.unpaginated_list_probe(_m({"method": "GET", "path": "/items"}), "http://x", session=s)
    assert out and out[0].probe == "unpaginated-list" and _gaps(s, "unpaginated-list") == []


def test_search_robustness_rejected_baseline_is_could_not_exercise():
    s = _Quiet()                                  # the benign search request itself 404s
    assert P.search_robustness_probe(_m(_CASES["search-robustness"][1]), "http://x", session=s) == []
    g = _gaps(s, "search-robustness")
    assert len(g) == 1 and "benign search request was rejected" in g[0]["reason"] and g[0]["needs"] == "valid-body"


def test_crash_fuzz_no_registration_endpoint_is_not_applicable_and_a_5xx_is_a_finding():
    s = _Responder({("POST", "/auth/register"): (404, "", {})})
    assert P.crash_fuzz_probe(_m(), "http://x", session=s) == []
    assert _gaps(s, "crash-fuzz")[0]["kind"] == "not-applicable"
    s = _Responder({("POST", "/auth/register"): (500, "boom", {})})
    out = P.crash_fuzz_probe(_m(), "http://x", session=s)
    assert out and out[0].probe == "crash-fuzz" and _gaps(s, "crash-fuzz") == []


def test_auth_error_and_error_handling_findings_do_not_also_claim_a_gap():
    s = _Responder({("GET", "/me"): (500, "boom", {})})
    out = P.auth_error_probe(_m({"method": "GET", "path": "/me", "auth_required": True}), "http://x", session=s)
    assert out and out[0].probe == "auth-error" and _gaps(s, "auth-error") == []
    class _Crash(_Quiet):
        def get(self, *a, **k):
            return MagicMock(status_code=500, text="x", headers={})
    c = _Crash()
    out = P.error_handling_probe(_m(), "http://x", session=c)
    assert out and out[0].probe == "error-handling" and _gaps(c, "error-handling") == []


def test_security_headers_missing_headers_is_a_finding_not_a_gap():
    s = _Quiet()
    out = P.security_headers_probe(_m(), "http://x", session=s)
    assert out and out[0].probe == "security-headers" and _gaps(s, "security-headers") == []


# ── the last probes: persistence, jwt-flaws, bfla, forged-token-replay, bola, business-logic ─────

from tests.test_probes import _VALID_JWT, _PersistSession, _model_persist  # noqa: E402


def test_persistence_has_distinct_states_for_every_way_it_can_fail_to_judge():
    # no create/read pair at all → not applicable
    s = _Quiet()
    assert P.persistence_probe(_m({"method": "GET", "path": "/x"}), "http://x", session=s) == []
    assert _gaps(s, "persistence")[0]["kind"] == "not-applicable"
    # the create is rejected → could not exercise
    s = _Quiet()
    assert P.persistence_probe(_model_persist(), "http://x", session=s) == []
    g = _gaps(s, "persistence")
    assert len(g) == 1 and "create request was rejected or failed" in g[0]["reason"] and g[0]["needs"] == "valid-body"

    # created, but the read-by-key fails
    class _NoRead(_PersistSession):
        def get(self, url, headers=None, timeout=None):
            return MagicMock(status_code=404, text="no")
    s = _NoRead(); s._qax_skips = []
    assert P.persistence_probe(_model_persist(), "http://x", session=s) == []
    assert "could not be read back by key" in s._qax_skips[0]["reason"]

    # created and read, but the GET echoes none of our fields → cannot tell a drop from a field it never returns
    s = _PersistSession(drop=None, echo_none=True); s._qax_skips = []
    assert P.persistence_probe(_model_persist(), "http://x", session=s) == []
    assert "did not echo any submitted field" in s._qax_skips[0]["reason"]

    # everything persists → genuinely clean, NO gap entry
    s = _PersistSession(drop=None); s._qax_skips = []
    assert P.persistence_probe(_model_persist(), "http://x", session=s) == [] and s._qax_skips == []


class _JwtApp(_Led):
    """A tiny JWT-protected API. public: /me answers with no token; valid_ok: the real token is accepted."""
    def __init__(self, public=False, valid_ok=True, accepts_forged=False, valid_token=_VALID_JWT):
        super().__init__()
        self.public, self.valid_ok, self.accepts_forged, self.valid_token = public, valid_ok, accepts_forged, valid_token

    def get(self, url, headers=None, **k):
        tok = ((headers or {}).get("Authorization") or "").replace("Bearer ", "")
        if not tok:
            code = 200 if self.public else 401
        elif tok == self.valid_token:
            code = 200 if self.valid_ok else 401
        else:
            code = 200 if self.accepts_forged else 401
        return MagicMock(status_code=code, text="{}", headers={})


_ME = {"method": "GET", "path": "/me", "auth_required": True}
_AUTH = {"token": _VALID_JWT}


def test_jwt_flaws_states():
    # (The recipe always supplies a default identity endpoint, so "no target at all" is not reachable.)
    s = _JwtApp(valid_ok=False)
    assert P.jwt_flaws_probe(_m(_ME), "http://x", session=s, auth=_AUTH) == []
    g = _gaps(s, "jwt-flaws")
    assert len(g) == 1 and "valid token was not accepted" in g[0]["reason"] and g[0]["needs"] == "auth-token"
    s = _JwtApp(public=True)
    assert P.jwt_flaws_probe(_m(_ME), "http://x", session=s, auth=_AUTH) == []
    assert "not access-controlled" in _gaps(s, "jwt-flaws")[0]["reason"] and _gaps(s, "jwt-flaws")[0]["kind"] == "not-applicable"
    s = _JwtApp(valid_token="opaque-api-key")        # the app accepts it, but it is not a JWT
    assert P.jwt_flaws_probe(_m(_ME), "http://x", session=s, auth={"token": "opaque-api-key"}) == []
    assert "not a JWT" in _gaps(s, "jwt-flaws")[0]["reason"]
    # a server that verifies signatures: clean and NO gap
    s = _JwtApp()
    assert P.jwt_flaws_probe(_m(_ME), "http://x", session=s, auth=_AUTH) == [] and _gaps(s, "jwt-flaws") == []


def test_forged_token_replay_distinguishes_no_control_from_a_clean_server():
    s = _JwtApp(valid_ok=False)
    assert P.forged_token_replay_probe(_m(_ME), "http://x", session=s, auth=_AUTH) == []
    g = _gaps(s, "forged-token-replay")
    assert len(g) == 1 and "valid token was rejected on all 1 protected endpoint" in g[0]["reason"] and g[0]["needs"] == "auth-token"
    s = _JwtApp(public=True)
    assert P.forged_token_replay_probe(_m(_ME), "http://x", session=s, auth=_AUTH) == []
    assert _gaps(s, "forged-token-replay")[0]["kind"] == "not-applicable"
    s = _JwtApp()                                   # clean control + forged rejected → silent, no gap
    assert P.forged_token_replay_probe(_m(_ME), "http://x", session=s, auth=_AUTH) == [] and _gaps(s, "forged-token-replay") == []
    s = _JwtApp()
    assert P.forged_token_replay_probe(_m(), "http://x", session=s, auth=_AUTH) == []
    assert _gaps(s, "forged-token-replay")[0]["kind"] == "not-applicable"


def test_bfla_states():
    adm = {"method": "GET", "path": "/admin/users"}
    s = _Dead()
    assert P.bfla_probe(_m(adm), "http://x", session=s, auth={"token_b": "B"}) == []
    assert "could be reached" in _gaps(s, "bfla")[0]["reason"]
    s = _JwtApp(public=True)                         # admin path answers without a token: not access-controlled
    assert P.bfla_probe(_m(adm), "http://x", session=s, auth={"token_b": "B"}) == []
    g = _gaps(s, "bfla")
    assert len(g) == 1 and g[0]["kind"] == "not-applicable" and "none of the 1 admin-namespaced" in g[0]["reason"]
    s = _JwtApp()
    assert P.bfla_probe(_m({"method": "GET", "path": "/home"}), "http://x", session=s, auth={"token_b": "B"}) == []
    assert _gaps(s, "bfla")[0]["kind"] == "not-applicable"


_BOLA = [{"method": "POST", "path": "/notes", "request_fields": ["title", "body"], "auth_required": True},
         {"method": "GET", "path": "/notes/{title}", "auth_required": True}]


def test_bola_states():
    both = {"token": "A", "token_b": "B"}
    s = _Quiet()
    assert P.bola_probe(_m(), "http://x", session=s, auth=both) == []
    assert _gaps(s, "bola")[0]["kind"] == "not-applicable"
    s = _Quiet()                                     # A's create is rejected (404)
    assert P.bola_probe(_m(*_BOLA), "http://x", session=s, auth=both) == []
    g = _gaps(s, "bola")
    assert len(g) == 1 and "user A's create request was rejected" in g[0]["reason"] and g[0]["needs"] == "valid-body"

    class _NoOwnRead(_Quiet):
        def post(self, *a, **k):
            return MagicMock(status_code=201, text="{}", headers={})
    s = _NoOwnRead()                                 # created, but A cannot read it back (GET 404)
    assert P.bola_probe(_m(*_BOLA), "http://x", session=s, auth=both) == []
    assert "could not read its own back" in _gaps(s, "bola")[0]["reason"]

    key_only = [{"method": "POST", "path": "/tags", "request_fields": ["name"], "auth_required": True},
                {"method": "GET", "path": "/tags/{name}", "auth_required": True}]
    s = _Quiet()
    assert P.bola_probe(_m(*key_only), "http://x", session=s, auth=both) == []
    assert _gaps(s, "bola")[0]["kind"] == "not-applicable" and "only a key field" in _gaps(s, "bola")[0]["reason"]


def test_business_logic_states():
    ep = {"method": "POST", "path": "/orders", "request_fields": ["item", "quantity"]}
    s = _Quiet()
    assert P.business_logic_probe(_m({"method": "GET", "path": "/x"}), "http://x", session=s) == []
    assert _gaps(s, "business-logic")[0]["kind"] == "not-applicable"
    s = _Quiet()                                     # the POSITIVE quantity itself is rejected
    assert P.business_logic_probe(_m(ep), "http://x", session=s) == []
    g = _gaps(s, "business-logic")
    assert len(g) == 1 and "positive-value request was rejected" in g[0]["reason"] and g[0]["needs"] == "valid-body"
    s = _Accepting()                                 # accepts positives, but does not echo the negative: clean
    s2 = _Quiet()
    class _OkNoEcho(_Quiet):
        def post(self, *a, **k):
            return MagicMock(status_code=201, text="{}", headers={})
        request = post
    s = _OkNoEcho()
    assert P.business_logic_probe(_m(ep), "http://x", session=s) == [] and _gaps(s, "business-logic") == []


def test_forge_jwt_is_not_applicable_when_the_source_has_no_hardcoded_secret():
    from services.context import QAXContext
    from services.probe_service import ProbeService
    res = ProbeService(QAXContext(anthropic_key="k", project="")).run(_m(), "http://127.0.0.1:1")
    g = [x for x in res.skipped if x["probe"] == "forge-jwt"]
    assert g and g[0]["kind"] == "not-applicable" and "no hardcoded JWT signing secret" in g[0]["reason"]


# ── probe candidate ordering (found by planting bugs on the golden target) ─────────────────────────

def test_risk_ordered_puts_security_relevant_endpoints_first_without_mutating():
    from services.probe_service import risk_ordered
    from services.results import AppModel
    eps = [{"method": "GET", "path": "/aaa-health"},
           {"method": "GET", "path": "/bbb-status"},
           {"method": "POST", "path": "/zzz/upload", "request_fields": ["file"], "auth_required": True},
           {"method": "GET", "path": "/zzz/reset-link"}]
    m = AppModel(root="/x", endpoints=eps)
    ordered = risk_ordered(m)
    paths = [e["path"] for e in ordered.endpoints]
    assert paths[0] == "/zzz/upload" and paths[1] == "/zzz/reset-link"
    assert [e["path"] for e in m.endpoints] == ["/aaa-health", "/bbb-status", "/zzz/upload", "/zzz/reset-link"]
    assert len(paths) == 4                                            # nothing dropped
    assert risk_ordered(AppModel(root="/x", endpoints=[])).endpoints == []   # tiny/empty models untouched


def test_risk_ordered_is_stable_for_ties():
    from services.probe_service import risk_ordered
    from services.results import AppModel
    eps = [{"method": "GET", "path": f"/p{i}"} for i in range(6)]
    assert [e["path"] for e in risk_ordered(AppModel(root="/x", endpoints=eps)).endpoints] == [e["path"] for e in eps]


# ── found on Healthchecks (A3) ────────────────────────────────────────────────────────────────────

def test_jwt_flaws_is_not_applicable_for_an_opaque_api_key():
    import requests
    from execution.probes import jwt_flaws_probe
    from services.results import AppModel
    s = requests.Session()
    s._qax_skips = []
    m = AppModel(root="/x", endpoints=[{"method": "GET", "path": "/api/v3/checks/", "auth_required": True}])
    assert jwt_flaws_probe(m, "http://127.0.0.1:9", session=s,
                           auth={"token": "opaque-api-key-123", "header": "X-Api-Key", "scheme": ""}) == []
    assert s._qax_skips[0]["kind"] == "not-applicable" and "not a JWT" in s._qax_skips[0]["reason"]


def test_a_tiny_known_surface_is_reported_as_a_gap():
    from services.orchestrator_service import OrchestratorService
    from services.results import AppModel
    tiny = AppModel(root="/x", endpoints=[{"method": "GET", "path": "/admin"}])
    gap = OrchestratorService._surface_gap(tiny)
    assert gap and gap[0]["needs"] == "api-surface" and "only 1 endpoint" in gap[0]["reason"]
    big = AppModel(root="/x", endpoints=[{"method": "GET", "path": f"/p{i}"} for i in range(5)])
    assert OrchestratorService._surface_gap(big) == []


# ── found on microblog-api (A4) ───────────────────────────────────────────────────────────────────

def test_a_has_password_flag_is_not_an_exposed_credential():
    from execution.probes import _sensitive_keys
    assert _sensitive_keys({"has_password": True, "password_set": "yes", "password_policy": "min 8"}) == set()
    assert _sensitive_keys({"password": "hunter2"}) == {"password"}            # a real value still counts
    assert _sensitive_keys({"user": {"password_hash": "abc", "api_secret": "x"}}) == {"password_hash", "api_secret"}
    assert _sensitive_keys({"password": True}) == set()                         # a boolean can never be the secret


def test_a_put_that_echoes_an_id_is_not_a_created_record():
    from unittest.mock import MagicMock
    from execution.probes import track_writes

    class S:
        def __init__(self): self._qax_created = []
        def request(self, method, url, **kw):
            r = MagicMock(status_code=200); r.json.return_value = {"id": 1}; return r
    s = track_writes(S())
    s.request("PUT", "http://t/api/me", json={"about_me": "x"})        # update of an existing resource
    s.request("POST", "http://t/api/tokens", json={})                    # an action, not a record
    s.request("POST", "http://t/api/posts", json={"text": "x"})          # a creation
    assert [a["collection"] for a in s._qax_created] == ["/api/posts"]


def test_mass_assignment_strict_schema_rejection_is_a_pass_not_a_gap():
    """A strict app answers 400 to extra privileged fields but 201 to a clean registration: the probe WAS
    exercised and the app is safe — it must not report 'registration failed' (found on microblog-api)."""
    import requests
    from unittest.mock import MagicMock
    from execution.probes import mass_assignment_probe
    from services.results import AppModel

    class S(requests.Session):
        def post(self, url, json=None, **k):
            extras = any(f in (json or {}) for f in ("admin", "is_admin", "role"))
            return MagicMock(status_code=400 if extras else 201, json=lambda: {})
    s = S(); s._qax_skips = []; s._qax_created = []
    m = AppModel(root="/x", endpoints=[{"method": "POST", "path": "/api/users",
                 "request_fields": ["username", "email", "password"], "required_fields": ["username", "email", "password"]}])
    assert mass_assignment_probe(m, "http://t", session=s, auth={}) == []
    assert not [x for x in s._qax_skips if x["probe"] == "mass-assignment"]      # no false 'could not run'
    assert len(s._qax_created) == 1                                              # the clean user is tracked for cleanup


def test_bola_uses_the_server_generated_id_and_a_valid_email():
    import requests
    from unittest.mock import MagicMock
    from execution.probes import bola_probe
    from services.results import AppModel
    posts = {}

    class S(requests.Session):
        def post(self, url, json=None, headers=None, **k):
            if url.endswith("/api/posts"):
                assert json == {"text": json["text"]}
                posts[7] = json["text"]
                return MagicMock(status_code=201, json=lambda: {"id": 7})
            return MagicMock(status_code=201, json=lambda: {"access_token": "t"}, text="{}")

        def get(self, url, headers=None, **k):
            mine = (headers or {}).get("Authorization", "").endswith("A")
            if url.endswith("/api/posts/7"):
                return MagicMock(status_code=200 if mine else 404, text=posts.get(7, "") if mine else "nope")
            return MagicMock(status_code=404, text="")
    s = S(); s._qax_skips = []; s._qax_created = []
    m = AppModel(root="/x", endpoints=[
        {"method": "POST", "path": "/api/posts", "request_fields": ["text"], "auth_required": True},
        {"method": "GET", "path": "/api/posts/{id}", "auth_required": True}])
    out = bola_probe(m, "http://t", session=s, auth={"token": "A", "token_b": "B"})
    assert out == []                                          # B is blocked (404): safe, and it WAS exercised
    assert not [x for x in s._qax_skips if x["probe"] == "bola" and x["kind"] == "missing-prerequisite"]


def test_command_injection_ignores_a_reflected_payload_but_catches_real_output():
    """Regression (CTFd, P1 false positive): an error page that echoes the submitted `;echo TOKEN` is
    reflection, not execution. Real execution prints the token WITHOUT the 'echo'."""
    import requests
    from unittest.mock import MagicMock
    from execution.probes import command_injection_probe, _strip_echoed_payload
    from services.results import AppModel

    tok = "qaxcmdABC"
    assert tok not in _strip_echoed_payload(f"INSERT ... VALUES ('127.0.0.1;echo {tok}')", tok)
    assert tok not in _strip_echoed_payload(f"value=127.0.0.1;echo%20{tok}", tok)
    assert tok in _strip_echoed_payload(f"PING 127.0.0.1\n{tok}\n", tok)

    class Reflecting(requests.Session):                    # debug page that echoes the input back
        def request(self, method, url, json=None, **k):
            return MagicMock(status_code=500, text=f"IntegrityError for ip={(json or {}).get('ip')}")
    class Executing(requests.Session):                     # a vulnerable endpoint: runs the echo
        def request(self, method, url, json=None, **k):
            v = (json or {}).get("ip", "")
            import re
            out = [m for m in re.findall(r"echo\s+(\S+)", v)]
            return MagicMock(status_code=200, text="PING ok\n" + "\n".join(out))
    m = AppModel(root="/x", endpoints=[{"method": "POST", "path": "/api/x", "request_fields": ["ip"]}])
    for cls, expected in ((Reflecting, 0), (Executing, 1)):
        sess = cls(); sess._qax_skips = []
        assert len(command_injection_probe(m, "http://127.0.0.1:9", session=sess)) == expected, cls.__name__


def test_declared_field_types_beat_name_guessing():
    from execution.probes import _declared_value
    ep = {"request_field_types": {"draft": {"type": "boolean"}, "count": {"type": "integer"},
                                  "tags": {"type": "array"}, "kind": {"type": "string", "enum": ["a", "b"]},
                                  "title": {"type": "string"}, "when": {"type": "string", "format": "date-time"}}}
    assert _declared_value(ep, "draft", "u") is True
    assert _declared_value(ep, "count", "u") == 1 and _declared_value(ep, "tags", "u") == []
    assert _declared_value(ep, "kind", "u") == "a"
    assert _declared_value(ep, "when", "u").startswith("2020-")
    assert _declared_value(ep, "title", "u") is None            # plain string: left to the caller's marker
    assert _declared_value({}, "draft", "u") is None and _declared_value(None, "x", "u") is None


def test_route_key_field_uses_the_server_id_for_id_routes_and_a_field_for_slug_routes():
    from execution.probes import _route_key_field
    assert _route_key_field("id", ["title", "url"]) is None            # server-assigned: never a title guess
    assert _route_key_field("bookmark_id", ["title", "url"]) is None
    assert _route_key_field("pk", ["name"]) is None
    assert _route_key_field("slug", ["title", "slug"]) == "slug"        # exact name match
    assert _route_key_field("username", ["username", "email"]) == "username"
    assert _route_key_field("name", ["display_name", "x"]) == "display_name"
