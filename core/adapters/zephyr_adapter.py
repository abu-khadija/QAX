"""
core/adapters/zephyr_adapter.py — Zephyr Scale Export Adapter
==============================================================
Pushes test cases to Zephyr Scale (Atlassian Marketplace) using the
Zephyr Scale REST API v2.

Required env vars:
    ZEPHYR_API_TOKEN    — API token from Jira → Apps → Zephyr Scale → API Keys
    ZEPHYR_PROJECT_KEY  — Project key (e.g. "MYAPP")
    JIRA_BASE_URL       — Base Jira URL (e.g. "https://org.atlassian.net")

API reference:
    POST /rest/atm/1.0/testcase
    https://support.smartbear.com/zephyr-scale-cloud/api-docs/

Usage:
    adapter = ZephyrAdapter()
    result  = adapter.export(test_cases, dry_run=True)
"""

import logging
import os

from core.adapters.base import BaseExportAdapter

logger = logging.getLogger(__name__)


class ZephyrAdapter(BaseExportAdapter):
    """
    Export adapter that pushes test cases to Zephyr Scale via its REST API.

    Converts QAX test case dicts to Zephyr's STEP_BY_STEP test script format.
    In dry_run mode no HTTP calls are made and a placeholder key is returned.
    """

    name = "zephyr"

    def validate_config(self) -> tuple[bool, str]:
        """
        Check that all Zephyr Scale credentials are present in the environment.

        Returns:
            tuple[bool, str]: (True, "") if all vars are set,
                              (False, "Missing: ...") otherwise.
        """
        required = ["ZEPHYR_API_TOKEN", "ZEPHYR_PROJECT_KEY", "JIRA_BASE_URL"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            return False, f"Missing env vars: {', '.join(missing)}"
        return True, ""

    def export(
        self,
        test_cases: list[dict],
        project: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        """
        Push test cases to Zephyr Scale one at a time.

        Each test case is converted to Zephyr's STEP_BY_STEP script format.
        Continues on individual failures so partial success is possible.

        Args:
            test_cases (list[dict]): Validated QAX test case dicts.
            project (str | None): Project key override. Falls back to
                                   ZEPHYR_PROJECT_KEY env var.
            dry_run (bool): When True, log and return without HTTP calls.

        Returns:
            dict: Standard result dict (see BaseExportAdapter.export docstring).
        """
        if not test_cases:
            return self._empty_result(0, error="No test cases provided")

        valid, err = self.validate_config()
        if not valid and not dry_run:
            logger.warning("QAX ZephyrAdapter: %s", err)
            return self._empty_result(len(test_cases), error=err)

        if dry_run:
            logger.info(
                "QAX ZephyrAdapter [DRY RUN]: would push %d case(s) to Zephyr Scale",
                len(test_cases),
            )
            print(
                f"  [DRY RUN] Would push {len(test_cases)} test case(s) to Zephyr Scale"
            )
            return self._success_result(
                len(test_cases), 0, ["DRY-ZEPHYR-001"]
            )

        # ── Live push ──────────────────────────────────────────────────────────
        try:
            import requests
        except ImportError:
            return self._empty_result(
                len(test_cases), error="requests not installed"
            )

        base_url = os.getenv("JIRA_BASE_URL", "").rstrip("/")
        token    = os.getenv("ZEPHYR_API_TOKEN", "")
        proj     = os.getenv("ZEPHYR_PROJECT_KEY", "") or project or ""
        headers  = {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        }

        exported: list[str] = []
        failed_count = 0

        for tc in test_cases:
            payload = self._build_payload(tc, proj)
            try:
                resp = requests.post(
                    f"{base_url}/rest/atm/1.0/testcase",
                    json=payload,
                    headers=headers,
                    timeout=30,
                )
                if resp.ok:
                    key = resp.json().get("key", "")
                    exported.append(key)
                    logger.info(
                        "QAX ZephyrAdapter: pushed %s → %s",
                        tc.get("test_case_id", "?"), key,
                    )
                else:
                    failed_count += 1
                    logger.warning(
                        "QAX ZephyrAdapter: push failed for %s — HTTP %s",
                        tc.get("test_case_id", "?"), resp.status_code,
                    )
            except Exception as exc:
                failed_count += 1
                logger.warning(
                    "QAX ZephyrAdapter: exception for %s — %s",
                    tc.get("test_case_id", "?"), exc,
                )

        return self._success_result(len(exported), failed_count, exported)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _build_payload(self, tc: dict, project_key: str) -> dict:
        """
        Convert a QAX test case dict to Zephyr Scale API payload format.

        Args:
            tc (dict): QAX test case dict.
            project_key (str): Zephyr project key.

        Returns:
            dict: Zephyr Scale API request body.
        """
        steps = [
            {
                "description":    s.get("action", ""),
                "expectedResult": s.get("expected_result", ""),
            }
            for s in tc.get("manual_steps", [])
        ]
        return {
            "projectKey": project_key,
            "name":       tc.get("title", "Untitled"),
            "status":     "Draft",
            "priority":   tc.get("priority", "Medium"),
            "labels":     tc.get("tags", []),
            "testScript": {
                "type":  "STEP_BY_STEP",
                "steps": steps,
            },
        }
