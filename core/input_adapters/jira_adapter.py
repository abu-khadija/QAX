"""
core/input_adapters/jira_adapter.py — Jira Ticket Input Adapter
================================================================
Fetches ticket details from Jira and converts them into a source input dict
that Claude can use to generate test cases from the ticket's story content.

Strategy:
  1. Fetch the ticket via Jira REST API v3
  2. Extract summary + ADF description text
  3. Return as a "story" input so Claude generates tests from requirements

Required env vars:
    JIRA_BASE_URL      — e.g. "https://org.atlassian.net"
    JIRA_EMAIL         — Atlassian account email
    JIRA_API_TOKEN     — API token from id.atlassian.com → Security

Usage:
    adapter = JiraInputAdapter()
    items   = adapter.fetch("PROJ-123")
    # items[0]["content"] = full ticket text for Claude
"""

import logging
import os
from base64 import b64encode

from core.input_adapters.base import BaseInputAdapter

logger = logging.getLogger(__name__)


class JiraInputAdapter(BaseInputAdapter):
    """
    Input adapter that fetches a Jira ticket and returns its description as
    a story-style input for test case generation.

    Falls back to an empty list on any network or auth failure — never raises.
    """

    name = "jira"

    def validate(self) -> tuple[bool, str]:
        """
        Check that Jira credentials are present in the environment.

        Returns:
            tuple[bool, str]: (True, "") if all vars are set,
                              (False, "Missing: ...") otherwise.
        """
        required = ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"]
        missing  = [k for k in required if not os.getenv(k)]
        if missing:
            return False, f"Missing env vars: {', '.join(missing)}"
        return True, ""

    def fetch(self, source: str, **kwargs) -> list[dict]:
        """
        Fetch a Jira ticket and return its content as a normalised input dict.

        Args:
            source (str): Jira issue key, e.g. "PROJ-123".
            **kwargs: Unused.

        Returns:
            list[dict]: Single-element list with the ticket content as a
                        story-style source, or [] on failure.
        """
        if not source:
            logger.warning("QAX JiraInputAdapter: no ticket key provided")
            return []

        valid, err = self.validate()
        if not valid:
            logger.warning("QAX JiraInputAdapter: %s", err)
            print(f"  [QAX] Jira not configured: {err}")
            return []

        base_url = os.getenv("JIRA_BASE_URL", "").rstrip("/")
        email    = os.getenv("JIRA_EMAIL", "")
        token    = os.getenv("JIRA_API_TOKEN", "")
        creds    = b64encode(f"{email}:{token}".encode()).decode()
        headers  = {
            "Authorization": f"Basic {creds}",
            "Accept":        "application/json",
        }

        try:
            import requests
            url  = f"{base_url}/rest/api/3/issue/{source}"
            resp = requests.get(url, headers=headers, timeout=15)
            if not resp.ok:
                logger.warning(
                    "QAX JiraInputAdapter: HTTP %s for %s", resp.status_code, source
                )
                print(f"  [QAX] Jira fetch failed: HTTP {resp.status_code}")
                return []

            issue  = resp.json()
            fields = issue.get("fields", {})
        except ImportError:
            logger.error("QAX JiraInputAdapter: requests not installed")
            print("  [QAX] requests not installed — run: pip install requests")
            return []
        except Exception as exc:
            logger.error("QAX JiraInputAdapter: fetch error — %s", exc)
            print(f"  [QAX] Jira error: {exc}")
            return []

        summary   = fields.get("summary", "")
        desc_text = self._extract_description(fields.get("description"))
        issue_type = fields.get("issuetype", {}).get("name", "")
        priority   = fields.get("priority",  {}).get("name", "")
        status     = fields.get("status",    {}).get("name", "")

        story_content = (
            f"Jira Ticket: {source}\n"
            f"Summary: {summary}\n"
            f"Type: {issue_type}\n"
            f"Priority: {priority}\n"
            f"Status: {status}\n\n"
            f"Description:\n{desc_text}\n"
        )

        logger.info("QAX JiraInputAdapter: fetched %s — %s", source, summary[:60])
        print(f"  Jira {source}: {summary[:60]}")

        return [{
            "file_path":   f"jira://{source}",
            "content":     story_content,
            "file_name":   source,
            "source_type": self.name,
            "metadata": {
                "ticket_key": source,
                "summary":    summary,
                "issue_type": issue_type,
                "priority":   priority,
                "status":     status,
            },
        }]

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _extract_description(self, desc_obj) -> str:
        """
        Extract plain text from a Jira Atlassian Document Format (ADF) object.

        Handles both raw string descriptions and ADF JSON objects gracefully.

        Args:
            desc_obj: The Jira description field value — ADF dict or string or None.

        Returns:
            str: Extracted plain text, or "" if empty/unparseable.
        """
        if not desc_obj:
            return ""
        if isinstance(desc_obj, str):
            return desc_obj

        # ADF: recursively extract text nodes
        texts: list[str] = []
        try:
            for block in desc_obj.get("content", []):
                for item in block.get("content", []):
                    if item.get("type") == "text":
                        texts.append(item.get("text", ""))
                    # Handle nested lists / table cells
                    for sub in item.get("content", []):
                        if sub.get("type") == "text":
                            texts.append(sub.get("text", ""))
        except Exception as exc:
            logger.debug("QAX JiraInputAdapter: ADF parse error — %s", exc)

        return " ".join(t for t in texts if t)
