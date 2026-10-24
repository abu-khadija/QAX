"""
test_validator.py — Unit Tests for the Validator Module
=========================================================
Tests the core validation logic to ensure the schema enforcement,
auto-fix utilities, and quality reporting work correctly.

Run with:
    pytest tests/test_validator.py -v
"""

import pytest
from core.validator import (
    fix_common_issues,
    validate_batch,
    validate_test_case,
)


# ─────────────────────────────────────────────────────────────────────────────
# Test Fixtures — Reusable Test Case Templates
# ─────────────────────────────────────────────────────────────────────────────

def make_valid_test_case(**overrides) -> dict:
    """
    Factory function that builds a fully valid test case dictionary.
    Use keyword arguments to override specific fields for negative tests.

    Args:
        **overrides: Any field to override in the base test case.

    Returns:
        dict: A test case dictionary that should pass schema validation.
    """
    base = {
        "test_case_id": "TC-INC-0001",
        "title": "Verify order cannot be submitted without mandatory fields",
        "description": "Ensure the system prevents form submission when required fields are empty.",
        "module": "Orders",
        "test_type": "functional",
        "priority": "high",
        "preconditions": [
            "User is logged in with Reporter role",
            "User is on the New Order form page"
        ],
        "manual_steps": [
            {
                "step_number": 1,
                "action": "Leave the Order Title field empty and click Submit",
                "expected_result": "Form does not submit. Validation error appears.",
                "notes": ""
            },
            {
                "step_number": 2,
                "action": "Fill all mandatory fields with valid data and click Submit",
                "expected_result": "Order is created. Status is Draft.",
                "notes": ""
            }
        ],
        "automation_steps": [
            {
                "step_number": 1,
                "playwright_action": "click",
                "selector_key": "TODO_submit_button",
                "value": "",
                "assertion_type": "visible",
                "assertion_value": ""
            },
            {
                "step_number": 2,
                "playwright_action": "fill",
                "selector_key": "TODO_title_field",
                "value": "Chemical spill in lab B",
                "assertion_type": "visible",
                "assertion_value": ""
            }
        ],
        "test_data": {
            "order_title": "Chemical spill in lab B",
            "severity": "High"
        },
        "confidence_score": 8,
        "needs_review": False,
        "review_note": "",
        "generated_from_file": "test_file.cs",
        "generated_at": "2025-01-15T10:30:00Z"
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Valid Test Case Passes Validation
# ─────────────────────────────────────────────────────────────────────────────

class TestValidTestCase:
    """Tests that a correctly structured test case passes validation."""

    def test_valid_test_case_passes(self):
        """
        A fully valid test case with all required fields should pass validation.
        This is the baseline "happy path" for the validator.
        """
        tc = make_valid_test_case()
        result = validate_test_case(tc)
        assert result is True, "A fully valid test case should return True from validate_test_case"

    def test_valid_batch_with_single_case(self):
        """A list with one valid test case should have valid_count=1 and invalid_count=0."""
        tc = make_valid_test_case()
        result = validate_batch([tc])

        assert result["summary"]["total"] == 1
        assert result["summary"]["valid_count"] == 1
        assert result["summary"]["invalid_count"] == 0
        assert len(result["valid"]) == 1
        assert len(result["invalid"]) == 0

    def test_valid_batch_avg_confidence(self):
        """Average confidence score should be computed correctly across valid cases."""
        tc1 = make_valid_test_case(test_case_id="TC-INC-0001", confidence_score=8)
        tc2 = make_valid_test_case(test_case_id="TC-INC-0002", confidence_score=6)

        result = validate_batch([tc1, tc2])
        assert result["summary"]["avg_confidence"] == 7.0


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Missing Required Field Fails Validation
# ─────────────────────────────────────────────────────────────────────────────

class TestMissingRequiredField:
    """Tests that missing required fields cause validation to fail."""

    def test_missing_title_fails(self):
        """A test case without a 'title' field should fail schema validation."""
        tc = make_valid_test_case()
        del tc["title"]   # Remove required field

        result = validate_test_case(tc)
        assert result is False, "Missing 'title' field should fail validation"

    def test_missing_steps_fails(self):
        """A test case without 'manual_steps' should fail validation."""
        tc = make_valid_test_case()
        del tc["manual_steps"]

        result = validate_test_case(tc)
        assert result is False, "Missing 'manual_steps' field should fail validation"

    def test_missing_module_fails(self):
        """A test case without 'module' should fail validation."""
        tc = make_valid_test_case()
        del tc["module"]

        result = validate_test_case(tc)
        assert result is False, "Missing 'module' field should fail validation"

    def test_arbitrary_module_is_accepted(self):
        """QAX is generic — any non-empty module name validates (no hardcoded enum)."""
        tc = make_valid_test_case(module="Checkout")

        result = validate_test_case(tc)
        assert result is True, "Free-form module name should validate (generic tool)"

    def test_empty_module_fails(self):
        """An empty module still fails validation (minLength: 1)."""
        tc = make_valid_test_case(module="")

        result = validate_test_case(tc)
        assert result is False, "Empty module should fail validation"

    def test_missing_generated_at_fails(self):
        """A test case without 'generated_at' should fail validation."""
        tc = make_valid_test_case()
        del tc["generated_at"]

        result = validate_test_case(tc)
        assert result is False, "Missing 'generated_at' should fail validation"

    def test_batch_captures_invalid_cases(self):
        """validate_batch should place invalid cases in the 'invalid' list with error info."""
        valid_tc = make_valid_test_case(test_case_id="TC-INC-0001")
        invalid_tc = make_valid_test_case(test_case_id="TC-INC-0002")
        del invalid_tc["title"]   # Make it invalid

        result = validate_batch([valid_tc, invalid_tc])

        assert result["summary"]["valid_count"] == 1
        assert result["summary"]["invalid_count"] == 1
        assert len(result["invalid"]) == 1
        # The invalid entry should include the error message
        assert "error" in result["invalid"][0]


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Wrong confidence_score Type Fails Validation
# ─────────────────────────────────────────────────────────────────────────────

class TestConfidenceScoreValidation:
    """Tests that confidence_score enforces the correct integer type and range."""

    def test_confidence_score_as_string_fails(self):
        """
        confidence_score must be an integer, not a string.
        Claude sometimes returns "8" instead of 8 — this should be caught.
        """
        tc = make_valid_test_case(confidence_score="8")   # String, not int

        result = validate_test_case(tc)
        assert result is False, "confidence_score as string should fail validation"

    def test_confidence_score_above_10_fails(self):
        """confidence_score must be between 1 and 10 inclusive."""
        tc = make_valid_test_case(confidence_score=11)

        result = validate_test_case(tc)
        assert result is False, "confidence_score of 11 should fail (max is 10)"

    def test_confidence_score_of_zero_fails(self):
        """confidence_score of 0 should fail (minimum is 1)."""
        tc = make_valid_test_case(confidence_score=0)

        result = validate_test_case(tc)
        assert result is False, "confidence_score of 0 should fail (min is 1)"

    def test_confidence_score_10_passes(self):
        """confidence_score of 10 is valid (maximum allowed value)."""
        tc = make_valid_test_case(confidence_score=10)

        result = validate_test_case(tc)
        assert result is True, "confidence_score of 10 should pass"

    def test_confidence_score_1_passes(self):
        """confidence_score of 1 is valid (minimum allowed value)."""
        tc = make_valid_test_case(confidence_score=1, needs_review=True, review_note="Very uncertain about UI labels.")

        result = validate_test_case(tc)
        assert result is True, "confidence_score of 1 should pass when needs_review is set"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: needs_review=True Without review_note Fails
# ─────────────────────────────────────────────────────────────────────────────

class TestNeedsReviewValidation:
    """Tests the conditional validation: needs_review=True requires a review_note."""

    def test_needs_review_true_without_note_fails(self):
        """
        When needs_review=True, review_note must be present and non-empty.
        This enforces that AI flags are always accompanied by an explanation.
        """
        tc = make_valid_test_case(
            needs_review=True,
            review_note=""   # Empty string should fail when needs_review=True
        )

        result = validate_test_case(tc)
        assert result is False, "needs_review=True with empty review_note should fail"

    def test_needs_review_true_with_note_passes(self):
        """When needs_review=True and review_note is set, validation should pass."""
        tc = make_valid_test_case(
            needs_review=True,
            review_note="Could not confirm the exact button label from source code. Needs UI verification."
        )

        result = validate_test_case(tc)
        assert result is True, "needs_review=True with a valid review_note should pass"

    def test_needs_review_false_without_note_passes(self):
        """When needs_review=False, an empty review_note is acceptable."""
        tc = make_valid_test_case(
            needs_review=False,
            review_note=""   # Empty is fine when needs_review=False
        )

        result = validate_test_case(tc)
        assert result is True, "needs_review=False with empty review_note should pass"

    def test_needs_review_batch_categorization(self):
        """validate_batch should place needs_review=True cases in the needs_review list."""
        tc_normal = make_valid_test_case(test_case_id="TC-INC-0001")
        tc_review = make_valid_test_case(
            test_case_id="TC-INC-0002",
            needs_review=True,
            review_note="UI element name is uncertain — not visible in backend code.",
            confidence_score=5
        )

        result = validate_batch([tc_normal, tc_review])

        assert result["summary"]["needs_review_count"] == 1
        assert len(result["needs_review"]) == 1
        assert result["needs_review"][0]["test_case_id"] == "TC-INC-0002"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: fix_common_issues Fixes Timestamp Correctly
# ─────────────────────────────────────────────────────────────────────────────

class TestFixCommonIssues:
    """Tests that fix_common_issues auto-corrects known issues without changing content."""

    def test_missing_timestamp_gets_fixed(self):
        """
        A test case with a missing generated_at should have it added
        automatically as the current UTC timestamp.
        """
        tc = make_valid_test_case()
        del tc["generated_at"]   # Simulate missing timestamp

        fixed = fix_common_issues(tc)

        # generated_at should now be present
        assert "generated_at" in fixed, "fix_common_issues should add missing generated_at"
        assert fixed["generated_at"] != "", "generated_at should not be empty after fix"

        # Should be a valid ISO 8601 format
        from datetime import datetime
        try:
            datetime.fromisoformat(fixed["generated_at"].replace("Z", "+00:00"))
        except ValueError:
            pytest.fail(f"Fixed generated_at is not valid ISO 8601: {fixed['generated_at']}")

    def test_malformed_timestamp_gets_replaced(self):
        """A malformed timestamp string should be replaced with a valid one."""
        tc = make_valid_test_case(generated_at="not-a-date")

        fixed = fix_common_issues(tc)

        # Should be a valid ISO 8601 after fix
        from datetime import datetime
        try:
            datetime.fromisoformat(fixed["generated_at"].replace("Z", "+00:00"))
        except ValueError:
            pytest.fail(f"Malformed timestamp was not fixed correctly: {fixed['generated_at']}")

    def test_confidence_score_string_gets_converted(self):
        """confidence_score as string '8' should be converted to integer 8."""
        tc = make_valid_test_case(confidence_score="8")

        fixed = fix_common_issues(tc)

        assert isinstance(fixed["confidence_score"], int), "confidence_score should be int after fix"
        assert fixed["confidence_score"] == 8

    def test_needs_review_string_gets_converted_true(self):
        """needs_review as string 'true' should be converted to boolean True."""
        tc = make_valid_test_case(
            needs_review="true",  # String, not bool
            review_note="Something needs checking."
        )

        fixed = fix_common_issues(tc)

        assert isinstance(fixed["needs_review"], bool), "needs_review should be bool after fix"
        assert fixed["needs_review"] is True

    def test_needs_review_string_gets_converted_false(self):
        """needs_review as string 'false' should be converted to boolean False."""
        tc = make_valid_test_case(needs_review="false")

        fixed = fix_common_issues(tc)

        assert isinstance(fixed["needs_review"], bool), "needs_review should be bool after fix"
        assert fixed["needs_review"] is False

    def test_fix_does_not_mutate_original(self):
        """fix_common_issues should return a new dict and not modify the original."""
        tc = make_valid_test_case()
        del tc["generated_at"]
        original_keys = set(tc.keys())

        fixed = fix_common_issues(tc)

        # Original should still be missing generated_at
        assert "generated_at" not in tc, "Original dict should not be mutated"
        # Fixed version should have it
        assert "generated_at" in fixed

    def test_missing_review_note_gets_empty_string(self):
        """A test case missing review_note entirely should have it added as empty string."""
        tc = make_valid_test_case()
        if "review_note" in tc:
            del tc["review_note"]

        fixed = fix_common_issues(tc)

        assert "review_note" in fixed, "fix_common_issues should add missing review_note key"
        assert fixed["review_note"] == ""


# ── API-mode test cases (api_steps) validate without the Playwright UI step pair ──

def _api_base():
    return {"title": "Login returns a token",
            "description": "Register then login and fetch the current user",
            "module": "auth", "test_type": "api", "priority": "high",
            "preconditions": ["A clean database"], "confidence_score": 7,
            "needs_review": False, "generated_from_file": "endpoints://auth",
            "generated_at": "2026-10-13"}


def test_api_case_with_api_steps_is_valid_without_ui_steps():
    from core.validator import validate_batch
    tc = {**_api_base(), "test_case_id": "TC-API-0001",
          "manual_steps": [], "automation_steps": [],
          "api_steps": [{"step_number": 1, "method": "POST", "path": "/auth/login"},
                        {"step_number": 2, "method": "GET", "path": "/me"}]}
    b = validate_batch([tc])
    assert len(b["valid"]) == 1 and not b["invalid"]   # API mode no longer needs UI steps


def test_api_case_can_omit_ui_step_arrays_entirely():
    from core.validator import validate_batch
    tc = {**_api_base(), "test_case_id": "TC-API-0002",
          "api_steps": [{"step_number": 1, "method": "GET", "path": "/me"}]}
    assert len(validate_batch([tc])["valid"]) == 1


def test_ui_case_without_steps_still_rejected():
    from core.validator import validate_batch
    tc = {**_api_base(), "test_case_id": "TC-UI-0009",
          "manual_steps": [], "automation_steps": []}          # no api_steps → UI schema applies
    assert len(validate_batch([tc])["valid"]) == 0             # base (UI) schema untouched


def test_fix_common_issues_defaults_empty_preconditions():
    from core.validator import fix_common_issues
    assert fix_common_issues({"preconditions": []})["preconditions"] == ["No special preconditions."]
    assert fix_common_issues({"preconditions": "a clean db"})["preconditions"] == ["a clean db"]
    assert fix_common_issues({})["preconditions"] == ["No special preconditions."]


def test_an_id_whose_area_is_too_long_is_shortened_not_lost():
    """Found 2026-10-25: gemini named the area after the story (TC-LOGOUT-0001); the schema allows 2-5 letters, so every case
    of the story was refused and the story scored 0 cases, three runs in a row."""
    from core.validator import fix_common_issues
    assert fix_common_issues({"test_case_id": "TC-LOGOUT-0001"})["test_case_id"] == "TC-LOGOU-0001"
    assert fix_common_issues({"test_case_id": "TC-notes-0002"})["test_case_id"] == "TC-notes-0002"      # 5 letters: left alone
    assert fix_common_issues({"test_case_id": "TC-AUTH-0003"})["test_case_id"] == "TC-AUTH-0003"
    assert fix_common_issues({"test_case_id": "nonsense"})["test_case_id"] == "nonsense"                # not ours to fix
