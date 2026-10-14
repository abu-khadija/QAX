"""S8: extracted run/push summary formatters (pure, testable)."""
from cli.summaries import render_push_summary, render_run_summary


def test_push_summary_lists_keys_and_failures():
    out = render_push_summary({"summary": {"pushed_count": 1, "failed_count": 1},
                               "pushed": [{"test_case_id": "TC-1", "jira_key": "PROJ-9"}],
                               "failed": [{"test_case_id": "TC-2", "error": "boom"}]})
    assert "PROJ-9" in out and "TC-2" in out and "boom" in out and "Pushed : 1" in out


def test_run_summary_ok_and_error():
    ok = render_run_summary("regression", {"total_valid": 5, "duration_seconds": 2.0})
    assert "complete" in ok and "Valid     : 5" in ok
    err = render_run_summary("smoke", {"error": "nope"})
    assert "failed" in err and "nope" in err
