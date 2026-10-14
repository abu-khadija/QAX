"""
core/adapters/xray_adapter.py — Xray Export Adapter
=====================================================
Wraps the existing XrayClient so the pipeline can push test cases to
Jira/Xray through the standard adapter interface.

DRY_RUN behaviour is inherited from XrayClient (reads DRY_RUN env var).
The adapter temporarily overrides the env var so the dry_run parameter
passed to export() is always respected regardless of .env content.

Usage:
    adapter = XrayAdapter()
    valid, err = adapter.validate_config()
    result = adapter.export(test_cases, project="MYAPP", dry_run=True)
"""

import logging
import os

from core.adapters.base import BaseExportAdapter

logger = logging.getLogger(__name__)


class XrayAdapter(BaseExportAdapter):
    """
    Export adapter that pushes test cases to Jira/Xray.

    Delegates to the existing core.xray_client.XrayClient for all
    API communication. The adapter layer adds:
      - credential pre-flight via validate_config()
      - uniform result dict mapping
      - exception containment so the pipeline never crashes
    """

    name = "xray"

    def validate_config(self) -> tuple[bool, str]:
        """
        Check that all Xray/Jira credentials are present in the environment.

        Returns:
            tuple[bool, str]: (True, "") if all vars are set,
                              (False, "Missing: ...") otherwise.
        """
        required = [
            "JIRA_BASE_URL",
            "JIRA_EMAIL",
            "JIRA_API_TOKEN",
            "XRAY_CLIENT_ID",
            "XRAY_CLIENT_SECRET",
        ]
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
        Push test cases to Xray via XrayClient.push_batch().

        Temporarily sets the DRY_RUN env var so XrayClient respects
        the dry_run parameter passed here, regardless of .env content.

        Args:
            test_cases (list[dict]): Validated test case dicts.
            project (str | None): Unused — XrayClient reads JIRA_PROJECT_KEY.
            dry_run (bool): When True, simulate push without API calls.

        Returns:
            dict: Standard result dict (see BaseExportAdapter.export docstring).
        """
        if not test_cases:
            return self._empty_result(0, error="No test cases provided")

        valid, err = self.validate_config()
        if not valid and not dry_run:
            logger.warning("QAX XrayAdapter: %s", err)
            return self._empty_result(len(test_cases), error=err)

        # S1: pass the dry-run flag request-scoped straight into the client — no
        # process-global os.environ mutation (which raced between threaded requests).
        try:
            from core.xray_client import XrayClient
            client = XrayClient(dry_run=dry_run)
            batch_result = client.push_batch(test_cases)

            pushed   = batch_result.get("pushed", [])
            failed   = batch_result.get("failed", [])
            keys     = [
                r["jira_key"] for r in pushed
                if r.get("jira_key")
            ]

            logger.info(
                "QAX XrayAdapter: pushed %d, failed %d",
                len(pushed), len(failed),
            )
            return self._success_result(len(pushed), len(failed), keys)

        except Exception as exc:
            logger.error("QAX XrayAdapter: export failed — %s", exc)
            return self._empty_result(len(test_cases), error=str(exc))
