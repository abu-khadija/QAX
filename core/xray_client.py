"""
xray_client.py — Jira/Xray API Client
=======================================
Handles authentication and test case creation in Jira Xray.
Supports both dry-run mode (for testing without actual API calls)
and live mode (pushes test cases to Xray).

Xray Cloud REST API v2 is used for test case creation.
Docs: https://docs.getxray.app/display/XRAYCLOUD/REST+API
"""

import json
import logging
import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Xray Cloud API endpoints ──────────────────────────────────────────────────
XRAY_AUTH_URL = "https://xray.cloud.getxray.app/api/v2/authenticate"
XRAY_GRAPHQL_URL = "https://xray.cloud.getxray.app/api/v2/graphql"
XRAY_REST_URL = "https://xray.cloud.getxray.app/api/v2"


class XrayClient:
    """
    Client for the Xray Cloud API (Jira test management).

    Authenticates using Client ID + Client Secret, then creates
    test cases in the specified Jira project. Supports dry-run mode
    to preview what would be pushed without actually calling the API.

    Usage:
        client = XrayClient()
        result = client.push_test_case(test_case_dict)
    """

    def __init__(self, dry_run: bool | None = None):
        """
        Initialize the Xray client.

        Loads credentials from .env file:
          - XRAY_CLIENT_ID: Xray App client ID (from Jira API token settings)
          - XRAY_CLIENT_SECRET: Xray App client secret
          - JIRA_CLOUD_ID: Your Jira Cloud instance ID
          - JIRA_PROJECT_KEY: Target project key (e.g., "EXAMPLE")
          - DRY_RUN: Set to "true" to skip actual API calls

        Args:
            dry_run (bool | None): Explicit, request-scoped dry-run flag. When given
                it WINS — the caller decides, and no process-global state is consulted
                or mutated. When None, falls back to the DRY_RUN env var, defaulting to
                TRUE (safe default — never push live unless explicitly told to). This
                matches QAXContext's default and removes the old live-by-default gap.

        Raises:
            ValueError: If required credentials are missing.
        """
        load_dotenv()

        self.client_id = os.getenv("XRAY_CLIENT_ID")
        self.client_secret = os.getenv("XRAY_CLIENT_SECRET")
        self.cloud_id = os.getenv("JIRA_CLOUD_ID")
        self.project_key = os.getenv("JIRA_PROJECT_KEY", "EXAMPLE")
        self.jira_base_url = os.getenv("JIRA_BASE_URL", "")
        self.jira_email = os.getenv("JIRA_EMAIL", "")
        self.jira_api_token = os.getenv("JIRA_API_TOKEN", "")

        # Request-scoped flag wins; otherwise env with a SAFE default of true.
        if dry_run is not None:
            self.dry_run = bool(dry_run)
        else:
            self.dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

        if self.dry_run:
            logger.info("XrayClient initialized in DRY RUN mode — no API calls will be made")
        else:
            # Validate required credentials for live mode
            missing = [
                name for name, val in [
                    ("XRAY_CLIENT_ID", self.client_id),
                    ("XRAY_CLIENT_SECRET", self.client_secret),
                    ("JIRA_CLOUD_ID", self.cloud_id),
                ]
                if not val
            ]
            if missing:
                raise ValueError(
                    f"Missing required environment variables: {', '.join(missing)}\n"
                    "Set DRY_RUN=true to test without credentials."
                )

        # Token cache — refreshed per session
        self._auth_token: str | None = None

        logger.info(
            "XrayClient initialized (project=%s, dry_run=%s)",
            self.project_key, self.dry_run
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Authentication
    # ─────────────────────────────────────────────────────────────────────────

    def _get_auth_token(self) -> str:
        """
        Authenticate with Xray Cloud and return a Bearer token.

        Caches the token for the session lifetime (tokens expire after 24h,
        but we don't track that — re-instantiate the client if needed).

        Returns:
            str: Bearer token for Xray API requests.

        Raises:
            requests.HTTPError: If authentication fails.
        """
        if self._auth_token:
            return self._auth_token

        logger.debug("Authenticating with Xray Cloud...")

        response = requests.post(
            XRAY_AUTH_URL,
            json={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/json"},
            timeout=30,
        )

        response.raise_for_status()

        # Xray returns the token as a quoted string in the response body
        token = response.text.strip().strip('"')
        self._auth_token = token

        logger.debug("Xray authentication successful")
        return token

    # ─────────────────────────────────────────────────────────────────────────
    # Push Single Test Case
    # ─────────────────────────────────────────────────────────────────────────

    def push_test_case(self, test_case: dict) -> dict:
        """
        Push a single validated test case to Xray as a Jira Test issue.

        In dry-run mode, prints what would be pushed without calling the API.

        Args:
            test_case (dict): A validated test case dictionary matching xray_schema.json.

        Returns:
            dict: Result with keys:
                - "success" (bool): Whether the push succeeded
                - "test_case_id" (str): The local test case ID
                - "jira_key" (str | None): The created Jira issue key (e.g., "EXAMPLE-42")
                - "error" (str | None): Error message if failed
        """
        tc_id = test_case.get("test_case_id", "UNKNOWN")

        # ── Dry run mode: just log and return success ─────────────────────────
        if self.dry_run:
            logger.info("[DRY RUN] Would push test case: %s — %s", tc_id, test_case.get("title"))
            return {
                "success": True,
                "test_case_id": tc_id,
                "jira_key": f"DRY-RUN-{tc_id}",
                "error": None,
            }

        try:
            # Convert our schema to Jira/Xray API format
            jira_payload = self._build_jira_payload(test_case)

            token = self._get_auth_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            # Create the test issue in Jira
            response = requests.post(
                f"{XRAY_REST_URL}/import/test",
                headers=headers,
                json=jira_payload,
                timeout=30,
            )
            response.raise_for_status()

            result_data = response.json()
            jira_key = result_data.get("key") or result_data.get("testIssueKey", "UNKNOWN")

            logger.info("Pushed test case %s → Jira issue %s", tc_id, jira_key)
            return {
                "success": True,
                "test_case_id": tc_id,
                "jira_key": jira_key,
                "error": None,
            }

        except requests.HTTPError as e:
            error_msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error("Failed to push %s: %s", tc_id, error_msg)
            return {
                "success": False,
                "test_case_id": tc_id,
                "jira_key": None,
                "error": error_msg,
            }

        except Exception as e:
            logger.error("Unexpected error pushing %s: %s", tc_id, str(e))
            return {
                "success": False,
                "test_case_id": tc_id,
                "jira_key": None,
                "error": str(e),
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Push Batch of Test Cases
    # ─────────────────────────────────────────────────────────────────────────

    def push_batch(self, test_cases: list[dict]) -> dict:
        """
        Push a list of validated test cases to Xray, one at a time.

        Continues pushing even if individual cases fail, collecting all
        results for the final summary.

        Args:
            test_cases (list[dict]): List of validated test case dictionaries.

        Returns:
            dict: Batch result with keys:
                - "pushed" (list): Successfully pushed test cases with their Jira keys
                - "failed" (list): Failed test cases with error messages
                - "summary" (dict): Push statistics
        """
        pushed = []
        failed = []

        logger.info("Starting batch push of %d test cases...", len(test_cases))

        for test_case in test_cases:
            result = self.push_test_case(test_case)

            if result["success"]:
                pushed.append(result)
            else:
                failed.append(result)

        summary = {
            "total": len(test_cases),
            "pushed_count": len(pushed),
            "failed_count": len(failed),
            "dry_run": self.dry_run,
            "pushed_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "Batch push complete: %d pushed, %d failed",
            len(pushed), len(failed)
        )

        return {
            "pushed": pushed,
            "failed": failed,
            "summary": summary,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Bug filing (standard Jira issue API — findings, not Xray Tests)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _adf(text: str) -> dict:
        """Minimal Atlassian Document Format doc from plain text (Jira Cloud needs ADF)."""
        paras = [{"type": "paragraph", "content": [{"type": "text", "text": ln}]}
                 for ln in str(text or "").split("\n") if ln.strip()]
        return {"type": "doc", "version": 1, "content": paras or
                [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}]}

    def push_bug(self, bug: dict) -> dict:
        """
        File ONE security finding as a Jira **Bug** via the standard Jira issue API
        (not Xray's test-import endpoint, which is for Tests). DRY_RUN previews only.
        `bug` keys used: title, description, priority (critical/high/medium/low), labels.
        """
        bid = bug.get("test_case_id") or bug.get("id") or "BUG"
        if self.dry_run:
            logger.info("[DRY RUN] Would file Jira Bug: %s — %s", bid, bug.get("title"))
            return {"success": True, "test_case_id": bid, "jira_key": f"DRY-RUN-BUG-{bid}", "error": None}
        try:
            import base64
            pri = {"critical": "Highest", "high": "High", "medium": "Medium",
                   "low": "Low"}.get(str(bug.get("priority", "medium")).lower(), "Medium")
            payload = {"fields": {
                "project": {"key": self.project_key},
                "summary": str(bug.get("title", "Security finding"))[:250],
                "description": self._adf(bug.get("description", "")),
                "issuetype": {"name": "Bug"},
                "priority": {"name": pri},
                "labels": bug.get("labels", ["qax", "security"]),
            }}
            auth = base64.b64encode(f"{self.jira_email}:{self.jira_api_token}".encode()).decode()
            resp = requests.post(f"{self.jira_base_url.rstrip('/')}/rest/api/3/issue",
                                 headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
                                 json=payload, timeout=30)
            if resp.status_code in (200, 201):
                return {"success": True, "test_case_id": bid,
                        "jira_key": (resp.json() or {}).get("key"), "error": None}
            return {"success": False, "test_case_id": bid, "jira_key": None,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except Exception as exc:  # pragma: no cover - network
            return {"success": False, "test_case_id": bid, "jira_key": None, "error": str(exc)}

    def push_bugs(self, findings: list) -> dict:
        """File a batch of findings as Jira Bugs. Adapter-shaped result (never raises)."""
        results = [self.push_bug(b) for b in (findings or []) if isinstance(b, dict)]
        ok = [r for r in results if r.get("success")]
        return {
            "success": bool(results) and len(ok) == len(results),
            "exported_count": len(ok),
            "failed_count": len(results) - len(ok),
            "references": [r["jira_key"] for r in ok if r.get("jira_key")],
            "error": None if ok or not results else (results[0].get("error") or "all failed"),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Payload Builder (Internal)
    # ─────────────────────────────────────────────────────────────────────────

    def _build_jira_payload(self, test_case: dict) -> dict:
        """
        Convert our internal test case schema to the Xray REST API payload format.

        Maps our xray_schema.json fields to Xray's expected structure.
        Xray test steps use their own format, so we convert our steps array.

        Args:
            test_case (dict): Test case following our internal xray_schema.json.

        Returns:
            dict: Payload formatted for the Xray Cloud REST API.

        Note: This payload uses only standard Jira fields (project, summary,
              description, issuetype, priority, labels) plus Xray's own testtype
              and steps — none of these require a custom field ID, so a basic
              push is not blocked by field mapping. The only optional custom
              field (Module) is left commented out below; to enable it, supply
              its field ID from {JIRA_BASE_URL}/rest/api/3/field.
        """
        # Convert our manual_steps to Xray manual-test step format.
        # Generated cases expose manual_steps (action / expected_result / notes);
        # fall back to the legacy "steps" key for older files. This is the
        # human-readable step list pushed to Xray — the automation_steps path is
        # executed separately by the Playwright runner and is not pushed here.
        source_steps = test_case.get("manual_steps") or test_case.get("steps") or []
        xray_steps = [
            {
                "action": step.get("action", ""),
                "result": step.get("expected_result", ""),
                "data": step.get("data", ""),   # per-step test data (empty if none)
            }
            for step in source_steps
        ]

        # Map priority to Jira priority names
        priority_map = {
            "critical": "Highest",
            "high": "High",
            "medium": "Medium",
            "low": "Low",
        }

        payload = {
            "testtype": "Manual",
            "fields": {
                "project": {"key": self.project_key},
                "summary": test_case.get("title", ""),
                "description": self._build_jira_description(test_case),
                "issuetype": {"name": "Test"},
                "priority": {"name": priority_map.get(test_case.get("priority", "medium"), "Medium")},
                "labels": [
                    "auto-generated",
                    "qax-agent",
                    test_case.get("module", "General").lower().replace(" ", "-"),
                ],
                # TODO: Uncomment and set the correct custom field ID for "Module" if it exists in your Jira
                # "customfield_10100": test_case.get("module", ""),
            },
            "steps": xray_steps,
        }

        return payload

    def _build_jira_description(self, test_case: dict) -> str:
        """
        Build a formatted Jira issue description from the test case data.

        Includes the description, preconditions, test data, and metadata
        in a readable format for Jira's text renderer.

        Args:
            test_case (dict): Test case dictionary.

        Returns:
            str: Formatted description string for the Jira issue body.
        """
        lines = [
            f"*Description:* {test_case.get('description', '')}",
            "",
            "*Preconditions:*",
        ]

        # Add each precondition as a bullet point
        for pre in test_case.get("preconditions", []):
            lines.append(f"* {pre}")

        # Add test data section if present
        test_data = test_case.get("test_data")
        if test_data:
            lines.append("")
            lines.append("*Test Data:*")
            lines.append("{code:json}")
            lines.append(json.dumps(test_data, indent=2))
            lines.append("{code}")

        # Add metadata footer
        lines.extend([
            "",
            "----",
            f"*Generated from:* {test_case.get('generated_from_file', 'Unknown')}",
            f"*Confidence Score:* {test_case.get('confidence_score', '?')}/10",
            f"*Generated at:* {test_case.get('generated_at', 'Unknown')}",
        ])

        if test_case.get("needs_review"):
            lines.extend([
                "",
                f"*⚠ Needs Review:* {test_case.get('review_note', '')}",
            ])

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # Fetch Tests by Tag (Integration Layer — Enhancement Integration)
    # ─────────────────────────────────────────────────────────────────────────

    def fetch_tests_by_tag(self, tag: str) -> list[dict]:
        """
        Fetch all Jira Test issues that have a specific label/tag.

        Uses the Jira REST API to search for issues of type "Test" that
        carry the given label. This is used by smoke and regression run modes
        to retrieve the existing test library from Jira.

        JQL used:
            project={KEY} AND issuetype=Test AND labels="{tag}"
            ORDER BY created DESC

        Args:
            tag (str): The label to filter by (e.g., "smoke", "regression").

        Returns:
            list[dict]: List of test dicts, each with:
                - key (str): Jira issue key (e.g., "EXAMPLE-42")
                - summary (str): Test case title/summary
                - status (str): Current Jira issue status
                - labels (list[str]): All labels on the issue
                - priority (str): Issue priority
            Returns empty list on any error (safe fallback — never crashes).
        """
        # In dry-run mode, return a placeholder list so the caller can proceed
        if self.dry_run:
            logger.info(
                "[DRY RUN] Would fetch tests tagged '%s' from Jira — returning mock list",
                tag
            )
            return [
                {
                    "key": f"DRY-RUN-{tag.upper()}-001",
                    "summary": f"[DRY RUN] Sample {tag} test case 1",
                    "status": "To Do",
                    "labels": [tag, "auto-generated"],
                    "priority": "High",
                },
                {
                    "key": f"DRY-RUN-{tag.upper()}-002",
                    "summary": f"[DRY RUN] Sample {tag} test case 2",
                    "status": "To Do",
                    "labels": [tag, "auto-generated"],
                    "priority": "Medium",
                },
            ]

        if not self.jira_base_url or not self.jira_email or not self.jira_api_token:
            logger.warning(
                "Jira credentials not configured — cannot fetch tests by tag. "
                "Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN in .env."
            )
            return []

        jql = (
            f'project="{self.project_key}" '
            f'AND issuetype=Test '
            f'AND labels="{tag}" '
            f'ORDER BY created DESC'
        )

        url = f"{self.jira_base_url.rstrip('/')}/rest/api/3/search"
        params = {
            "jql": jql,
            "maxResults": 100,
            "fields": "summary,status,labels,priority",
        }

        try:
            logger.info("Fetching tests tagged '%s' from Jira...", tag)
            response = requests.get(
                url,
                auth=(self.jira_email, self.jira_api_token),
                params=params,
                timeout=15,
            )
            response.raise_for_status()

            data = response.json()
            issues = data.get("issues", [])

            tests = []
            for issue in issues:
                fields = issue.get("fields", {})
                tests.append({
                    "key": issue.get("key", ""),
                    "summary": fields.get("summary", ""),
                    "status": (fields.get("status") or {}).get("name", "Unknown"),
                    "labels": fields.get("labels", []),
                    "priority": (fields.get("priority") or {}).get("name", "Medium"),
                })

            logger.info("Found %d test(s) tagged '%s' in Jira", len(tests), tag)
            return tests

        except requests.exceptions.ConnectionError:
            logger.warning("Cannot connect to Jira at %s", self.jira_base_url)
            return []
        except requests.exceptions.HTTPError as e:
            logger.warning("Jira API error fetching tests by tag '%s': %s", tag, e)
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning("Unexpected error fetching tests by tag: %s", e)
            return []

    # ─────────────────────────────────────────────────────────────────────────
    # Fetch Single Test by ID
    # ─────────────────────────────────────────────────────────────────────────

    def fetch_test_by_id(self, test_id: str) -> dict | None:
        """
        Fetch a single Jira Test issue by its issue key.

        Retrieves full details for one test case — useful for looking up
        a specific test before updating its execution status.

        Args:
            test_id (str): The Jira issue key (e.g., "EXAMPLE-42").

        Returns:
            dict | None: Test case dict with keys:
                - key, summary, description, status, labels, priority,
                  components, created, updated
            Returns None if the issue does not exist or on any error.
        """
        if self.dry_run:
            logger.info("[DRY RUN] Would fetch test '%s' — returning mock data", test_id)
            return {
                "key": test_id,
                "summary": f"[DRY RUN] Mock test case {test_id}",
                "description": "Dry-run placeholder",
                "status": "To Do",
                "labels": ["auto-generated"],
                "priority": "Medium",
                "components": [],
                "created": "",
                "updated": "",
            }

        if not self.jira_base_url or not self.jira_email or not self.jira_api_token:
            logger.warning("Jira credentials not configured — cannot fetch test by ID")
            return None

        url = f"{self.jira_base_url.rstrip('/')}/rest/api/3/issue/{test_id}"

        try:
            logger.debug("Fetching test '%s' from Jira...", test_id)
            response = requests.get(
                url,
                auth=(self.jira_email, self.jira_api_token),
                params={"fields": "summary,description,status,labels,priority,components,created,updated"},
                timeout=15,
            )

            if response.status_code == 404:
                logger.warning("Test '%s' not found in Jira", test_id)
                return None

            response.raise_for_status()
            data = response.json()
            fields = data.get("fields", {})

            # Extract description text (handles both plain text and ADF format)
            raw_desc = fields.get("description") or ""
            if isinstance(raw_desc, dict):
                # ADF format — extract plain text
                raw_desc = self._extract_adf_text(raw_desc)

            return {
                "key": data.get("key", test_id),
                "summary": fields.get("summary", ""),
                "description": str(raw_desc)[:1000],
                "status": (fields.get("status") or {}).get("name", "Unknown"),
                "labels": fields.get("labels", []),
                "priority": (fields.get("priority") or {}).get("name", "Medium"),
                "components": [
                    c.get("name", "") for c in (fields.get("components") or [])
                ],
                "created": (fields.get("created") or "")[:10],
                "updated": (fields.get("updated") or "")[:10],
            }

        except requests.exceptions.ConnectionError:
            logger.warning("Cannot connect to Jira at %s", self.jira_base_url)
            return None
        except requests.exceptions.HTTPError as e:
            logger.warning("Jira API error fetching test '%s': %s", test_id, e)
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("Unexpected error fetching test '%s': %s", test_id, e)
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Update Test Execution Result
    # ─────────────────────────────────────────────────────────────────────────

    def update_test_execution(
        self,
        test_id: str,
        status: str,
        comment: str | None = None,
    ) -> bool:
        """
        Record a test execution result for a Jira Test issue.

        Posts a formatted comment to the Jira issue documenting the execution
        outcome. This works with any Jira + Xray setup and creates a permanent
        audit trail of when tests were run and what the result was.

        For full Xray execution tracking (test runs with pass/fail rates),
        this will be extended in Enhancement 3 (Playwright Execution Engine).

        Args:
            test_id (str): Jira issue key of the test (e.g., "EXAMPLE-42").
            status (str): Execution result — must be "PASS", "FAIL", or "BLOCKED".
            comment (str | None): Optional description of what happened during execution.
                                   Added to the Jira comment body.

        Returns:
            bool: True if the comment was posted successfully, False otherwise.
        """
        # Validate status value
        valid_statuses = {"PASS", "FAIL", "BLOCKED"}
        status = status.upper().strip()
        if status not in valid_statuses:
            logger.error(
                "Invalid execution status '%s'. Must be one of: %s",
                status, ", ".join(sorted(valid_statuses))
            )
            return False

        # Status icons for Jira comment readability
        status_icons = {"PASS": "✅", "FAIL": "❌", "BLOCKED": "🚫"}
        icon = status_icons[status]

        # In dry-run mode, just log the update
        if self.dry_run:
            logger.info(
                "[DRY RUN] Would update test '%s' → status: %s %s",
                test_id, icon, status
            )
            if comment:
                logger.info("[DRY RUN] Comment: %s", comment)
            return True

        if not self.jira_base_url or not self.jira_email or not self.jira_api_token:
            logger.warning(
                "Jira credentials not configured — cannot update test execution. "
                "Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN in .env."
            )
            return False

        # ── Build the Jira comment body ────────────────────────────────────
        from datetime import datetime, timezone as _tz
        now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M UTC")

        comment_lines = [
            f"*Execution Result:* {icon} {status}",
            f"*Executed at:* {now_str}",
            f"*Executed by:* QAX (automated)",
        ]
        if comment:
            comment_lines.extend(["", f"*Notes:* {comment}"])

        comment_body = "\n".join(comment_lines)

        # ── Post comment to Jira issue ─────────────────────────────────────
        url = f"{self.jira_base_url.rstrip('/')}/rest/api/3/issue/{test_id}/comment"

        # Jira API v3 uses Atlassian Document Format (ADF) for comment body
        adf_body = {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": comment_body}
                    ]
                }
            ]
        }

        try:
            response = requests.post(
                url,
                auth=(self.jira_email, self.jira_api_token),
                json={"body": adf_body},
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            response.raise_for_status()

            logger.info(
                "Updated execution status for '%s': %s %s",
                test_id, icon, status
            )
            return True

        except requests.exceptions.ConnectionError:
            logger.warning("Cannot connect to Jira — execution update failed for '%s'", test_id)
            return False
        except requests.exceptions.HTTPError as e:
            logger.warning(
                "Jira API error updating execution for '%s': %s",
                test_id, e
            )
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning("Unexpected error updating execution for '%s': %s", test_id, e)
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Internal Helper: ADF Text Extractor
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_adf_text(self, adf_doc: dict) -> str:
        """
        Extract plain text from an Atlassian Document Format (ADF) JSON object.

        Jira API v3 returns description fields as ADF instead of plain text.
        This recursively walks the ADF tree and collects all text nodes.

        Args:
            adf_doc (dict): ADF JSON document from Jira API v3.

        Returns:
            str: Plain text extracted from the document.
        """
        if not isinstance(adf_doc, dict):
            return str(adf_doc)

        text_parts = []
        if "text" in adf_doc:
            text_parts.append(adf_doc["text"])
        for child in adf_doc.get("content", []):
            text_parts.append(self._extract_adf_text(child))

        return " ".join(filter(None, text_parts)).strip()
