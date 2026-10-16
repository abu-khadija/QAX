"""core/read_only.py + its wiring — a --read-only scan sends no write request (proven server-side in the gate)."""
import pytest

from core import read_only as RO


class _Sess:
    def __init__(self):
        self.sent = []

    def request(self, method, url, *a, **k):
        self.sent.append((method, url))
        return "ok"

    def post(self, url, **k):              # like requests.Session: post → self.request
        return self.request("POST", url, **k)

    def get(self, url, **k):
        return self.request("GET", url, **k)


def cookie_flags_probe(sess):              # a fake probe frame, to check attribution from the call stack
    return sess.post("http://t/auth/register")


def test_guard_refuses_writes_without_sending_and_attributes_the_probe():
    s = RO.guard(_Sess())
    assert s.get("http://t/x") == "ok"
    with pytest.raises(RO.ReadOnlyViolation):
        cookie_flags_probe(s)
    with pytest.raises(RO.ReadOnlyViolation):
        s.request("DELETE", "http://t/items/1")
    assert s.sent == [("GET", "http://t/x")]                      # the writes were never sent
    b = RO.blocked(s)
    assert b[0] == {"probe": "cookie-flags", "method": "POST", "path": "/auth/register"}
    assert b[1]["probe"] == "auth-setup" and b[1]["method"] == "DELETE"
    assert RO.guard(s) is s                                       # idempotent


def test_ledger_entries_replace_side_effects_of_a_refusal():
    existing = [{"probe": "cookie-flags", "kind": "missing-prerequisite", "reason": "unreached"},
                {"probe": "cors", "kind": "not-applicable", "reason": "x"}]
    blocked = [{"probe": "cookie-flags", "method": "POST", "path": "/auth/register"}] * 2 + \
              [{"probe": "cookie-flags", "method": "POST", "path": "/auth/login"}]
    out = RO.ledger_entries(blocked, existing)
    cf = [e for e in out if e["probe"] == "cookie-flags"]
    assert len(cf) == 1 and cf[0]["kind"] == "read-only" and cf[0]["needs"] == "write-permission"
    assert "POST /auth/register" in cf[0]["reason"] and "POST /auth/login" in cf[0]["reason"]
    assert {"probe": "cors", "kind": "not-applicable", "reason": "x"} in out


def test_safe_cases_keeps_only_all_safe_tests():
    safe, unsafe = RO.safe_cases([
        {"title": "a", "api_steps": [{"method": "GET"}, {"method": "head"}]},
        {"title": "b", "api_steps": [{"method": "GET"}, {"method": "POST"}]},
        {"title": "c", "api_steps": [{"path": "/x"}]},            # no method = GET
        {"title": "d"}])                                          # no api_steps: not executable as API
    assert [t["title"] for t in safe] == ["a", "c"] and [t["title"] for t in unsafe] == ["b", "d"]


def test_orchestrator_records_the_tests_it_did_not_run():
    from services.orchestrator_service import OrchestratorService
    from services.progress import NullProgressReporter
    skips = []
    kept = OrchestratorService._read_only_cases(
        [{"title": "read", "api_steps": [{"method": "GET"}]},
         {"title": "create a task", "api_steps": [{"method": "POST"}]}], skips, NullProgressReporter())
    assert [t["title"] for t in kept] == ["read"]
    assert skips[0]["kind"] == "read-only" and "1 generated/coverage test" in skips[0]["reason"]


def test_family_sweep_records_and_cleans_what_it_created(monkeypatch):
    """Regression: the family sweep wrote records on an untracked session — never cleaned, never reported."""
    import execution.probes as P
    from services.orchestrator_service import OrchestratorService
    o = OrchestratorService.__new__(OrchestratorService)
    o._family_side = {"created": 0, "cleaned": 0, "leftover": [], "not_tested": []}

    class S:
        _qax_created = [{"collection": "/notes", "body": {"title": "x"}, "headers": {}}] * 2
    monkeypatch.setattr(P, "cleanup_artifacts", lambda m, b, s: 1)
    monkeypatch.setattr(P, "leftover_artifacts", lambda s, why: [{"path": "/notes", "why": why}])
    o._family_bookkeeping(None, "http://t", S(), read_only=False)
    side = o._family_side
    assert side["created"] == 2 and side["cleaned"] == 1 and side["leftover"][0]["why"].startswith("family sweep")


def test_track_writes_registers_accepted_creations_only():
    """Regression: probes POSTed into create endpoints without registering it → LEFT ON THE TARGET under-reported."""
    from unittest.mock import MagicMock
    from execution.probes import _record_created, track_writes

    class S:
        def __init__(self):
            self._qax_created = []

        def request(self, method, url, **kw):
            from urllib.parse import urlparse
            path = urlparse(url).path
            if path == "/notes":
                return MagicMock(status_code=201)
            if path == "/orders":
                r = MagicMock(status_code=200)
                r.json.return_value = {"id": 7}
                return r
            r = MagicMock(status_code=200)
            r.json.return_value = {"access_token": "x"}           # login / lookup: not a record
            return r

    s = track_writes(S())
    s.request("POST", "http://t/notes", json={"title": "a"})
    s.request("POST", "http://t/orders", json={"item": "x"})
    s.request("POST", "http://t/auth/login", json={"email": "e"})
    s.request("GET", "http://t/notes")
    assert [a["collection"] for a in s._qax_created] == ["/notes", "/orders"]
    _record_created(s, "/notes", {"title": "a"}, {"Authorization": "Bearer t"})   # explicit record: de-duplicated
    assert len(s._qax_created) == 2 and s._qax_created[0]["headers"] == {"Authorization": "Bearer t"}
    assert "auto" not in s._qax_created[0]


def test_cookie_auth_is_carried_by_the_probe_session_end_to_end():
    """A session cookie supplied via auth['cookie'] reaches an authed endpoint through the shared probe
    session — proven against a tiny live app that gates content on the cookie."""
    import socket
    import threading
    import wsgiref.simple_server as ws

    import requests

    from execution.probes import excessive_data_probe, seed_cookies
    from services.results import AppModel

    def app(environ, start_response):
        authed = "sid=secret-sess" in environ.get("HTTP_COOKIE", "")
        body = b'{"password": "leaked"}' if authed else b'{"error": "login required"}'
        start_response("200 OK", [("Content-Type", "application/json")])
        return [body]

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = ws.make_server("127.0.0.1", port, app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    model = AppModel(root="/x", endpoints=[{"method": "GET", "path": "/account", "auth_required": True}])
    try:
        # no cookie → the endpoint returns the "login required" body, nothing sensitive is seen
        sess = requests.Session()
        out_no = excessive_data_probe(model, base, session=sess, auth={})
        # with the cookie → the probe session carries it and sees the leaked credential
        sess2 = requests.Session()
        seed_cookies(sess2, "sid=secret-sess")
        out_yes = excessive_data_probe(model, base, session=sess2, auth={"cookie": "sid=secret-sess"})
    finally:
        srv.shutdown()
    assert out_no == []                                   # unauthenticated: no sensitive field exposed
    assert out_yes and out_yes[0].probe == "excessive-data"   # cookie carried → authed content probed


def test_cleanup_uses_the_server_assigned_id_from_the_create_response():
    """Regression (microblog-api): a post created with {"text": ...} has no key in its body; the id the server
    returned must be used to delete it instead of reporting 'could not work out its key'."""
    from unittest.mock import MagicMock
    from execution.probes import cleanup_artifacts, track_writes
    from services.results import AppModel

    class S:
        def __init__(self): self._qax_created = []; self.deleted = []
        def request(self, method, url, **kw):
            r = MagicMock(status_code=201); r.json.return_value = {"id": 7}; return r
        def delete(self, url, headers=None, timeout=None):
            self.deleted.append(url); return MagicMock(status_code=204)
    s = track_writes(S())
    s.request("POST", "http://t/api/posts", json={"text": "x"}, headers={"Authorization": "Bearer A"})
    m = AppModel(root="/x", endpoints=[{"method": "DELETE", "path": "/api/posts/{id}"}])
    assert cleanup_artifacts(m, "http://t", s) == 1 and s.deleted == ["http://t/api/posts/7"]
