"""
test_generation_quality_gate.py — `make eval-generation` runs on any configured AI provider
==========================================================================================
It used to check ANTHROPIC_API_KEY only, so a free or local OpenAI-compatible provider (which the product supports)
could not be measured. The gate now asks the provider module, and the record names the model that was measured.
"""
from pathlib import Path

import pytest

from evals import generation_quality as gq

STORIES = str(Path(__file__).resolve().parent.parent / "evals" / "data" / "generation_stories.json")


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "QAX_AI_PROVIDER", "QAX_AI_MODEL", "QAX_AI_BASE_URL", "QAX_AI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_anthropic_without_a_key_is_not_measured_and_says_how_to_use_another_provider(clean_env, tmp_path, capsys):
    ok, why = gq._ai_ready()
    assert not ok and "ANTHROPIC_API_KEY" in why and "QAX_AI_PROVIDER=openai" in why
    assert gq.run(STORIES, str(tmp_path / "q.json")) == 2
    assert "NOT MEASURED" in capsys.readouterr().out


def test_anthropic_with_a_key_is_ready(clean_env):
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    assert gq._ai_ready() == (True, "")
    assert "Anthropic" in gq._ai_label() and "sk-ant" not in gq._ai_label()


def test_openai_compatible_needs_a_model(clean_env):
    clean_env.setenv("QAX_AI_PROVIDER", "openai")
    ok, why = gq._ai_ready()
    assert not ok and "QAX_AI_MODEL" in why


def test_a_free_or_local_provider_passes_the_gate_without_an_anthropic_key(clean_env, tmp_path):
    """The case that used to be blocked: another provider configured, no Anthropic key at all."""
    clean_env.setenv("QAX_AI_PROVIDER", "openai")
    clean_env.setenv("QAX_AI_MODEL", "llama3.1:8b")
    clean_env.setenv("QAX_AI_BASE_URL", "http://localhost:11434/v1")
    assert gq._ai_ready() == (True, "")

    class PastTheGate(Exception):
        pass

    def stop(*a, **k):
        raise PastTheGate()

    clean_env.setattr(gq, "_serve_demo", stop)               # the first thing run() does once the gate is open
    with pytest.raises(PastTheGate):
        gq.run(STORIES, str(tmp_path / "q.json"))            # before the fix this returned 2 (NOT MEASURED)


def test_the_label_names_the_model_and_never_a_key(clean_env):
    clean_env.setenv("QAX_AI_PROVIDER", "openai")
    clean_env.setenv("QAX_AI_MODEL", "llama3.1:8b")
    clean_env.setenv("QAX_AI_API_KEY", "secret-should-never-print")
    label = gq._ai_label()
    assert "llama3.1:8b" in label and "secret" not in label
