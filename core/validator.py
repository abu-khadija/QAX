"""
validator.py — Test Case Validation Engine
===========================================
Validates AI-generated test cases against the Xray JSON schema before
any data is pushed to Jira. Catches format errors, missing fields, and
flags low-confidence cases for human review.
"""

import json
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import ValidationError

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Path to the schema file (relative to this file's location) ────────────────
from core.paths import config_file  # noqa: E402 — repo config/ or the installed qax_config copy
SCHEMA_PATH = config_file("xray_schema.json")

# ── Allowed Playwright action types (mirrors xray_schema.json enum) ───────────
VALID_PLAYWRIGHT_ACTIONS = {"click", "fill", "navigate", "assert", "select", "upload", "wait", "hover"}


# ─────────────────────────────────────────────────────────────────────────────
# Schema Loading
# ─────────────────────────────────────────────────────────────────────────────

def load_schema() -> dict:
    """
    Load the Xray JSON schema from config/xray_schema.json.

    Returns:
        dict: The parsed JSON schema used for validation.

    Raises:
        FileNotFoundError: If xray_schema.json does not exist at the expected path.
        json.JSONDecodeError: If the schema file contains invalid JSON.
    """
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Schema file not found at: {SCHEMA_PATH}\n"
            "Make sure config/xray_schema.json exists."
        )

    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)

    logger.debug("Schema loaded from %s", SCHEMA_PATH)
    return schema


_API_SCHEMA_CACHE: dict | None = None


def _is_api_case(tc: dict) -> bool:
    """True when a test case is an API-mode case (drives HTTP api_steps)."""
    steps = tc.get("api_steps")
    return isinstance(steps, list) and len(steps) > 0


def api_schema() -> dict:
    """
    The schema for API-mode test cases, derived from the base schema.

    API test cases carry `api_steps` (HTTP) instead of the Playwright
    `manual_steps`/`automation_steps` pair the base schema mandates. Without this,
    every generated API test fails validation ("[] should be non-empty") and the
    functional-generation leg silently yields zero cases. This copy requires a
    non-empty `api_steps`, drops the two UI-step arrays from `required`, and relaxes
    their minItems so an API case may omit or leave them empty. The base (UI) schema
    is untouched.
    """
    global _API_SCHEMA_CACHE
    if _API_SCHEMA_CACHE is None:
        import copy
        s = copy.deepcopy(load_schema())
        items = s.get("items", {})
        req = [r for r in items.get("required", [])
               if r not in ("manual_steps", "automation_steps")]
        if "api_steps" not in req:
            req.append("api_steps")
        items["required"] = req
        props = items.get("properties", {})
        for f in ("manual_steps", "automation_steps"):
            if f in props:
                props[f]["minItems"] = 0
        if "api_steps" in props:
            props["api_steps"]["minItems"] = 1
        _API_SCHEMA_CACHE = s
    return _API_SCHEMA_CACHE


# ─────────────────────────────────────────────────────────────────────────────
# Single Test Case Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_test_case(tc: dict) -> bool:
    """
    Validate a single test case dictionary against the Xray schema.

    Note: This validates the test case as a single-item array because
    the schema's root element is an array of test cases.

    Args:
        tc (dict): A single test case dictionary to validate.

    Returns:
        bool: True if valid, False if validation fails.
    """
    try:
        schema = load_schema()
        # Wrap in array because the schema validates an array of test cases
        jsonschema.validate(instance=[tc], schema=schema)
        return True
    except ValidationError as e:
        # Log the specific validation error for debugging
        logger.debug("Validation failed for test case '%s': %s", tc.get("test_case_id", "UNKNOWN"), e.message)
        return False
    except Exception as e:
        logger.error("Unexpected error during validation: %s", str(e))
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Auto-Accept Logic
# ─────────────────────────────────────────────────────────────────────────────

def should_auto_accept(test_case: dict, feedback_store=None) -> bool:
    """
    Determine whether a test case qualifies for automatic acceptance.

    A test case is auto-accepted only when ALL of the following are true:
      1. confidence_score >= 8.5  (AI is very confident)
      2. needs_review is False    (AI did not flag it for review)
      3. If a feedback_store is provided: the module has >= 10 accepted cases
         AND an acceptance_rate of >= 80% for that module.

    Never auto-accepts when needs_review is True, regardless of score.

    Args:
        test_case (dict): A single test case dictionary.
        feedback_store: Optional FeedbackStore instance for module-level
                        acceptance rate gating. Pass None to skip module check.

    Returns:
        bool: True if the test case should be auto-accepted, False otherwise.
    """
    # Guard: never auto-accept if AI flagged for review
    if test_case.get("needs_review") is True:
        return False

    score = test_case.get("confidence_score", 0)
    if not isinstance(score, (int, float)):
        return False
    if score < 8.5:
        return False

    # Optional module-level gate via feedback history
    if feedback_store is not None:
        try:
            module = test_case.get("module", "")
            stats = feedback_store.get_stats()
            module_data = stats.get("by_module", {}).get(module, {})
            module_count = module_data.get("count", 0)
            module_rate = module_data.get("rate", 0.0)
            if module_count < 10 or module_rate < 80.0:
                return False
        except Exception:
            # feedback_store unavailable — skip the module gate
            pass

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Batch Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_batch(test_cases: list[dict],
                   auto_accept: bool = False,
                   feedback_store=None) -> dict:
    """
    Validate a list of test cases and categorize them into valid, invalid,
    and needs_review buckets.

    Args:
        test_cases (list[dict]): List of test case dictionaries from Claude's output.
        auto_accept (bool): If True, flag high-confidence cases as auto_accepted=True.
        feedback_store: Optional FeedbackStore for module-level gating of auto-accept.

    Returns:
        dict: A results dictionary with four keys:
            - "valid":         List of test cases that passed schema validation
            - "invalid":       List of dicts with "test_case" and "error" keys
            - "needs_review":  List of valid test cases where needs_review=True
            - "summary":       Stats dict with counts and average confidence score
    """
    valid = []
    invalid = []
    needs_review = []

    schema = load_schema()

    # ── Track IDs seen in this batch for duplicate detection ──────────────────
    seen_ids: dict[str, int] = {}   # tc_id → count of times seen

    for tc in test_cases:
        # ── Try to validate against schema ────────────────────────────────────
        # API-mode cases (carrying api_steps) are validated against the API schema,
        # which requires api_steps and does not mandate the Playwright UI step pair.
        try:
            jsonschema.validate(instance=[tc],
                                schema=api_schema() if _is_api_case(tc) else schema)

            # ── Deduplicate IDs within this batch ─────────────────────────────
            tc_id = tc.get("test_case_id", "")
            if tc_id and tc_id in seen_ids:
                seen_ids[tc_id] += 1
                suffix = chr(ord("A") + seen_ids[tc_id])   # B, C, D...
                new_id = f"{tc_id}_{suffix}"
                logger.warning(
                    "Duplicate test_case_id '%s' in batch — renamed to '%s'",
                    tc_id, new_id
                )
                tc = dict(tc)   # avoid mutating original
                tc["test_case_id"] = new_id
            elif tc_id:
                seen_ids[tc_id] = 0

            # ── Auto-accept tagging (A3) ──────────────────────────────────────
            if auto_accept and should_auto_accept(tc, feedback_store):
                tc = dict(tc)
                tc["auto_accepted"] = True
            else:
                tc["auto_accepted"] = False

            valid.append(tc)

            # ── Flag for human review if AI set needs_review=True ─────────────
            if tc.get("needs_review") is True:
                needs_review.append(tc)

        except ValidationError as e:
            # Capture the error message alongside the failing test case
            invalid.append({
                "test_case": tc,
                "error": e.message,
                "field_path": " -> ".join(str(p) for p in e.absolute_path) or "root"
            })

    # ── Calculate average confidence score across all valid test cases ─────────
    confidence_scores = [
        tc.get("confidence_score", 0)
        for tc in valid
        if isinstance(tc.get("confidence_score"), int)
    ]
    avg_confidence = (
        round(sum(confidence_scores) / len(confidence_scores), 2)
        if confidence_scores else 0.0
    )

    return {
        "valid": valid,
        "invalid": invalid,
        "needs_review": needs_review,
        "summary": {
            "total": len(test_cases),
            "valid_count": len(valid),
            "invalid_count": len(invalid),
            "needs_review_count": len(needs_review),
            "avg_confidence": avg_confidence
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Auto-Fix Common Issues
# ─────────────────────────────────────────────────────────────────────────────

def fix_common_issues(tc: dict) -> dict:
    """
    Attempt to auto-fix small, predictable issues in a test case before
    re-running validation. Does NOT alter substantive content.

    Fixes applied:
      - Missing or invalid 'generated_at' → set to current UTC timestamp
      - 'confidence_score' as string → convert to int
      - 'needs_review' as string → convert to bool
      - Missing 'review_note' → add empty string (schema allows "" when needs_review=False)
      - 'test_data' as string → convert to object with raw_value key
      - Leading/trailing whitespace stripped from title, description
      - Empty 'assertion_type' in automation_steps → replaced with 'visible'
      - 'test_case_id' whose area is 6+ letters (TC-LOGOUT-0001) → TC-LOGOU-0001: the id's shape alone never costs a case

    Args:
        tc (dict): The test case dictionary to fix.

    Returns:
        dict: A new test case dictionary with fixes applied (original is not mutated).
    """
    # Work on a copy to avoid mutating the original
    fixed = dict(tc)

    # ── Fix 0: an id whose area is longer than the schema's 2-5 letters (the AI named the area after the story) ──
    m = re.fullmatch(r"TC-([A-Za-z]{6,})-(\d{4})", str(fixed.get("test_case_id") or ""))
    if m:
        fixed["test_case_id"] = f"TC-{m.group(1)[:5].upper()}-{m.group(2)}"
        logger.debug("Shortened test_case_id %s to %s", tc.get("test_case_id"), fixed["test_case_id"])

    # ── Fix 1: Missing or malformed generated_at ──────────────────────────────
    current_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not fixed.get("generated_at") or not isinstance(fixed.get("generated_at"), str):
        logger.debug("Fixing missing generated_at for %s", fixed.get("test_case_id"))
        fixed["generated_at"] = current_timestamp
    else:
        # Validate timestamp format — if invalid, replace it
        try:
            datetime.fromisoformat(fixed["generated_at"].replace("Z", "+00:00"))
        except ValueError:
            logger.debug("Fixing malformed generated_at for %s", fixed.get("test_case_id"))
            fixed["generated_at"] = current_timestamp

    # ── Fix 2: confidence_score as string → integer ───────────────────────────
    if isinstance(fixed.get("confidence_score"), str):
        try:
            fixed["confidence_score"] = int(fixed["confidence_score"])
            logger.debug("Fixed confidence_score type for %s", fixed.get("test_case_id"))
        except ValueError:
            # Can't convert — leave it for the validator to flag
            pass

    # ── Fix 3: needs_review as string → boolean ───────────────────────────────
    if isinstance(fixed.get("needs_review"), str):
        fixed["needs_review"] = fixed["needs_review"].lower() in ("true", "1", "yes")
        logger.debug("Fixed needs_review type for %s", fixed.get("test_case_id"))

    # ── Fix 4: Missing review_note when needs_review is False ─────────────────
    if "review_note" not in fixed:
        fixed["review_note"] = ""

    # ── Fix 5: test_data as a plain string → wrap in object ───────────────────
    if isinstance(fixed.get("test_data"), str):
        logger.debug("Fixing test_data type for %s", fixed.get("test_case_id"))
        fixed["test_data"] = {"raw_value": fixed["test_data"]}

    # ── Fix 6: Strip whitespace from string fields ────────────────────────────
    for field in ("title", "description", "review_note", "generated_from_file"):
        if isinstance(fixed.get(field), str):
            fixed[field] = fixed[field].strip()

    # ── Fix 7b: Empty/missing preconditions → a sensible default ──────────────
    # The schema requires a non-empty preconditions array; the model sometimes
    # emits [] (especially for self-contained API tests that register their own
    # state). Default it rather than discard an otherwise-valid case. A string is
    # wrapped into a single-item list.
    _PRE_DEFAULT = "No special preconditions."   # ≥ the schema's 5-char minimum
    pre = fixed.get("preconditions")
    if isinstance(pre, str):
        fixed["preconditions"] = [pre.strip()] if len(pre.strip()) >= 5 else [_PRE_DEFAULT]
    elif not isinstance(pre, list) or len(pre) == 0:
        fixed["preconditions"] = [_PRE_DEFAULT]

    # ── Fix 7: Empty assertion_type in automation_steps → default to 'visible' ─
    # Claude sometimes generates assertion_type: "" for non-assert steps.
    # Empty string is not in the enum — replace with 'visible' as safe default.
    if isinstance(fixed.get("automation_steps"), list):
        for step in fixed["automation_steps"]:
            if isinstance(step, dict):
                if step.get("assertion_type") == "" or step.get("assertion_type") is None:
                    step["assertion_type"] = "visible"
                    logger.debug(
                        "Fixed empty assertion_type → 'visible' in step %s of %s",
                        step.get("step_number", "?"), fixed.get("test_case_id")
                    )

    return fixed


# ─────────────────────────────────────────────────────────────────────────────
# Quality Report Generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_quality_report(validation_result: dict) -> str:
    """
    Generate a human-readable quality report from a validate_batch() result.
    Prints the report to console and also returns it as a string.

    Args:
        validation_result (dict): The dict returned by validate_batch().

    Returns:
        str: The full report text (also printed to stdout).
    """
    summary = validation_result.get("summary", {})
    invalid_cases = validation_result.get("invalid", [])
    review_cases = validation_result.get("needs_review", [])

    # ── Count auto-accepted cases ─────────────────────────────────────────────
    valid_cases = validation_result.get("valid", [])
    auto_accepted_count = sum(1 for tc in valid_cases if tc.get("auto_accepted") is True)

    # ── Build the report string ───────────────────────────────────────────────
    lines = [
        "",
        "=" * 60,
        "  QAX — VALIDATION QUALITY REPORT",
        "=" * 60,
        f"  Total test cases processed : {summary.get('total', 0)}",
        f"  Valid (schema pass)         : {summary.get('valid_count', 0)}",
        f"  Invalid (schema fail)       : {summary.get('invalid_count', 0)}",
        f"  Flagged for human review    : {summary.get('needs_review_count', 0)}",
        f"  Auto-accepted               : {auto_accepted_count} (confidence >= 8.5)",
        f"  Average confidence score   : {summary.get('avg_confidence', 0.0)} / 10",
        "-" * 60,
    ]

    # ── List invalid cases with their error reasons ───────────────────────────
    if invalid_cases:
        lines.append("  INVALID TEST CASES:")
        for item in invalid_cases:
            tc = item.get("test_case", {})
            tc_id = tc.get("test_case_id", "UNKNOWN")
            error = item.get("error", "Unknown error")
            field = item.get("field_path", "root")
            lines.append(f"    ✗ {tc_id}")
            lines.append(f"        Field   : {field}")
            lines.append(f"        Error   : {error}")
        lines.append("-" * 60)

    # ── List needs_review cases with their review notes ───────────────────────
    if review_cases:
        lines.append("  CASES FLAGGED FOR HUMAN REVIEW:")
        for tc in review_cases:
            tc_id = tc.get("test_case_id", "UNKNOWN")
            note = tc.get("review_note", "(no note provided)")
            score = tc.get("confidence_score", "?")
            lines.append(f"    ⚠  {tc_id}  [confidence: {score}/10]")
            lines.append(f"        Note: {note}")
        lines.append("-" * 60)

    # ── Overall assessment ────────────────────────────────────────────────────
    avg = summary.get("avg_confidence", 0)
    if avg >= 8:
        assessment = "EXCELLENT — High confidence output, ready to push to Xray."
    elif avg >= 6:
        assessment = "GOOD — Review flagged cases before pushing to Xray."
    elif avg >= 4:
        assessment = "FAIR — Multiple cases need review. Consider re-running with more code context."
    else:
        assessment = "POOR — Low confidence. Provide more complete source files for better results."

    lines.append(f"  Assessment: {assessment}")

    # ── Dual-step validation section (if included in result) ──────────────────
    dual = validation_result.get("dual_steps")
    if dual:
        lines.append("-" * 60)
        lines.append("  DUAL-STEP VALIDATION (manual + automation):")
        lines.append(f"    Passed : {dual['summary']['passed_count']}")
        lines.append(f"    Failed : {dual['summary']['failed_count']}")
        lines.append(f"    TODO selectors needing CSS : {dual['summary']['todo_selector_count']}")

        if dual.get("failed"):
            lines.append("")
            lines.append("  DUAL-STEP FAILURES:")
            for item in dual["failed"]:
                lines.append(f"    ✗ {item['test_case_id']}")
                for err in item["errors"]:
                    lines.append(f"        → {err}")

        if dual.get("todo_selectors"):
            lines.append("")
            lines.append("  TODO SELECTORS (fill in selectors.json for your project):")
            for sel in dual["todo_selectors"]:
                lines.append(
                    f"    • {sel['selector_key']}  "
                    f"[{sel['test_case_id']} step {sel['step_number']} — {sel['action']}]"
                )

    lines.append("=" * 60)
    lines.append("")

    report = "\n".join(lines)
    print(report)
    return report
