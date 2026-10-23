"""
test_system_prompt_rules.py — the rules the generator is told about browser cases
================================================================================
First measurement of AI-written cases (gemini-3.5-flash, 2026-10-23): 0 PASS of 4. Two causes were in the prompt:
(1) every step "must carry an assertion", so a click step asserted on the button it had just clicked, which is gone
once the page changes; (2) "start from a fresh session" was only said for API steps, so cases began on /dashboard.
These pin the two rules so a prompt edit cannot silently drop them; the real proof is `make eval-generation`.
"""
from pathlib import Path

from core.claude_client import SYSTEM_PROMPT_PATH


def _prompt() -> str:
    return Path(SYSTEM_PROMPT_PATH).read_text(encoding="utf-8")


def test_a_check_is_its_own_assert_step_never_riding_on_a_click():
    p = _prompt()
    assert "one action per step; every check is its own `assert` step" in p
    assert "evaluated against THAT step's own element" in p       # the reason, so the model does not guess
    assert "never the element that was clicked" in p


def test_browser_cases_start_signed_out_and_log_in_themselves():
    p = _prompt()
    assert "self-contained and start signed out" in p
    assert "FIRST steps navigate to the login page" in p
    assert "never assume an earlier case logged in" in p


def test_the_prompt_is_the_file_the_client_loads():
    assert Path(SYSTEM_PROMPT_PATH).name == "system_prompt.md" and Path(SYSTEM_PROMPT_PATH).is_file()
