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
}
_IDS = list(_CASES)
_NA = [k for k, v in _CASES.items() if v[2]]
_TOK = [k for k, v in _CASES.items() if v[3]]


@pytest.mark.parametrize("probe", _NA)
def test_nothing_to_test_is_reported_as_not_applicable_not_a_gap(probe):
    fn = _CASES[probe][0]
    s = _Quiet()
    assert fn(_m({"method": "DELETE", "path": "/x"}), "http://x", session=s) == []
    g = _gaps(s, probe)
    assert len(g) == 1 and g[0]["kind"] == "not-applicable"


@pytest.mark.parametrize("probe", _TOK)
def test_an_endpoint_needing_auth_without_a_token_is_a_missing_prerequisite(probe):
    fn, ep = _CASES[probe][:2]
    s = _Quiet()
    fn(_m({**ep, "auth_required": True}), "http://x", session=s)
    g = [x for x in _gaps(s, probe) if x["kind"] == "missing-prerequisite" and x.get("needs") == "auth-token"]
    assert g, _gaps(s, probe)


@pytest.mark.parametrize("probe", _IDS)
def test_a_dead_server_is_reported_as_unreached_never_as_clean(probe):
    fn, ep = _CASES[probe][:2]
    s = _Dead()
    assert fn(_m(ep), "http://x", session=s) == []
    g = [x for x in _gaps(s, probe) if "could be reached" in x["reason"] or "could not exercise" in x["reason"]]
    assert g and g[0]["kind"] == "missing-prerequisite", _gaps(s, probe)


@pytest.mark.parametrize("probe", _IDS)
def test_a_normal_server_that_simply_answers_produces_no_gap(probe):
    fn, ep = _CASES[probe][:2]
    s = _Quiet()
    assert fn(_m(ep), "http://x", session=s) == []
    assert _gaps(s, probe) == []                    # reachable + nothing wrong ≠ a coverage gap


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
