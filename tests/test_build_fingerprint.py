"""core/build_fingerprint.py — a build id built only from components that are stable across two fetches."""
import itertools
from unittest.mock import MagicMock

from core.build_fingerprint import build_fingerprint, describe, same_build


class _Sess:
    """Serves a spec and a root document; `root_body` may be a callable to simulate per-request noise."""
    def __init__(self, spec=None, root_body="<html>app</html>", headers=None):
        self.spec, self.root_body, self.headers = spec, root_body, headers or {}
        self._n = itertools.count()

    def get(self, url, timeout=None, allow_redirects=True, **k):
        body = self.root_body() if callable(self.root_body) else self.root_body
        if url.endswith("/swagger.v1.json") or url.endswith("/openapi.json"):
            if self.spec is None:
                return MagicMock(status_code=404, text="", headers={}, json=lambda: {})
            return MagicMock(status_code=200, text="", headers={}, json=lambda: self.spec)
        if url.endswith("/") and url.count("/") == 3:
            return MagicMock(status_code=200, text=body, headers=self.headers)
        return MagicMock(status_code=404, text="", headers={}, json=lambda: {})


_SPEC = {"swagger": "2.0", "info": {"version": "28.1.0"}, "paths": {"/x": {"get": {}}}}


def test_stable_app_gets_an_id_and_version():
    b = build_fingerprint("http://x", session=_Sess(spec=_SPEC))
    assert b and len(b["id"]) == 12 and b["version"] == "28.1.0"
    assert {"spec-sha", "spec-version", "root-body"} <= set(b["components"])


def test_same_app_twice_is_the_same_build():
    assert same_build(build_fingerprint("http://x", session=_Sess(spec=_SPEC)),
                      build_fingerprint("http://x", session=_Sess(spec=_SPEC))) is True


def test_a_changed_spec_is_a_new_build():
    v2 = {**_SPEC, "info": {"version": "28.2.0"}}
    assert same_build(build_fingerprint("http://x", session=_Sess(spec=_SPEC)),
                      build_fingerprint("http://x", session=_Sess(spec=v2))) is False


def test_per_request_noise_never_makes_the_same_build_look_new():
    counter = itertools.count()
    noisy = lambda: f"<html>csrf={next(counter)}</html>"            # changes on EVERY fetch
    a = build_fingerprint("http://x", session=_Sess(spec=_SPEC, root_body=noisy))
    b = build_fingerprint("http://x", session=_Sess(spec=_SPEC, root_body=noisy))
    assert "root-body" not in a["components"]                        # unstable component dropped
    assert same_build(a, b) is True                                  # …so the build id is unaffected


def test_nothing_stable_means_unknown_not_a_made_up_id():
    counter = itertools.count()
    assert build_fingerprint("http://x", session=_Sess(spec=None, root_body=lambda: str(next(counter)))) is None


def test_network_failure_returns_none():
    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("down")
    assert build_fingerprint("http://x", session=_Boom()) is None


def test_same_build_is_unknown_when_either_side_is_unknown():
    known = {"id": "abc"}
    assert same_build(known, None) is None and same_build(None, known) is None and same_build(None, None) is None
    assert same_build(known, {"id": "abc"}) is True and same_build(known, {"id": "def"}) is False


def test_describe():
    assert describe({"id": "abc123", "version": "1.2"}) == "abc123 (1.2)"
    assert describe({"id": "abc123"}) == "abc123" and describe(None) == "unknown"
