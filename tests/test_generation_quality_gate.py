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


def test_the_cli_entry_reads_dot_env_like_main_py(clean_env, tmp_path):
    """`python -m evals.generation_quality` used to ignore .env, so a key kept there was never seen.
    (The suite blocks real .env reading, so a stand-in loader plays the part of the file.)"""
    import core.env_loader as env_loader
    loaded = []

    def fake_load_env(path=None):
        loaded.append(True)
        clean_env.setenv("QAX_AI_PROVIDER", "openai")                 # through monkeypatch: undone after the test
        clean_env.setenv("QAX_AI_MODEL", "some-model")
        return {}

    clean_env.setattr(env_loader, "load_env", fake_load_env)
    clean_env.setattr("sys.argv", ["generation_quality", "--stories", STORIES, "--out", str(tmp_path / "q.json")])

    class PastTheGate(Exception):
        pass

    def stop(*a, **k):
        raise PastTheGate()

    clean_env.setattr(gq, "_serve_demo", stop)
    with pytest.raises(PastTheGate):               # the gate opened because the loader ran first (else: exit 2)
        gq.main()
    assert loaded


def test_the_label_names_the_model_and_never_a_key(clean_env):
    clean_env.setenv("QAX_AI_PROVIDER", "openai")
    clean_env.setenv("QAX_AI_MODEL", "llama3.1:8b")
    clean_env.setenv("QAX_AI_API_KEY", "secret-should-never-print")
    label = gq._ai_label()
    assert "llama3.1:8b" in label and "secret" not in label


def test_a_false_failure_is_recorded_with_the_step_and_the_engine_words():
    """Only the case ids used to be saved: a repeating false failure (TC-SEC-0002, 3 of 3 runs) could not be diagnosed."""
    from evals import generation_quality as gq
    results = [
        {"test_case_id": "TC-A", "status": "PASS"},
        {"test_case_id": "TC-B", "status": "FAIL", "steps": [
            {"step_number": 1, "action_type": "navigate", "status": "PASS", "actual_result": "ok"},
            {"step_number": 2, "action_type": "click", "status": "FAIL", "actual_result": "Element   not found:\n #go"}]},
        {"test_case_id": "TC-C", "status": "ERROR", "error": "boom"},
    ]
    out = gq.score_run(results, {"AC-1": ["TC-A"], "AC-2": ["TC-B"]}, planted=[])
    assert out["false_failures"] == ["TC-B"] and out["error_pct"] == 33        # an ERROR is not a false failure, it is counted apart
    assert out["why"]["TC-B"] == "step 2 (click) FAIL: Element not found: #go"
    assert out["why"]["TC-C"] == "boom"
    assert "TC-A" not in out["why"]


def test_cases_the_validator_refused_are_recorded_with_the_reason(tmp_path):
    """A story that scored 0 cases twice gave no clue why; the refused cases and the validator's words are now in the record."""
    import json
    from evals import generation_quality as gq
    story = {"stories": [{"id": "s", "story": "As a user\n- It works", "start_path": "/login"}]}
    f = tmp_path / "s.json"
    f.write_text(json.dumps(story), encoding="utf-8")

    def gen(story_text, app_map, ctx):
        return [], None
    gen.dropped = [{"id": "TC-X-0001", "why": "Schema: 'selector_key' is a required property"}]
    out = tmp_path / "o.json"
    gq.run(str(f), str(out), generate=gen, execute=False)
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["stories"][0]["review"]["dropped"] == gen.dropped
