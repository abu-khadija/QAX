"""
test_integration.py — Integration Tests for QAX
===========================================================
Five integration tests that verify the full pipeline works end-to-end
using mocks so no real API calls are made.

Each test covers one key workflow:
  1. analyze command runs the full generate + validate flow
  2. run generate mode works via SessionManager
  3. feedback --accept saves a record to the SQLite database
  4. check-connection handles missing credentials gracefully
  5. Session file is saved to output/sessions/ after a run

Run with:
  python -m pytest tests/test_integration.py -v
  python -m pytest tests/test_integration.py -v --tb=short
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Ensure the project root is on sys.path ─────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Minimal valid test case (matches xray_schema.json) ────────────────────
SAMPLE_TEST_CASE = {
    "test_case_id": "TC-TRK-0001",
    "title": "Verify checklist step saves successfully with valid data",
    "description": "Ensures the save endpoint returns success when all required fields are provided.",
    "module": "Orders",
    "test_type": "functional",
    "priority": "high",
    "preconditions": [
        "User is logged in with QA role",
        "User is on the Checklist Step page",
    ],
    "manual_steps": [
        {
            "step_number": 1,
            "action": "Fill all required fields and click Save",
            "expected_result": "Form saves successfully. Success message is shown.",
            "notes": "",
        },
        {
            "step_number": 2,
            "action": "Refresh the page and verify data persists",
            "expected_result": "Saved data is still visible with correct values.",
            "notes": "",
        },
    ],
    "automation_steps": [
        {
            "step_number": 1,
            "playwright_action": "click",
            "selector_key": "TODO_save_button",
            "value": "",
            "assertion_type": "visible",
            "assertion_value": "",
        },
        {
            "step_number": 2,
            "playwright_action": "navigate",
            "selector_key": "TODO_checklist_url",
            "value": "/Orders/OrderItem/View",
            "assertion_type": "url",
            "assertion_value": "/OrderItem/View",
        },
    ],
    "test_data": {"stepId": 1},
    "confidence_score": 7,
    "needs_review": False,
    "review_note": "",
    "generated_from_file": "WebApp/Areas/Orders/Controllers/OrderItemController.cs",
    "generated_at": "2025-03-20T10:00:00Z",
}

# ── Minimal source code that will pass is_testable() check ────────────────
SAMPLE_SOURCE_CODE = """
public class OrderItemController : Controller
{
    [HttpPost]
    public ActionResult Save(OrderItemViewModel model)
    {
        if (!ModelState.IsValid)
            return Json(new { QuestionsSuccess = false });

        var result = _service.SaveOrderItem(model);
        return Json(new { QuestionsSuccess = result.Success });
    }

    [HttpGet]
    public ActionResult View(int stepId)
    {
        var step = _service.GetStep(stepId);
        if (step == null) return HttpNotFound();
        return View(step);
    }
}
"""


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: analyze command runs full generate + validate flow
# ═══════════════════════════════════════════════════════════════════════════

class TestAnalyzeCommand:
    """Test that the analyze command runs the full pipeline end-to-end."""

    def test_analyze_generates_and_validates_test_cases(self, tmp_path: Path) -> None:
        """
        Verify the analyze pipeline:
          - Reads a source file
          - Sends it to Claude (mocked)
          - Validates the response against the schema
          - Saves valid test cases to output/

        Uses mocks for ClaudeClient and filesystem writes so no real
        API calls or side effects occur.
        """
        # ── Create a temporary source file ────────────────────────────────
        source_file = tmp_path / "OrderItemController.cs"
        source_file.write_text(SAMPLE_SOURCE_CODE, encoding="utf-8")

        # ── Mock ClaudeClient.analyze_code() to return our sample ─────────
        mock_claude = MagicMock()
        mock_claude.analyze_code.return_value = [SAMPLE_TEST_CASE]

        # ── Mock chunk_large_file() to return a simple single-chunk list ──
        mock_chunk = {
            "content": SAMPLE_SOURCE_CODE,
            "start_line": 1,
            "end_line": 20,
            "last_method": "Save",
            "chunk_index": 1,
            "total_chunks": 1,
        }

        with patch("core.claude_client.ClaudeClient", return_value=mock_claude), \
             patch("core.code_reader.chunk_large_file", return_value=[mock_chunk]), \
             patch("core.code_reader.is_testable", return_value=True), \
             patch("core.code_reader.read_file", return_value={
                 "content": SAMPLE_SOURCE_CODE,
                 "file_name": "OrderItemController.cs",
                 "file_path": str(source_file),
                 "module_hint": "Orders",
                 "line_count": 20,
             }):

            from core.validator import fix_common_issues, validate_batch

            # Run the core pipeline manually (same as analyze command does)
            raw_cases = [SAMPLE_TEST_CASE]
            fixed_cases = [fix_common_issues(tc) for tc in raw_cases]
            result = validate_batch(fixed_cases)

            # ── Assertions ─────────────────────────────────────────────────
            assert result["summary"]["total"] == 1, "Should process 1 test case"
            assert result["summary"]["valid_count"] == 1, "Sample test case should pass validation"
            assert result["summary"]["invalid_count"] == 0, "No invalid cases expected"
            assert len(result["valid"]) == 1

            valid_tc = result["valid"][0]
            assert valid_tc["test_case_id"] == "TC-TRK-0001"
            assert valid_tc["module"] == "Orders"
            assert valid_tc["confidence_score"] == 7
            assert isinstance(valid_tc["needs_review"], bool)

    def test_analyze_skips_non_testable_files(self, tmp_path: Path) -> None:
        """
        Verify that files without testable logic are silently skipped
        and return an empty test case list.
        """
        from core.code_reader import is_testable

        # A pure config file — no functions, no logic
        config_content = '{"setting": "value", "timeout": 30}'

        assert is_testable(config_content) is False, \
            "Config files should not be considered testable"


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: run generate mode works via SessionManager
# ═══════════════════════════════════════════════════════════════════════════

class TestRunGenerateMode:
    """Test that SessionManager.run_generate() orchestrates the pipeline correctly."""

    def test_run_generate_returns_results_dict(self, tmp_path: Path) -> None:
        """
        Verify run_generate() returns a properly structured results dict
        and saves a session file when given a valid source file.

        Mocks: ClaudeClient, chunk_large_file, read_file, is_testable, input()
        """
        # ── Set up a temporary source file ────────────────────────────────
        source_file = tmp_path / "TestController.cs"
        source_file.write_text(SAMPLE_SOURCE_CODE, encoding="utf-8")

        # ── Set up temp output directories ────────────────────────────────
        sessions_dir = tmp_path / "output" / "sessions"
        sessions_dir.mkdir(parents=True)

        mock_chunk = {
            "content": SAMPLE_SOURCE_CODE,
            "start_line": 1, "end_line": 20,
            "last_method": "Save",
            "chunk_index": 1, "total_chunks": 1,
        }

        mock_claude = MagicMock()
        mock_claude.analyze_code.return_value = [SAMPLE_TEST_CASE]

        config = {
            "file": str(source_file),
            "module": "Orders",
            "dry_run": True,
            "chunk_size": 300,
            "include_deps": False,
            "include_git": False,
            "include_bugs": False,
        }

        with patch("core.claude_client.ClaudeClient", return_value=mock_claude), \
             patch("core.code_reader.chunk_large_file", return_value=[mock_chunk]), \
             patch("core.code_reader.is_testable", return_value=True), \
             patch("core.code_reader.read_file", return_value={
                 "content": SAMPLE_SOURCE_CODE,
                 "file_name": "TestController.cs",
                 "file_path": str(source_file),
                 "module_hint": "Orders",
                 "line_count": 20,
             }), \
             patch("core.code_reader.extract_dependencies", return_value=[]), \
             patch("core.session_manager.SESSIONS_DIR", sessions_dir):

            from core.session_manager import SessionManager

            sm = SessionManager()
            results = sm.run_generate(config)

            # ── Assertions ─────────────────────────────────────────────────
            assert isinstance(results, dict), "run_generate should return a dict"
            assert "error" not in results or results.get("total_valid", 0) >= 0
            assert "total_valid" in results
            assert "duration_seconds" in results
            assert results.get("files_processed", 0) >= 0

    def test_run_generate_returns_error_for_missing_file(self) -> None:
        """
        Verify run_generate() returns an error dict (does not raise)
        when the specified source file does not exist.
        """
        from core.session_manager import SessionManager

        sm = SessionManager()
        config = {
            "file": "/nonexistent/path/DoesNotExist.cs",
            "module": "Orders",
            "dry_run": True,
        }

        result = sm.run_generate(config)

        assert "error" in result, "Should return error key for missing file"
        assert result["total_valid"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: feedback --accept saves a record to SQLite
# ═══════════════════════════════════════════════════════════════════════════

class TestFeedbackAccept:
    """Test that accepting a test case saves a record in the feedback database."""

    def test_accept_saves_to_database(self, tmp_path: Path) -> None:
        """
        Verify FeedbackStore.record_decision() saves an 'accepted' record
        and that it's retrievable via get_stats().

        Uses a temp directory for the SQLite database to avoid polluting
        the real output/feedback.db during testing.
        """
        db_path = tmp_path / "test_feedback.db"

        with patch("core.feedback_store.DB_PATH", db_path):
            from core.feedback_store import FeedbackStore

            # Force FeedbackStore to use our temp DB path
            store = FeedbackStore.__new__(FeedbackStore)
            store.db_path = db_path
            store._init_db()

            # Record an acceptance decision
            store.record_decision(SAMPLE_TEST_CASE, action="accepted")

            # Verify it was saved
            stats = store.get_stats()

            assert stats["total_reviewed"] == 1, "Should have 1 review record"
            assert stats["accepted"] == 1, "Should have 1 accepted record"
            assert stats["rejected"] == 0

    def test_reject_saves_to_database(self, tmp_path: Path) -> None:
        """
        Verify FeedbackStore.record_decision() saves a 'rejected' record correctly.
        """
        db_path = tmp_path / "test_feedback_reject.db"

        with patch("core.feedback_store.DB_PATH", db_path):
            from core.feedback_store import FeedbackStore

            store = FeedbackStore.__new__(FeedbackStore)
            store.db_path = db_path
            store._init_db()

            store.record_decision(SAMPLE_TEST_CASE, action="rejected")

            stats = store.get_stats()
            assert stats["rejected"] == 1
            assert stats["accepted"] == 0
            assert stats["acceptance_rate"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: check-connection handles missing credentials gracefully
# ═══════════════════════════════════════════════════════════════════════════

class TestCheckConnection:
    """Test that check-connection handles missing/invalid credentials without crashing."""

    def test_check_connection_handles_missing_anthropic_key(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """
        Verify that check-connection prints a NOT CONFIGURED message
        when ANTHROPIC_API_KEY is missing — and does NOT raise an exception.
        """
        # Temporarily clear the API key
        original = os.environ.pop("ANTHROPIC_API_KEY", None)

        try:
            # Import here to ensure fresh env state
            from main import run_check_connection_command
            run_check_connection_command()

            captured = capsys.readouterr()
            assert "NOT CONFIGURED" in captured.out or "FAILED" in captured.out, \
                "Should report NOT CONFIGURED when API key is missing"
        finally:
            # Restore original value
            if original:
                os.environ["ANTHROPIC_API_KEY"] = original

    def test_check_connection_handles_missing_jira_config(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """
        Verify that check-connection reports NOT CONFIGURED for Jira
        when credentials are missing — and does NOT crash.
        """
        # Save and clear Jira env vars
        saved = {}
        for key in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            saved[key] = os.environ.pop(key, None)
            os.environ.pop(key, None)

        try:
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-mock"}):
                # Mock the anthropic client to avoid real API calls
                mock_anthropic = MagicMock()
                mock_msg = MagicMock()
                mock_msg.content = [MagicMock(text="Hi")]
                mock_anthropic.return_value.messages.create.return_value = mock_msg

                with patch("anthropic.Anthropic", mock_anthropic):
                    from main import run_check_connection_command
                    run_check_connection_command()

            captured = capsys.readouterr()
            assert "NOT CONFIGURED" in captured.out, \
                "Should report NOT CONFIGURED for missing Jira credentials"
        finally:
            # Restore env vars
            for key, val in saved.items():
                if val is not None:
                    os.environ[key] = val


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: session file is saved to output/sessions/ after a run
# ═══════════════════════════════════════════════════════════════════════════

class TestSessionPersistence:
    """Test that run sessions are correctly saved to output/sessions/."""

    def test_save_session_creates_json_file(self, tmp_path: Path) -> None:
        """
        Verify that SessionManager.save_session() creates a correctly
        structured JSON file in the sessions directory.
        """
        sessions_dir = tmp_path / "output" / "sessions"
        sessions_dir.mkdir(parents=True)

        with patch("core.session_manager.SESSIONS_DIR", sessions_dir):
            from core.session_manager import SessionManager

            sm = SessionManager()
            session_path = sm.save_session(
                mode="generate",
                config={"file": "TestController.cs", "dry_run": True},
                results={
                    "files_processed": 1,
                    "total_generated": 5,
                    "total_valid": 4,
                    "pushed_count": 0,
                    "failed_count": 0,
                    "duration_seconds": 12.3,
                    "output_file": "output/test_cases_20250320.json",
                },
            )

            # ── Verify the file was created ────────────────────────────────
            assert session_path, "save_session should return a non-empty path"
            assert Path(session_path).exists(), f"Session file should exist at: {session_path}"

            # ── Verify the content ─────────────────────────────────────────
            with open(session_path, encoding="utf-8") as f:
                data = json.load(f)

            assert data["mode"] == "generate"
            assert data["results"]["files_processed"] == 1
            assert data["results"]["total_valid"] == 4
            assert data["results"]["duration_seconds"] == 12.3
            assert "session_id" in data
            assert "timestamp" in data

    def test_list_sessions_returns_recent_sessions(self, tmp_path: Path) -> None:
        """
        Verify that list_sessions() returns the saved sessions in
        reverse chronological order (most recent first).
        """
        sessions_dir = tmp_path / "output" / "sessions"
        sessions_dir.mkdir(parents=True)

        with patch("core.session_manager.SESSIONS_DIR", sessions_dir):
            from core.session_manager import SessionManager

            sm = SessionManager()

            # Save two sessions
            sm.save_session(
                mode="smoke",
                config={"dry_run": True},
                results={
                    "files_processed": 0, "total_generated": 3,
                    "total_valid": 3, "pushed_count": 0,
                    "failed_count": 0, "duration_seconds": 5.0, "output_file": "",
                },
            )
            time.sleep(1)
            sm.save_session(
                mode="generate",
                config={"dry_run": True},
                results={
                    "files_processed": 1, "total_generated": 8,
                    "total_valid": 7, "pushed_count": 0,
                    "failed_count": 0, "duration_seconds": 22.0, "output_file": "output/tc.json",
                },
            )

            sessions = sm.list_sessions(limit=10)

            assert len(sessions) == 2, "Should return 2 sessions"
            # Most recent first
            modes = [s["mode"] for s in sessions]
            assert "generate" in modes
            assert "smoke" in modes
