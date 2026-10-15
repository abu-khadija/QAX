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


class _Dead(_Led):
    """Nothing is listening: every request fails at the network level."""
    def _boom(self, *a, **k): raise ConnectionError("refused")
    get = post = put = delete = request = _boom


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
    s = _Quiet()
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
