"""Tests for core/test_lint.py and `qax lint-tests` (the test-case rewrite rules)."""
import json

import pytest

from core.test_lint import lint_case, lint_cases, summary_line, render
from cli.lint_tests import run, load_cases


def _case(**kw):
    c = {"title": "[Orders] Saving a checklist step persists the entered values",
         "manual_steps": [{"step_number": 1, "action": "Fill all required fields and click Save",
                           "expected_result": "A success toast appears and the step shows the saved name."}]}
    c.update(kw)
    return c


def _rules(case):
    return {i.rule for i in lint_case(case)}


def test_clean_case_has_no_issues():
    assert lint_case(_case()) == []


def test_no_steps_is_error_but_api_steps_count():
    assert "T1" in _rules(_case(manual_steps=[]))
    assert "T1" not in _rules(_case(manual_steps=[], api_steps=[{"method": "GET"}]))


def test_empty_expected_is_error():
    c = _case(manual_steps=[{"step_number": 1, "action": "Click the Save button", "expected_result": " "}])
    assert "T2" in _rules(c)


@pytest.mark.parametrize("text,rule", [
    ("Saved. live-confirmed on UAT", "R1"),
    ("Fails with SCRUM-12 behaviour", "R1"),
    ("The page works correctly", "R4"),
    ("Result is as expected", "R4"),
    ("x" * 400, "R5"),
    ("Either an error or a redirect is shown", "R6"),
])
def test_expected_result_rules(text, rule):
    c = _case(manual_steps=[{"step_number": 1, "action": "Click the Save button", "expected_result": text}])
    assert rule in _rules(c)


@pytest.mark.parametrize("action,rule", [
    ("Login as admin@corp.com and open page", "A1"),
    ("Run TC-123 first", "A1"),
    ("Click", "A2"),
    ("A -> B -> C -> D -> E -> F -> G", "A3"),
])
def test_action_rules(action, rule):
    c = _case(manual_steps=[{"step_number": 1, "action": action, "expected_result": "The page opens."}])
    assert rule in _rules(c)


@pytest.mark.parametrize("title,rule,present", [
    ("Saving a checklist step persists the entered values", "S1", True),
    ("[A] " + "x" * 140, "S2", True),
    ("[A] short", "S3", True),
    ("[Orders] Saving a step persists values (SCRUM-9)", "S4", True),
    ("[Orders] Saving a checklist step persists the entered values - verify", "S5", True),
    ("[Orders] Saving a checklist step persists the entered values", "S5", False),
])
def test_title_rules(title, rule, present):
    assert (rule in _rules(_case(title=title))) is present


def test_malformed_input_never_raises():
    assert lint_case("nope")[0].rule == "T0"
    assert lint_case({"title": None, "manual_steps": "x"})  # no crash, T1 reported
    assert lint_case({"manual_steps": [1, None]})


def test_report_counts_and_summary():
    bad = _case(manual_steps=[])
    rep = lint_cases([_case(), bad])
    assert rep["cases"] == 2 and rep["clean"] == 1
    assert rep["by_severity"]["error"] == 1 and rep["by_rule"]["T1"] == 1
    assert "1/2 cases clean" in summary_line(rep)
    assert "T1" in render(rep)
    assert "all 1 cases pass" in summary_line(lint_cases([_case()]))
    assert "no cases" in summary_line(lint_cases([]))


def test_cli_exit_codes(tmp_path, capsys):
    f = tmp_path / "c.json"
    f.write_text(json.dumps([_case(), _case(manual_steps=[])]))
    assert run(["--file", str(f)]) == 0
    assert run(["--file", str(f), "--fail-on", "error"]) == 1
    g = tmp_path / "ok.json"
    g.write_text(json.dumps({"test_cases": [_case()]}))
    assert run(["--file", str(g), "--fail-on", "warn"]) == 0
    assert run(["--file", str(tmp_path / "missing.json")]) == 2
    assert load_cases(str(g))[0]["title"].startswith("[Orders]")
    capsys.readouterr()
    run(["--file", str(f), "--json"])
    assert json.loads(capsys.readouterr().out)["cases"] == 2
