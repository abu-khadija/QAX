"""
test_ai_provider_client.py — the OpenAI-compatible client against a free tier that is busy and thinks too much
==============================================================================================================
Found on the first real run with a free provider (gemini-3.5-flash, 2026-10-23):
  * a 503 ("high demand") ended a whole measurement: the client did not retry;
  * with the default settings the model returned 11 tokens of a 300 budget (finish=length): it spent the budget
    thinking. `reasoning_effort: none` returned the full answer.
"""
import logging

import pytest
import requests

from core import ai_provider


class FakeResp:
    def __init__(self, status=200, body=None, text="", headers=None):
        self.status_code, self._body, self.text, self.headers = status, body, text, headers or {}

    def json(self):
        return self._body


def _ok(text="OK", finish="stop"):
    return FakeResp(200, {"choices": [{"message": {"content": text}, "finish_reason": finish}],
                          "usage": {"prompt_tokens": 5, "completion_tokens": 3}, "model": "m"})


@pytest.fixture
def client(monkeypatch):
    for k in ("QAX_AI_REASONING_EFFORT", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("QAX_AI_PROVIDER", "openai")
    monkeypatch.setenv("QAX_AI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("QAX_AI_API_KEY", "k")
    monkeypatch.setenv("QAX_AI_MODEL", "m")
    waits = []
    monkeypatch.setattr(ai_provider, "_SLEEP", lambda s: waits.append(s))
    return waits


def _call():
    return ai_provider.make_ai_client().messages.create(model="x", max_tokens=100,
                                                        messages=[{"role": "user", "content": "hi"}])


def test_reasoning_effort_is_sent_only_when_asked_for(client, monkeypatch):
    bodies = []
    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: bodies.append(json) or _ok())
    _call()
    assert "reasoning_effort" not in bodies[0]               # other servers may reject a field they do not know
    monkeypatch.setenv("QAX_AI_REASONING_EFFORT", "None")
    _call()
    assert bodies[1]["reasoning_effort"] == "none"


def test_a_busy_service_is_retried_then_answers(client, monkeypatch):
    answers = [FakeResp(503, text="high demand"), FakeResp(429, headers={"Retry-After": "3"}), _ok("hello")]
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or answers.pop(0))
    assert _call().content[0].text == "hello"
    assert len(calls) == 3 and client == [1.0, 3.0]          # backoff 1 s, then the server's Retry-After


def test_a_client_error_is_not_retried(client, monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or FakeResp(400, text="bad model"))
    with pytest.raises(RuntimeError, match="Error code: 400"):
        _call()
    assert len(calls) == 1


def test_retries_are_bounded(client, monkeypatch):
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or FakeResp(503, text="high demand"))
    with pytest.raises(RuntimeError, match="Error code: 503"):
        _call()
    assert len(calls) == 4


def test_a_hanging_model_is_not_retried_for_minutes(client, monkeypatch):
    calls = []

    def hang(*a, **k):
        calls.append(1)
        raise requests.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(requests, "post", hang)
    with pytest.raises(requests.exceptions.ReadTimeout):
        _call()
    assert len(calls) == 1                                    # 300 s x 4 would be 20 minutes of nothing


def test_a_very_short_cut_answer_tells_how_to_fix_it(client, monkeypatch, caplog):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _ok("Here are", finish="length"))
    with caplog.at_level(logging.WARNING, logger="core.ai_provider"):
        r = _call()
    assert r.stop_reason == "max_tokens"
    assert "QAX_AI_REASONING_EFFORT=none" in caplog.text
