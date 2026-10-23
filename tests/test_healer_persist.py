"""
test_healer_persist.py — a healed selector is saved as the selector that WORKED, never re-guessed afterwards
==========================================================================================================
First real run of AI self-healing (gemini-3.5-flash, 2026-10-23): the case healed '#sign-in-button' to '#login-btn'
and passed, then selectors.json was rewritten with '#add-note-btn' (another page's button): the executor asked the
AI again after the step, on the page the click had led to.
"""
import json

import pytest

from execution.claude_healer import ClaudeHealer


@pytest.fixture
def project(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "QAX_AI_PROVIDER", "QAX_AI_MODEL"):
        monkeypatch.delenv(k, raising=False)                      # no AI configured: the method must not need one
    d = tmp_path / "projects" / "shop"
    d.mkdir(parents=True)
    path = d / "selectors.json"
    path.write_text(json.dumps({"_meta": {"last_discovered": "old"},
                                "login": {"login_button": "#sign-in-button", "username": "#username"},
                                "dashboard": {"add": "#add-note-btn"}}), encoding="utf-8")
    import core.paths as paths
    monkeypatch.setattr(paths, "user_config_dir", lambda: tmp_path)
    return path


def test_the_working_selector_replaces_the_broken_one_and_nothing_else(project):
    assert ClaudeHealer().persist_selector("#sign-in-button", "#login-btn", project="shop") is True
    data = json.loads(project.read_text(encoding="utf-8"))
    assert data["login"] == {"login_button": "#login-btn", "username": "#username"}
    assert data["dashboard"] == {"add": "#add-note-btn"}          # an unrelated entry is untouched
    assert data["_meta"]["last_discovered"] != "old"
    assert not project.with_suffix(".json.tmp").exists()          # written atomically


def test_nothing_is_invented_when_no_entry_holds_the_broken_selector(project):
    before = project.read_text(encoding="utf-8")
    assert ClaudeHealer().persist_selector("#never-there", "#login-btn", project="shop") is False
    assert project.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("broken,healed", [("", "#x"), ("#a", ""), ("#a", "#a")])
def test_empty_or_identical_selectors_change_nothing(project, broken, healed):
    before = project.read_text(encoding="utf-8")
    assert ClaudeHealer().persist_selector(broken, healed, project="shop") is False
    assert project.read_text(encoding="utf-8") == before


def test_the_executor_persists_without_asking_the_ai_again():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "execution" / "test_executor.py").read_text(encoding="utf-8")
    assert "self.healer.persist_selector(" in src
    assert "self.healer.refresh_selectors(" not in src             # the call that stored an unrelated element
