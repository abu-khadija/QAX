"""QAX_AI_MAX_CALLS: a ceiling on AI calls per run, off unless set."""

import pytest

from services import usage


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("QAX_AI_MAX_CALLS", raising=False)
    usage.reset()
    yield
    usage.reset()


def test_no_cap_by_default_even_after_many_calls():
    for _ in range(50):
        usage.note(1, 1, "other:m")
    usage.check_cap()


def test_the_call_after_the_cap_is_refused(monkeypatch):
    monkeypatch.setenv("QAX_AI_MAX_CALLS", "3")
    for _ in range(3):
        usage.check_cap()
        usage.note(1, 1, "other:m")
    with pytest.raises(usage.AICallLimit, match="QAX_AI_MAX_CALLS=3"):
        usage.check_cap()


def test_a_reset_starts_a_new_budget(monkeypatch):
    monkeypatch.setenv("QAX_AI_MAX_CALLS", "1")
    usage.note(1, 1, "other:m")
    usage.reset()
    usage.check_cap()


@pytest.mark.parametrize("value", ["", "0", "-5", "many"])
def test_zero_negative_or_garbage_means_no_cap(monkeypatch, value):
    monkeypatch.setenv("QAX_AI_MAX_CALLS", value)
    usage.note(1, 1, "other:m")
    usage.check_cap()


def test_the_openai_compatible_client_stops_at_the_cap(monkeypatch):
    """The provider checks the cap BEFORE it sends anything (no request is made once the ceiling is reached)."""
    from core import ai_provider
    monkeypatch.setenv("QAX_AI_MAX_CALLS", "1")
    monkeypatch.setenv("QAX_AI_PROVIDER", "openai")
    monkeypatch.setenv("QAX_AI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("QAX_AI_API_KEY", "x")
    monkeypatch.setenv("QAX_AI_MODEL", "m")
    usage.note(1, 1, "other:m")
    sent = []
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: sent.append(1))
    client = ai_provider.OpenAICompatClient()
    with pytest.raises(usage.AICallLimit):
        client.messages.create(model="m", max_tokens=10, messages=[{"role": "user", "content": "hi"}])
    assert sent == []
