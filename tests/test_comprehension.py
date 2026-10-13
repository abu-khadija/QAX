"""
test_comprehension.py — RepoComprehensionService (Understand step)
===================================================================
Offline tests — the input adapter and the Anthropic client are both mocked, so
no network or key is needed beyond the config guard.

Run with:
    pytest tests/test_comprehension.py -v
"""

from unittest.mock import MagicMock, patch

import pytest

from services.comprehension_service import RepoComprehensionService
from services.context import QAXContext
from services.exceptions import QAXConfigError


def _ctx() -> QAXContext:
    return QAXContext(anthropic_key="k", project="")


class _FakeAdapter:
    def __init__(self, items):
        self._items = items

    def fetch(self, source, **kwargs):
        return self._items


def _item(path="a.py", content="def f():\n    return 1") -> dict:
    return {"file_path": path, "content": content, "file_name": path,
            "source_type": "folder", "metadata": {}}


def _fake_anthropic(json_text: str):
    """A mock anthropic client whose messages.create returns json_text."""
    client = MagicMock()
    block = MagicMock()
    block.text = json_text
    msg = MagicMock()
    msg.content = [block]
    client.messages.create.return_value = msg
    return client


# ─────────────────────────────────────────────────────────────────────────────

def test_missing_key_raises():
    with pytest.raises(QAXConfigError):
        RepoComprehensionService(QAXContext(anthropic_key="")).comprehend(
            "x", input_type="folder")


def test_empty_source_returns_error():
    with patch("core.input_adapters.get_input_adapter", return_value=_FakeAdapter([])):
        r = RepoComprehensionService(_ctx()).comprehend("x", input_type="folder")
    assert r.success is False
    assert r.error


def test_parses_app_map():
    js = ('{"overview":"A login module","main_flows":["login","reset password"],'
          '"entry_points":["auth.py: login handler"],'
          '"risk_areas":["auth","validation"],'
          '"files":[{"path":"auth.py","role":"login handler"}]}')
    with patch("core.input_adapters.get_input_adapter", return_value=_FakeAdapter([_item()])), \
         patch("anthropic.Anthropic", return_value=_fake_anthropic(js)):
        r = RepoComprehensionService(_ctx()).comprehend("src/", input_type="folder", ext=".py")
    assert r.success
    assert r.overview == "A login module"
    assert "auth" in r.risk_areas
    assert r.file_count == 1
    ctx = r.as_context()
    assert "login module" in ctx and "auth" in ctx


def test_parses_json_wrapped_in_prose():
    js = 'Here is the map:\n{"overview":"X","risk_areas":["auth"]}\nDone.'
    with patch("core.input_adapters.get_input_adapter", return_value=_FakeAdapter([_item()])), \
         patch("anthropic.Anthropic", return_value=_fake_anthropic(js)):
        r = RepoComprehensionService(_ctx()).comprehend("src/", input_type="folder")
    assert r.success
    assert r.overview == "X"


def test_bad_json_is_empty_not_crash():
    with patch("core.input_adapters.get_input_adapter", return_value=_FakeAdapter([_item()])), \
         patch("anthropic.Anthropic", return_value=_fake_anthropic("not json at all")):
        r = RepoComprehensionService(_ctx()).comprehend("src/", input_type="folder")
    assert r.success           # no outright error
    assert r.overview == ""     # just an empty map
    assert r.as_context() == ""
