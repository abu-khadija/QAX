"""
test_appmodel.py — AppModelService + AppModel (whole-system comprehension)
=========================================================================
Offline tests — the Anthropic client and the filesystem digest are exercised on
a tiny fake repo, with the model call mocked.

Run with:
    pytest tests/test_appmodel.py -v
"""

import json
from unittest.mock import MagicMock, patch

from services.appmodel_service import AppModelService
from services.context import QAXContext
from services.results import AppModel


def _ctx() -> QAXContext:
    return QAXContext(anthropic_key="k", project="")


def _fake_anthropic(json_text: str):
    client = MagicMock()
    block = MagicMock(); block.text = json_text
    msg = MagicMock(); msg.content = [block]
    client.messages.create.return_value = msg
    return client


def _model_json() -> str:
    return json.dumps({
        "files": [{"path": "app/config.py", "role": "config"}],
        "endpoints": [{"method": "POST", "path": "/auth/login", "auth_required": False,
                       "status_codes": [200, 401], "file": "app/routers/auth.py"}],
        "entities": [{"name": "UserCreate",
                      "fields": [{"name": "password", "type": "str", "constraints": "min_length=8,max_length=128"}],
                      "file": "app/schemas.py"}],
        "config": [
            {"key": "jwt_secret", "default": "change-this-to-a-long-random-secret",
             "is_secret": True, "risk": "anyone can forge tokens for any user"},
            {"key": "jwt_expire_minutes", "default": 60, "is_secret": False, "risk": ""},
        ],
        "auth": {"scheme": "JWT", "token_type": "bearer", "secret_source": "config default",
                 "password_hash": "bcrypt"},
        "contracts": [{"frontend_call": "POST /todos", "backend_endpoint": "POST /todos",
                       "mismatch": "frontend default page_size 10, backend 20"}],
        "risks": ["password max_length 128 exceeds bcrypt's 72-byte limit → 500"],
    })


# ─────────────────────────────────────────────────────────────────────────────

def test_missing_path_is_error():
    model = AppModelService(_ctx()).build("/no/such/repo")
    assert not model.success
    assert "not found" in (model.error or "").lower()


def test_builds_model_and_surfaces_exposed_secret(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "config.py").write_text('jwt_secret = "change-this"\n')
    (tmp_path / "app" / "main.py").write_text("x = 1\n")
    with patch("anthropic.Anthropic", return_value=_fake_anthropic(_model_json())):
        model = AppModelService(_ctx()).build(str(tmp_path))
    assert model.success
    assert len(model.endpoints) == 1
    # The crown-jewel finding: a hardcoded secret is surfaced.
    assert len(model.exposed_secrets) == 1
    assert model.exposed_secrets[0]["key"] == "jwt_secret"
    # And a cross-file contradiction is captured as a risk.
    assert any("bcrypt" in r for r in model.risks)


def test_contract_mismatch_view(tmp_path):
    (tmp_path / "f.py").write_text("x=1\n")
    with patch("anthropic.Anthropic", return_value=_fake_anthropic(_model_json())):
        model = AppModelService(_ctx()).build(str(tmp_path))
    assert len(model.contract_mismatches) == 1
    ctx_text = model.as_context()
    assert "EXPOSED SECRETS" in ctx_text
    assert "jwt_secret" in ctx_text


def test_non_secret_default_is_not_flagged(tmp_path):
    (tmp_path / "f.py").write_text("x=1\n")
    reply = json.dumps({"config": [{"key": "jwt_expire_minutes", "default": 60,
                                    "is_secret": False, "risk": ""}]})
    with patch("anthropic.Anthropic", return_value=_fake_anthropic(reply)):
        model = AppModelService(_ctx()).build(str(tmp_path))
    assert model.exposed_secrets == []


def test_bad_json_captured_not_crash(tmp_path):
    (tmp_path / "f.py").write_text("x=1\n")
    with patch("anthropic.Anthropic", return_value=_fake_anthropic("not json")):
        model = AppModelService(_ctx()).build(str(tmp_path))
    assert model.success            # empty model, not an error
    assert model.endpoints == []


def test_skips_vendored_dirs(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "real.py").write_text("x=1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("y=2\n")
    files = AppModelService._collect_files(tmp_path, 60)
    names = [f.name for f in files]
    assert "real.py" in names
    assert "lib.js" not in names
