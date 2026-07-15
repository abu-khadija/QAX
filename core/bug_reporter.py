"""
bug_reporter.py — Professional QA Bug Reporter
===============================================
Creates fully-structured Jira bug tickets from QAX test execution failures.

Follows professional QA bug reporting standards:
  - Every field is a real Jira field (not buried in description text)
  - Description uses Atlassian Document Format (ADF) with 9 standard sections
  - Attachments: screenshot, HTML snapshot, and test log as real file uploads
  - Severity and priority are mapped from QAX risk levels
  - Failed test is linked to its Xray test case via Jira issue link

DRY_RUN guard:
  When DRY_RUN=true, create_bug() logs a full summary and returns a mock
  result dict — no Jira API calls are made.

All methods are wrapped in try/except and never raise — they return
safe defaults (None / False) on any error so the test pipeline continues.
"""

import base64
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


# ── ADF helper functions ───────────────────────────────────────────────────────

def _adf_heading(text: str, level: int = 2) -> dict:
    """Return an ADF heading node."""
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [{"type": "text", "text": text}],
    }


def _adf_paragraph(text: str) -> dict:
    """Return an ADF paragraph node."""
    return {
        "type": "paragraph",
        "content": [{"type": "text", "text": str(text)}],
    }


def _adf_table(rows: list[tuple[str, str]]) -> dict:
    """
    Return an ADF table node from a list of (field, value) pairs.

    The first row becomes the header row (bold Field / Value labels).
    Subsequent rows are data cells.

    Args:
        rows: List of (field_name, value) string tuples.

    Returns:
        dict: ADF table node.
    """
    def _header_cell(text: str) -> dict:
        return {
            "type": "tableHeader",
            "attrs": {},
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": text,
                         "marks": [{"type": "strong"}]},
                    ],
                }
            ],
        }

    def _data_cell(text: str) -> dict:
        return {
            "type": "tableCell",
            "attrs": {},
            "content": [_adf_paragraph(text)],
        }

    header_row = {
        "type": "tableRow",
        "content": [_header_cell("Field"), _header_cell("Value")],
    }
    data_rows = [
        {
            "type": "tableRow",
            "content": [_data_cell(field), _data_cell(str(value))],
        }
        for field, value in rows
    ]
    return {
        "type": "table",
        "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
        "content": [header_row, *data_rows],
    }


def _adf_ordered_list(items: list[str]) -> dict:
    """
    Return an ADF orderedList node from a list of text items.

    Args:
        items: List of string entries for each list item.

    Returns:
        dict: ADF orderedList node.
    """
    return {
        "type": "orderedList",
        "content": [
            {
                "type": "listItem",
                "content": [_adf_paragraph(item)],
            }
            for item in items
        ],
    }


def _adf_code_block(text: str) -> dict:
    """
    Return an ADF codeBlock node (monospace, plain text).

    Args:
        text: The preformatted text content.

    Returns:
        dict: ADF codeBlock node.
    """
    return {
        "type": "codeBlock",
        "attrs": {"language": "plain"},
        "content": [{"type": "text", "text": str(text)}],
    }


def _adf_bullet_list(items: list[str]) -> dict:
    """
    Return an ADF bulletList node from a list of text items.

    Args:
        items: List of string entries.

    Returns:
        dict: ADF bulletList node.
    """
    return {
        "type": "bulletList",
        "content": [
            {
                "type": "listItem",
                "content": [_adf_paragraph(item)],
            }
            for item in items
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# BugReporter
# ══════════════════════════════════════════════════════════════════════════════

class BugReporter:
    """
    Creates professional Jira bug reports from QAX test execution failures.

    Adheres to QA bug reporting standards:
      - Summary, description (ADF), issue type, priority, severity, labels,
        and environment are all set as proper Jira fields.
      - Screenshots, HTML snapshots, and test logs are uploaded as file
        attachments (not embedded in description text).
      - Severity is mapped from QAX risk levels; priority follows suit.

    Usage:
        reporter = BugReporter()
        result = reporter.create_bug(test_case, failure, screenshot_path=...,
                                     risk_level="critical")
    """

    def __init__(self) -> None:
        """
        Initialise the reporter from .env variables.

        Required for real operation:
          JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY

        Optional (with defaults):
          JIRA_SEVERITY_FIELD    — custom field name for severity (default: "severity")
          JIRA_ENVIRONMENT_FIELD — custom field name for environment
                                   (default: "environment")
          DRY_RUN                — "true" → log only, no real API calls
          AUTO_BUG_REPORT        — "false" → disable auto bug creation entirely

        Sets self.enabled = True only when all four required vars are present.
        """
        self._base_url   = os.getenv("JIRA_BASE_URL", "").rstrip("/")
        self._email      = os.getenv("JIRA_EMAIL", "")
        self._token      = os.getenv("JIRA_API_TOKEN", "")
        self._project    = os.getenv("JIRA_PROJECT_KEY", "")
        self._sev_field  = os.getenv("JIRA_SEVERITY_FIELD", "severity")
        self._env_field  = os.getenv("JIRA_ENVIRONMENT_FIELD", "environment")
        self._auto_mode  = os.getenv("AUTO_BUG_REPORT", "true").lower() == "true"

        self.dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
        self.enabled = bool(
            self._base_url and self._email and self._token and self._project
        )

        if self.enabled:
            # Basic-auth header: base64(email:token)
            creds = f"{self._email}:{self._token}"
            encoded = base64.b64encode(creds.encode()).decode()
            self.headers = {
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        else:
            self.headers = {}
            logger.debug(
                "BugReporter: Jira credentials not fully configured — "
                "JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY required."
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Mapping helpers
    # ─────────────────────────────────────────────────────────────────────────

    def map_severity(self, risk_level: str, confidence: float) -> str:
        """
        Map a QAX risk level + confidence score to a Jira severity value.

        Rules (first match wins):
          risk=critical OR confidence < 5  → "Critical"
          risk=high     OR confidence < 7  → "Major"
          risk=medium                      → "Minor"
          risk=low                         → "Trivial"

        Args:
            risk_level (str): QAX risk level ("critical", "high", "medium", "low").
            confidence (float): Confidence score 0–10 from test case generation.

        Returns:
            str: Jira severity value — one of Critical / Major / Minor / Trivial.
        """
        risk = (risk_level or "medium").lower()
        if risk == "critical" or confidence < 5:
            return "Critical"
        if risk == "high" or confidence < 7:
            return "Major"
        if risk == "medium":
            return "Minor"
        return "Trivial"

    def map_priority(self, risk_level: str) -> dict:
        """
        Map a QAX risk level to a Jira priority dict.

        Args:
            risk_level (str): QAX risk level ("critical", "high", "medium", "low").

        Returns:
            dict: Jira priority field value, e.g. {"name": "Highest"}.
        """
        mapping = {
            "critical": {"name": "Highest"},
            "high":     {"name": "High"},
            "medium":   {"name": "Medium"},
            "low":      {"name": "Low"},
        }
        return mapping.get((risk_level or "medium").lower(), {"name": "Medium"})

    # ─────────────────────────────────────────────────────────────────────────
    # ADF description builder
    # ─────────────────────────────────────────────────────────────────────────

    def build_adf_description(
        self,
        test_case: dict,
        failure: dict,
        environment: dict | None = None,
        git_history: dict | None = None,
    ) -> dict:
        """
        Build a full Atlassian Document Format (ADF) description doc.

        Nine sections in QA-standard order:
          1. Summary Box      — test case metadata table
          2. Environment      — OS / browser / URL / branch
          3. Steps to Reproduce — numbered list with failing step marked ⚠
          4. Expected Result  — what should have happened
          5. Actual Result    — what actually happened
          6. Error Details    — code block with error info
          7. Self-Healing     — healing attempt result (or "not attempted")
          8. Git History      — file risk context (or "not available")
          9. Attachments Note — lists the attached files

        Args:
            test_case (dict): Test case dict (title, test_case_id, module, …).
            failure (dict): Failure info dict with step_number, step_title,
                            expected_result, actual_result, error, and optionally
                            heal_attempted, heal_succeeded, healed_selector,
                            selector_used.
            environment (dict | None): Runtime environment info (os, browser,
                                        url, project, git_branch).
            git_history (dict | None): Output from GitAnalyzer.get_file_history().

        Returns:
            dict: ADF document root node ready for the Jira description field.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        steps   = test_case.get("manual_steps", test_case.get("steps", []))
        fail_step_num = failure.get("step_number", 0)
        total_steps   = len(steps)

        content: list[dict] = []

        # ── Section 1: Summary Box ─────────────────────────────────────────
        content.append(_adf_heading("Bug Summary", level=2))
        content.append(_adf_table([
            ("Test Case",    test_case.get("title", "N/A")),
            ("Test Case ID", test_case.get("test_case_id", "N/A")),
            ("Module",       test_case.get("module", "N/A")),
            ("Test Type",    test_case.get("test_type", failure.get("test_type", "N/A"))),
            ("Confidence",   f"{test_case.get('confidence_score', 'N/A')}/10"),
            ("Risk Level",   test_case.get("risk_level", failure.get("risk_level", "N/A"))),
            ("Reported by",  "QAX Auto-Reporter"),
            ("Timestamp",    now_str),
        ]))

        # ── Section 2: Environment ─────────────────────────────────────────
        content.append(_adf_heading("Environment", level=2))
        env = environment or {}
        content.append(_adf_table([
            ("OS",           env.get("os", "N/A")),
            ("Browser",      env.get("browser", "N/A")),
            ("App URL",      env.get("url", os.getenv("APP_BASE_URL", "N/A"))),
            ("Project",      env.get("project", os.getenv("DEFAULT_PROJECT", "N/A"))),
            ("Branch/Build", env.get("git_branch", "N/A")),
        ]))

        # ── Section 3: Steps to Reproduce ─────────────────────────────────
        content.append(_adf_heading("Steps to Reproduce", level=2))
        if steps:
            step_items = []
            for step in steps:
                snum   = step.get("step_number", "?")
                action = step.get("action", step.get("manual_step", str(step)))
                if int(snum) == int(fail_step_num):
                    step_items.append(f"⚠ {snum}. {action}  ← FAILS HERE")
                else:
                    step_items.append(f"{snum}. {action}")
            content.append(_adf_ordered_list(step_items))
        else:
            content.append(_adf_paragraph("No steps recorded."))

        # ── Section 4: Expected Result ─────────────────────────────────────
        content.append(_adf_heading("Expected Result", level=2))
        content.append(_adf_paragraph(
            failure.get("expected_result", "N/A")
        ))

        # ── Section 5: Actual Result ───────────────────────────────────────
        content.append(_adf_heading("Actual Result", level=2))
        content.append(_adf_paragraph(
            failure.get("actual_result", "N/A")
        ))

        # ── Section 6: Error Details ───────────────────────────────────────
        content.append(_adf_heading("Error Details", level=2))
        error_msg  = failure.get("error", "N/A")
        error_type = type(error_msg).__name__ if error_msg != "N/A" else "N/A"
        # Try to extract error type from the message string
        if isinstance(error_msg, str) and ":" in error_msg:
            parts = error_msg.split(":", 1)
            if len(parts[0].split()) <= 3:
                error_type = parts[0].strip()

        error_text = (
            f"Error type:   {error_type}\n"
            f"Message:      {str(error_msg)[:500]}\n"
            f"Selector:     {failure.get('selector_used', 'N/A')}\n"
            f"Step number:  {fail_step_num} of {total_steps}"
        )
        content.append(_adf_code_block(error_text))

        # ── Section 7: Self-Healing Result ────────────────────────────────
        content.append(_adf_heading("Self-Healing Result", level=2))
        heal_attempted = failure.get("heal_attempted", False)
        if heal_attempted:
            heal_succeeded  = failure.get("heal_succeeded", False)
            healed_selector = failure.get("healed_selector", "N/A")
            content.append(_adf_table([
                ("Healing attempted", "Yes"),
                ("Result",            "Fixed ✓" if heal_succeeded else "Failed ✗"),
                ("New selector",      str(healed_selector) if heal_succeeded else "N/A"),
            ]))
        else:
            content.append(_adf_paragraph("Self-healing was not attempted."))

        # ── Section 8: Git History Context ────────────────────────────────
        content.append(_adf_heading("Git History Context", level=2))
        if git_history and isinstance(git_history, dict):
            last_commit = git_history.get("commits", [{}])
            last_date   = last_commit[0].get("date", "N/A") if last_commit else "N/A"
            content.append(_adf_table([
                ("File risk level", git_history.get("risk_level", "N/A").upper()),
                ("Bug-fix commits", str(git_history.get("bug_fix_count", 0))),
                ("Last modified",   last_date),
            ]))
        else:
            content.append(_adf_paragraph("No git history available for this file."))

        # ── Section 9: Attachments Note ───────────────────────────────────
        content.append(_adf_heading("Attachments", level=2))
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        content.append(_adf_paragraph(
            "The following files are attached to this ticket:"
        ))
        content.append(_adf_bullet_list([
            f"screenshot_{ts}.png — Browser state at point of failure",
            f"snapshot_{ts}.html  — Full page HTML at point of failure",
            f"test_log_{ts}.txt   — Complete test execution log",
        ]))

        return {"version": 1, "type": "doc", "content": content}

    # ─────────────────────────────────────────────────────────────────────────
    # Payload builder
    # ─────────────────────────────────────────────────────────────────────────

    def build_payload(
        self,
        test_case: dict,
        failure: dict,
        severity: str,
        environment: dict | None = None,
        git_history: dict | None = None,
        project: str | None = None,
        test_type: str | None = None,
        risk_level: str = "medium",
        selector_used: str | None = None,
        heal_attempted: bool = False,
        heal_succeeded: bool = False,
    ) -> dict:
        """
        Build the complete Jira issue creation payload dict.

        Every QA-standard field is included as a proper Jira field:
          - summary, description (ADF), issuetype, priority, severity,
            labels, and environment are all top-level Jira fields.

        Args:
            test_case (dict): The test case dict that failed.
            failure (dict): Failure details (step_number, step_title,
                            expected_result, actual_result, error).
            severity (str): Mapped severity string ("Critical" / "Major" /
                            "Minor" / "Trivial").
            environment (dict | None): Runtime environment info.
            git_history (dict | None): Git history from GitAnalyzer.
            project (str | None): Project override. Falls back to JIRA_PROJECT_KEY.
            test_type (str | None): Test type label ("regression", "feature", …).
            risk_level (str): QAX risk level ("critical", "high", "medium", "low").
            selector_used (str | None): CSS selector that caused the failure.
            heal_attempted (bool): Whether AI healing was tried.
            heal_succeeded (bool): Whether AI healing fixed the step.

        Returns:
            dict: Jira issue creation payload ready to POST.
        """
        project_key = project or self._project

        step_title = failure.get("step_title", failure.get("action", "unknown step"))
        summary = (
            f"[QAX] {test_case.get('title', 'Test failure')} — "
            f"Step {failure.get('step_number', '?')} failed: "
            f"{str(step_title)[:60]}"
        )

        module_label = (
            test_case.get("module", "unknown")
            .lower()
            .replace(" ", "-")
            .replace("_", "-")
        )
        type_label = (test_type or "unknown").lower().replace(" ", "-")

        labels = [
            "qax-auto-reported",
            f"module-{module_label}",
            f"risk-{risk_level.lower()}",
            f"test-type-{type_label}",
        ]

        # Enrich failure dict with heal info for ADF builder
        failure_enriched = {
            **failure,
            "heal_attempted": heal_attempted,
            "heal_succeeded": heal_succeeded,
            "selector_used":  selector_used or failure.get("selector_used"),
            "risk_level":     risk_level,
            "test_type":      test_type,
        }

        adf_desc = self.build_adf_description(
            test_case=test_case,
            failure=failure_enriched,
            environment=environment,
            git_history=git_history,
        )

        env_text = self._build_environment_text(environment)

        fields: dict = {
            "project":     {"key": project_key},
            "summary":     summary,
            "description": adf_desc,
            "issuetype":   {"name": "Bug"},
            "priority":    self.map_priority(risk_level),
            "labels":      labels,
        }

        # Severity custom field
        fields[self._sev_field] = {"value": severity}

        # Environment field (string value)
        if env_text:
            fields[self._env_field] = env_text

        return {"fields": fields}

    # ─────────────────────────────────────────────────────────────────────────
    # Public: create_bug
    # ─────────────────────────────────────────────────────────────────────────

    def create_bug(
        self,
        test_case: dict,
        failure: dict,
        screenshot_path: str | None = None,
        html_snapshot_path: str | None = None,
        test_log: str | None = None,
        git_history: dict | None = None,
        project: str | None = None,
        test_type: str | None = None,
        risk_level: str = "medium",
        selector_used: str | None = None,
        heal_attempted: bool = False,
        heal_succeeded: bool = False,
        environment: dict | None = None,
    ) -> dict | None:
        """
        Create a Jira bug ticket from a test execution failure.

        Flow:
          1. Guard: not self.enabled → log warning, return None
          2. Map severity from risk_level + confidence
          3. Build full Jira payload (ADF description + all real fields)
          4. DRY_RUN guard: if true → log summary, return mock result dict
          5. POST to Jira /rest/api/3/issue
          6. Attach screenshot, HTML snapshot, and test log as real file attachments
          7. Log success with key, priority, severity, attachment count, and URL
          8. Return result dict with key, url, severity, priority, attachments_count

        Args:
            test_case (dict): The failing test case dict. Must have: title,
                              test_case_id, module, confidence_score,
                              manual_steps (list of step dicts).
            failure (dict): Failure details with: step_number, step_title,
                            expected_result, actual_result, error.
            screenshot_path (str | None): Path to PNG screenshot file.
            html_snapshot_path (str | None): Path to HTML snapshot file.
            test_log (str | None): Full test execution log as a text string.
            git_history (dict | None): Git history dict from GitAnalyzer.
            project (str | None): Jira project key override.
            test_type (str | None): "regression", "feature", "smoke", "uat".
            risk_level (str): "critical", "high", "medium", "low".
            selector_used (str | None): The CSS selector that failed.
            heal_attempted (bool): Whether ClaudeHealer was invoked.
            heal_succeeded (bool): Whether healing succeeded.
            environment (dict | None): Dict with os, browser, url, project,
                                        git_branch keys.

        Returns:
            dict | None: On success: {key, url, severity, priority,
                                      attachments_count, dry_run}.
                         On DRY_RUN: {key, dry_run, summary, severity}.
                         None if not enabled or any fatal error.
        """
        # Allow DRY_RUN even when credentials are absent (no real API calls)
        if not self.enabled and not self.dry_run:
            logger.warning(
                "QAX BugReporter: Jira credentials not configured — "
                "bug not created. Set JIRA_BASE_URL, JIRA_EMAIL, "
                "JIRA_API_TOKEN, JIRA_PROJECT_KEY in .env."
            )
            return None

        # ── 1. Map severity ────────────────────────────────────────────────
        confidence = float(test_case.get("confidence_score", 7))
        severity   = self.map_severity(risk_level, confidence)
        priority   = self.map_priority(risk_level)

        # ── 2. Build payload ───────────────────────────────────────────────
        try:
            payload = self.build_payload(
                test_case=test_case,
                failure=failure,
                severity=severity,
                environment=environment,
                git_history=git_history,
                project=project,
                test_type=test_type,
                risk_level=risk_level,
                selector_used=selector_used,
                heal_attempted=heal_attempted,
                heal_succeeded=heal_succeeded,
            )
        except Exception as e:
            logger.error("QAX BugReporter: failed to build payload: %s", e)
            return None

        summary = payload["fields"].get("summary", "")
        labels  = payload["fields"].get("labels", [])

        attachment_count = sum([
            1 if screenshot_path else 0,
            1 if html_snapshot_path else 0,
            1 if test_log else 0,
        ])

        # ── 3. DRY_RUN guard ───────────────────────────────────────────────
        if self.dry_run:
            logger.info(
                "[DRY RUN] Would create Jira bug:\n"
                "  Summary:     %s\n"
                "  Priority:    %s\n"
                "  Severity:    %s\n"
                "  Labels:      %s\n"
                "  Attachments: %d file(s)",
                summary,
                priority.get("name"),
                severity,
                labels,
                attachment_count,
            )
            print(f"\n  [DRY RUN] Would create Jira bug:")
            print(f"    Summary:     {summary}")
            print(f"    Priority:    {priority.get('name')}")
            print(f"    Severity:    {severity}")
            print(f"    Labels:      {labels}")
            print(f"    Attachments: {attachment_count} file(s)")
            return {
                "key":      "DRY-RUN-BUG-001",
                "dry_run":  True,
                "summary":  summary,
                "severity": severity,
                "priority": priority.get("name"),
                "labels":   labels,
            }

        # ── 4. POST to Jira ────────────────────────────────────────────────
        try:
            url = f"{self._base_url}/rest/api/3/issue"
            response = requests.post(
                url,
                json=payload,
                headers=self.headers,
                timeout=30,
            )
            if not response.ok:
                logger.error(
                    "QAX BugReporter: Jira API error %d — %s",
                    response.status_code,
                    response.text[:400],
                )
                return None

            issue_data = response.json()
            issue_key  = issue_data.get("key", "")
            issue_url  = f"{self._base_url}/browse/{issue_key}"

        except Exception as e:
            logger.error("QAX BugReporter: POST to Jira failed: %s", e)
            return None

        # ── 5. Attach files ────────────────────────────────────────────────
        attached = 0

        if screenshot_path and Path(screenshot_path).exists():
            if self.attach_file(issue_key, screenshot_path, "image/png"):
                attached += 1

        if html_snapshot_path and Path(html_snapshot_path).exists():
            if self.attach_file(issue_key, html_snapshot_path, "text/html"):
                attached += 1

        if test_log:
            if self.attach_log(issue_key, test_log):
                attached += 1

        # ── 6. Log success ─────────────────────────────────────────────────
        logger.info(
            "QAX BugReporter: Bug created: %s\n"
            "  Priority:    %s\n"
            "  Severity:    %s\n"
            "  Attachments: %d attached\n"
            "  URL:         %s",
            issue_key,
            priority.get("name"),
            severity,
            attached,
            issue_url,
        )

        return {
            "key":               issue_key,
            "url":               issue_url,
            "severity":          severity,
            "priority":          priority.get("name", "Medium"),
            "attachments_count": attached,
            "dry_run":           False,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Public: attach_file
    # ─────────────────────────────────────────────────────────────────────────

    def attach_file(
        self,
        issue_key: str,
        file_path: str,
        mime_type: str,
    ) -> bool:
        """
        Upload a file as an attachment to a Jira issue.

        Uses the Jira REST API v2 attachment endpoint. The Content-Type header
        is intentionally omitted from the main headers here — the requests
        library sets the correct multipart/form-data boundary automatically.

        X-Atlassian-Token: no-check is required to bypass Jira's CSRF protection
        on the attachment endpoint.

        Args:
            issue_key (str): Jira issue key (e.g. "PROJ-123").
            file_path (str): Absolute or relative path to the file on disk.
            mime_type (str): MIME type of the file (e.g. "image/png", "text/html").

        Returns:
            bool: True if the attachment was accepted (2xx response).
                  False on any error — never raises.
        """
        if not self.enabled:
            return False

        try:
            attach_url = (
                f"{self._base_url}/rest/api/2/issue/{issue_key}/attachments"
            )
            # Build headers without Content-Type (multipart sets it)
            creds   = f"{self._email}:{self._token}"
            encoded = base64.b64encode(creds.encode()).decode()
            attach_headers = {
                "Authorization":     f"Basic {encoded}",
                "X-Atlassian-Token": "no-check",
                "Accept":            "application/json",
            }

            file_name = Path(file_path).name
            with open(file_path, "rb") as f:
                response = requests.post(
                    attach_url,
                    files={"file": (file_name, f, mime_type)},
                    headers=attach_headers,
                    timeout=30,
                )

            if response.ok:
                logger.info(
                    "QAX BugReporter: Attached '%s' to %s", file_name, issue_key
                )
                return True

            logger.error(
                "QAX BugReporter: Attachment failed for %s (%s): %d — %s",
                file_name, issue_key, response.status_code, response.text[:200],
            )
            return False

        except Exception as e:  # noqa: BLE001
            logger.error("QAX BugReporter: attach_file error: %s", e)
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Public: attach_log
    # ─────────────────────────────────────────────────────────────────────────

    def attach_log(self, issue_key: str, log_text: str) -> bool:
        """
        Write a test execution log string to a temp .txt file and attach it.

        Creates the file in output/temp/ (creating the directory if needed),
        calls attach_file(), then deletes the temp file regardless of outcome.

        Args:
            issue_key (str): Jira issue key to attach to.
            log_text (str): Full text content of the test log.

        Returns:
            bool: True if attached successfully. False on any error.
        """
        if not self.enabled or not log_text:
            return False

        temp_dir = Path("output") / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_path  = temp_dir / f"test_log_{timestamp}.txt"

        try:
            log_path.write_text(log_text, encoding="utf-8")
            result = self.attach_file(issue_key, str(log_path), "text/plain")
        except Exception as e:  # noqa: BLE001
            logger.error("QAX BugReporter: attach_log write error: %s", e)
            result = False
        finally:
            try:
                log_path.unlink(missing_ok=True)
            except Exception:
                pass

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Public: update_severity
    # ─────────────────────────────────────────────────────────────────────────

    def update_severity(self, issue_key: str, new_severity: str) -> bool:
        """
        Update the severity field of an existing Jira bug to a new value.

        Used post-run to upgrade severity when a bug is found to be more
        critical than initially assessed (e.g., risk re-evaluation reveals
        a higher impact).

        Args:
            issue_key (str): Jira issue key to update (e.g. "PROJ-123").
            new_severity (str): Target severity value ("Critical", "Major",
                                "Minor", "Trivial").

        Returns:
            bool: True if the update was accepted (204 No Content response).
                  False on any error — never raises.
        """
        if not self.enabled:
            return False

        if self.dry_run:
            logger.info(
                "[DRY RUN] Would update severity: %s → %s", issue_key, new_severity
            )
            return True

        try:
            url  = f"{self._base_url}/rest/api/3/issue/{issue_key}"
            body = {"fields": {self._sev_field: {"value": new_severity}}}
            response = requests.put(
                url,
                json=body,
                headers=self.headers,
                timeout=15,
            )

            if response.ok or response.status_code == 204:
                logger.info(
                    "QAX BugReporter: Severity updated: %s → %s",
                    issue_key, new_severity
                )
                return True

            logger.error(
                "QAX BugReporter: Severity update failed for %s: %d — %s",
                issue_key, response.status_code, response.text[:200],
            )
            return False

        except Exception as e:  # noqa: BLE001
            logger.error("QAX BugReporter: update_severity error: %s", e)
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _build_environment_text(self, environment: dict | None) -> str:
        """
        Build a short plain-text environment string for the Jira environment field.

        Args:
            environment (dict | None): Dict with os, browser, url, git_branch keys.

        Returns:
            str: Formatted single-line or multi-line environment string,
                 or empty string if nothing is available.
        """
        if not environment:
            base_url = os.getenv("APP_BASE_URL", "")
            project  = os.getenv("DEFAULT_PROJECT", "")
            if base_url or project:
                return f"URL: {base_url or 'N/A'} | Project: {project or 'N/A'}"
            return ""

        parts = []
        if environment.get("os"):
            parts.append(f"OS: {environment['os']}")
        if environment.get("browser"):
            parts.append(f"Browser: {environment['browser']}")
        if environment.get("url"):
            parts.append(f"URL: {environment['url']}")
        if environment.get("project"):
            parts.append(f"Project: {environment['project']}")
        if environment.get("git_branch"):
            parts.append(f"Branch: {environment['git_branch']}")
        return " | ".join(parts)
