"""
test_probes.py — active exploit probes + ProbeService
=====================================================
The JWT signer is checked against a real verifier when available; the network
probes run against a fake in-process app so no live server is needed.

Run with:
    pytest tests/test_probes.py -v
"""

from unittest.mock import MagicMock

from execution.probes import crash_fuzz_probe, forge_jwt_probe, sign_jwt_hs
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


# ── ProbeService ─────────────────────────────────────────────────────────────

def test_service_requires_model_and_url():
    assert not ProbeService(_ctx()).run(AppModel(error="bad"), "http://x").success
    assert not ProbeService(_ctx()).run(_model_with_secret(), "").success
