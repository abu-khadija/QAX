"""
test_provider_blind_checks.py — features that checked ANTHROPIC_API_KEY only now honour the configured provider
==============================================================================================================
Found while trying every AI feature with a free provider (QAX_AI_PROVIDER=openai, no Anthropic key, 2026-10-23):
self-healing, bug-pattern extraction, selector discovery, the prompt optimizer and the gate's deep lane each looked
for the Anthropic key and decided "no AI" although a provider was configured.
"""
import pytest

from core import ai_provider


@pytest.fixture
def env(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "QAX_AI_PROVIDER", "QAX_AI_MODEL", "QAX_AI_BASE_URL", "QAX_AI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _use_openai(env):
    env.setenv("QAX_AI_PROVIDER", "openai")
    env.setenv("QAX_AI_MODEL", "some-model")


def test_resolved_key_is_the_anthropic_key_a_placeholder_for_another_provider_or_empty(env):
    assert ai_provider.resolved_key() == ""                              # nothing configured
    env.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    assert ai_provider.resolved_key() == "sk-ant-not-real"
    env.delenv("ANTHROPIC_API_KEY")
    _use_openai(env)
    assert ai_provider.resolved_key() == "openai-compatible"            # the client reads its own key
    env.delenv("QAX_AI_MODEL")
    assert ai_provider.resolved_key() == ""                              # a provider without a model is not configured


def test_self_healing_is_enabled_for_any_configured_provider(env):
    from execution.claude_healer import ClaudeHealer
    assert not ClaudeHealer()._api_key
    _use_openai(env)
    assert ClaudeHealer()._api_key


def test_the_prompt_optimizer_reaches_its_own_checks_with_another_provider(env):
    from core.prompt_optimizer import optimize_prompt
    assert "no AI provider configured" in optimize_prompt("anything")
    _use_openai(env)
    out = optimize_prompt("Not enough data yet")
    assert "no AI provider" not in out and "Cannot optimize yet" in out   # past the key check, stopped by its own rule


def test_the_gate_deep_lane_skips_with_a_message_that_names_both_ways_to_enable_it(env, capsys):
    from evals import gate
    assert gate._deep_lane() == []
    out = capsys.readouterr().out
    assert "deep lane skipped" in out and "QAX_AI_PROVIDER=openai" in out
