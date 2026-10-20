"""
test_ui_executor_honesty.py — the browser test executor's verdicts and Jira writes
==================================================================================
1. A case is PASS only when every step ran and passed: a SKIP step (unrecognised
   action, no browser) or an ERROR step proved nothing and must not yield PASS.
2. Auto-filed bugs on FAIL (AUTO_BUG_REPORT) are drafts unless the caller opts in
   explicitly — `DRY_RUN=false` in .env alone is not an operator's OK to write to Jira.
   `pipeline --dry-run` and `execute` without `--upload` used to file live bugs.

Offline: no Playwright, no browser, no Jira. The step runner and the reporter are stubbed.
"""

import asyncio

import pytest

import execution.test_executor as te


class _Runner:
    """Stand-in for PlaywrightRunner: ready, with a page whose snapshot fails (non-fatal)."""

    def is_ready(self):
        """Report the runner as set up."""
        return True

    async def get_page(self):
        """Return a page stub whose content() raises, so no snapshot file is written."""
        class _Page:
            async def content(self):
                raise RuntimeError("no page in tests")
        return _Page()

    async def take_screenshot(self, name):
        """Pretend a screenshot could not be taken."""
        return ""


def _executor(statuses, dry_run=True):
    """A TestExecutor whose steps return `statuses` in order (no ActionMapper/healer needed)."""
    ex = te.TestExecutor.__new__(te.TestExecutor)
    ex.dry_run = dry_run
    ex.action_mapper = object()
    ex.healer = None
    it = iter(statuses)

    async def _step(step, page, runner, tc_id):
        return {"step_number": step.get("step_number"), "status": next(it),
                "actual_result": "", "error": None}
    ex._execute_step = _step
    return ex


def _case(n):
    """A test case with n automation steps."""
    return {"test_case_id": "TC-1", "title": "t",
            "automation_steps": [{"action": f"step {i}"} for i in range(n)]}


def _run(ex, n):
    """Execute a case of n steps and return its result dict."""
    return asyncio.run(ex.execute_test_case(_case(n), _Runner()))


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    """Pretend the execution layer exists; keep any file writes in a temp dir; no auto bugs."""
    monkeypatch.setattr(te, "_EXECUTION_AVAILABLE", True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTO_BUG_REPORT", "false")


@pytest.mark.parametrize("statuses, expected", [
    (["PASS", "PASS"], "PASS"),
    (["PASS", "HEALED"], "PASS"),
    (["PASS", "SKIP"], "SKIP"),            # a step that never ran is not a pass
    (["SKIP", "SKIP"], "SKIP"),            # nothing ran at all — was PASS before the fix
    (["PASS", "ERROR"], "ERROR"),          # a crashed step is not a pass
    (["ERROR", "SKIP"], "ERROR"),
    (["SKIP", "FAIL"], "FAIL"),            # a real failure still wins
])
def test_case_verdict_needs_every_step_to_pass(statuses, expected):
    assert _run(_executor(statuses), len(statuses))["status"] == expected


def test_batch_counts_a_skipped_case_as_skipped_not_passed():
    ex = _executor(["SKIP", "SKIP"])
    batch = asyncio.run(ex.execute_batch([_case(2)], _Runner()))
    assert batch["passed"] == 0 and batch["skipped"] == 1


class _RecordingReporter:
    """Captures the dry_run the executor passes to BugReporter."""
    seen = []

    def __init__(self, dry_run=None):
        _RecordingReporter.seen.append(dry_run)

    def create_bug(self, **kwargs):
        """Return a mock filed bug."""
        return {"key": "DRY-1"}


@pytest.mark.parametrize("executor_dry_run", [True, False])
def test_auto_bug_report_follows_the_callers_dry_run_not_just_env(monkeypatch, executor_dry_run):
    import core.bug_reporter as br
    monkeypatch.setenv("AUTO_BUG_REPORT", "true")
    monkeypatch.setenv("DRY_RUN", "false")            # .env says live …
    monkeypatch.setattr(br, "BugReporter", _RecordingReporter)
    _RecordingReporter.seen = []
    _run(_executor(["FAIL"], dry_run=executor_dry_run), 1)
    assert _RecordingReporter.seen == [executor_dry_run]   # … but the caller decides


def test_executor_defaults_to_drafting_bugs(monkeypatch):
    monkeypatch.setattr(te, "_EXECUTION_AVAILABLE", False)   # skip ActionMapper/healer setup
    assert te.TestExecutor().dry_run is True


def _jira_env(monkeypatch, dry_run_env):
    """Fake Jira credentials (never contacted) and the given DRY_RUN env value."""
    for k, v in {"JIRA_BASE_URL": "https://jira.invalid", "JIRA_EMAIL": "qa@invalid",
                 "JIRA_API_TOKEN": "test-token", "JIRA_PROJECT_KEY": "QA"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DRY_RUN", dry_run_env)


def test_bug_reporter_explicit_dry_run_beats_a_live_env(monkeypatch):
    from core.bug_reporter import BugReporter
    _jira_env(monkeypatch, "false")
    assert BugReporter(dry_run=True).dry_run is True
    assert BugReporter(dry_run=False).dry_run is False
    assert BugReporter().dry_run is False                 # legacy: env only


def test_bug_reporter_env_dry_run_cannot_be_overridden_to_live(monkeypatch):
    from core.bug_reporter import BugReporter
    _jira_env(monkeypatch, "true")
    assert BugReporter(dry_run=False).dry_run is True


def test_pipeline_passes_its_dry_run_to_the_ui_executor(monkeypatch):
    import execution.playwright_runner as pr
    from core.pipeline import QAXPipeline

    seen = []

    class _Exec:
        def __init__(self, dry_run=True):
            seen.append(dry_run)

        async def execute_batch(self, cases, runner, stop_on_fail=False):
            return {"total": 0, "passed": 0, "failed": 0, "results": []}

    class _PR:
        async def setup(self, headless=True):
            return None

        async def teardown(self):
            return None

    monkeypatch.setattr(te, "TestExecutor", _Exec)
    monkeypatch.setattr(pr, "PlaywrightRunner", _PR)
    QAXPipeline(dry_run=True, exec_mode="ui")._run_ui_execution([])
    QAXPipeline(dry_run=False, exec_mode="ui")._run_ui_execution([])
    assert seen == [True, False]


def test_a_browser_that_dies_mid_batch_is_relaunched_or_the_rest_is_error_never_fail():
    """Engineering audit 2026-10-20 (P1): after a crash every remaining case FAILED — and FAILs
    can be filed as Jira bugs. Relaunch the browser; if that fails, the rest is ERROR."""
    from execution.action_mapper import _is_browser_gone
    assert _is_browser_gone(RuntimeError("Target page, context or browser has been closed"))
    assert not _is_browser_gone(RuntimeError("Timeout 15000ms exceeded"))

    class _Dead(_Runner):
        async def new_session(self):
            return False

        async def ensure_alive(self):
            return False
    ex = _executor(["PASS"] * 10)
    cases = [dict(_case(1), test_case_id=f"TC-{i}") for i in range(3)]
    batch = asyncio.run(ex.execute_batch(cases, _Dead()))
    assert [r["status"] for r in batch["results"]] == ["PASS", "ERROR", "ERROR"]
    assert batch["failed"] == 0 and "could not be restarted" in batch["results"][1]["error"]

    class _Revived(_Dead):
        async def ensure_alive(self):
            return True
    batch = asyncio.run(_executor(["PASS"] * 10).execute_batch(cases, _Revived()))
    assert [r["status"] for r in batch["results"]] == ["PASS", "PASS", "PASS"]
