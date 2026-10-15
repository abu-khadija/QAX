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


# probe-id, function, endpoint a candidate-less app lacks, endpoint that gives it work
_CASES = {
    "host-header-injection": (P.host_header_injection_probe, {"method": "GET", "path": "/home"}),
    "xxe": (P.xxe_probe, {"method": "POST", "path": "/import"}),
    "file-upload": (P.file_upload_probe, {"method": "POST", "path": "/upload", "request_fields": ["file"]}),
    "ssti": (P.ssti_probe, {"method": "GET", "path": "/greet", "query_params": ["name"]}),
    "xss": (P.xss_probe, {"method": "GET", "path": "/greet", "query_params": ["name"]}),
}
_IDS = list(_CASES)


@pytest.mark.parametrize("probe", _IDS)
def test_nothing_to_test_is_reported_as_not_applicable_not_a_gap(probe):
    fn, _ = _CASES[probe]
    s = _Quiet()
    assert fn(_m({"method": "DELETE", "path": "/x"}), "http://x", session=s) == []
    g = _gaps(s, probe)
    assert len(g) == 1 and g[0]["kind"] == "not-applicable"


@pytest.mark.parametrize("probe", _IDS)
def test_an_endpoint_needing_auth_without_a_token_is_a_missing_prerequisite(probe):
    fn, ep = _CASES[probe]
    s = _Quiet()
    fn(_m({**ep, "auth_required": True}), "http://x", session=s)
    g = [x for x in _gaps(s, probe) if x["kind"] == "missing-prerequisite" and x.get("needs") == "auth-token"]
    assert g, _gaps(s, probe)


@pytest.mark.parametrize("probe", _IDS)
def test_a_dead_server_is_reported_as_unreached_never_as_clean(probe):
    fn, ep = _CASES[probe]
    s = _Dead()
    assert fn(_m(ep), "http://x", session=s) == []
    g = [x for x in _gaps(s, probe) if "could be reached" in x["reason"]]
    assert g and g[0]["kind"] == "missing-prerequisite"


@pytest.mark.parametrize("probe", _IDS)
def test_a_normal_server_that_simply_answers_produces_no_gap(probe):
    fn, ep = _CASES[probe]
    s = _Quiet()
    assert fn(_m(ep), "http://x", session=s) == []
    assert _gaps(s, probe) == []                    # reachable + nothing wrong ≠ a coverage gap


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
    for fn, ep in _CASES.values():
        assert fn(_m(ep), "http://x", session=_Bare()) == []
