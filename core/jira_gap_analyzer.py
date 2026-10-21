"""
jira_gap_analyzer.py — Jira/Xray Gap Analysis
===============================================
B1: Checks what test cases already exist in Xray before generating new ones.
Prevents duplicates and focuses effort on truly uncovered scenarios.

Requires: JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY in .env
If any credentials are missing, all methods return empty results gracefully
— the pipeline continues without crashing.

Usage:
    analyzer = JiraGapAnalyzer()
    existing = analyzer.fetch_existing_tests("Orders")
    gaps = analyzer.analyze_gaps(proposed_tests, existing)
    print(gaps["recommendation"])
"""

import logging
import os
import re

import requests
from dotenv import load_dotenv

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


class JiraGapAnalyzer:
    """
    Fetches existing test cases from Jira/Xray and compares them against
    newly proposed test cases to find gaps and duplicates.

    Gracefully degrades when Jira credentials are not configured —
    every method returns an empty/no-op result instead of crashing.

    Usage:
        analyzer = JiraGapAnalyzer()
        existing = analyzer.fetch_existing_tests("Orders")
        gaps = analyzer.analyze_gaps(proposed, existing)
        print(gaps["recommendation"])
    """

    def __init__(self) -> None:
        """
        Load Jira credentials from .env.

        Sets self._configured = False if any required credential is missing,
        which causes all subsequent methods to return empty results gracefully.
        """
        from core.env_loader import load_env
        load_env()
        self.jira_base = os.getenv("JIRA_BASE_URL", "").rstrip("/")
        self.jira_email = os.getenv("JIRA_EMAIL", "")
        self.jira_token = os.getenv("JIRA_API_TOKEN", "")
        self.project_key = os.getenv("JIRA_PROJECT_KEY", "")

        self._configured = bool(
            self.jira_base and self.jira_email and self.jira_token
        )

        if not self._configured:
            logger.warning(
                "QAX JiraGapAnalyzer: Jira credentials not fully configured — "
                "gap analysis will return empty results. "
                "Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN in .env to enable."
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Fetch Existing Tests
    # ─────────────────────────────────────────────────────────────────────────

    def fetch_existing_tests(self, module: str) -> list[dict]:
        """
        Fetch existing test cases from Jira filtered by module label.

        Queries Jira using JQL for Test issues labeled with the module name.
        Falls back to empty list gracefully if the API is unavailable or
        credentials are missing.

        Args:
            module (str): Module name used as a Jira label filter
                          (e.g., "Orders", "Reports").

        Returns:
            list[dict]: Each item contains:
                - key (str): Jira issue key (e.g., "PROJ-123")
                - summary (str): Issue summary / test title
                - status (str): Current status ("Pass", "Fail", etc.)
                - labels (list): Issue labels
        """
        if not self._configured:
            return []

        try:
            # Build JQL query — search for Test issues labeled with the module
            jql = (
                f'issuetype = Test AND labels = "{module}"'
            )
            if self.project_key:
                jql = f'project = "{self.project_key}" AND {jql}'

            url = f"{self.jira_base}/rest/api/3/search"
            resp = requests.get(
                url,
                auth=(self.jira_email, self.jira_token),
                params={
                    "jql": jql,
                    "fields": "summary,status,labels",
                    "maxResults": 200,
                },
                timeout=15,
            )

            if resp.status_code != 200:
                logger.warning(
                    "QAX JiraGapAnalyzer: Jira search returned HTTP %d — "
                    "returning empty list",
                    resp.status_code,
                )
                return []

            data = resp.json()
            results = []
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                status_info = fields.get("status", {})
                results.append({
                    "key": issue.get("key", ""),
                    "summary": fields.get("summary", ""),
                    "status": status_info.get("name", "Unknown"),
                    "labels": fields.get("labels", []),
                })

            logger.info(
                "QAX JiraGapAnalyzer: Found %d existing test(s) for module '%s'",
                len(results), module,
            )
            return results

        except Exception as e:  # noqa: BLE001
            logger.warning(
                "QAX JiraGapAnalyzer: Could not fetch existing tests "
                "(non-fatal): %s", e
            )
            return []

    # ─────────────────────────────────────────────────────────────────────────
    # Gap Analysis
    # ─────────────────────────────────────────────────────────────────────────

    def analyze_gaps(
        self,
        proposed: list[dict],
        existing: list[dict],
    ) -> dict:
        """
        Compare proposed test cases against existing Xray tests to find gaps.

        Uses word-overlap Jaccard similarity (>60% = duplicate) to detect
        test cases that are effectively already covered in Xray.

        Args:
            proposed (list[dict]): Newly generated test case dicts.
                                   Each must have a "title" key.
            existing (list[dict]): Existing Jira/Xray test dicts from
                                   fetch_existing_tests(). Each must have
                                   a "summary" key.

        Returns:
            dict: {
                "new": list[dict],          # proposed cases NOT in Xray
                "duplicates": list[dict],   # proposed cases already in Xray
                "gap_count": int,           # number of truly new cases
                "duplicate_count": int,     # number of duplicate cases
                "recommendation": str,      # human-readable summary
            }
        """
        if not existing:
            return {
                "new": proposed,
                "duplicates": [],
                "gap_count": len(proposed),
                "duplicate_count": 0,
                "recommendation": (
                    f"No existing tests found in Xray — "
                    f"all {len(proposed)} proposed test(s) are new."
                    if proposed
                    else "No tests proposed and none in Xray."
                ),
            }

        existing_summaries = [e.get("summary", "") for e in existing]

        new_cases: list[dict] = []
        duplicate_cases: list[dict] = []

        for tc in proposed:
            title = tc.get("title", "")
            if self._is_duplicate(title, existing_summaries):
                duplicate_cases.append(tc)
            else:
                new_cases.append(tc)

        gap_count = len(new_cases)
        dup_count = len(duplicate_cases)
        existing_count = len(existing)

        # ── Build recommendation string ────────────────────────────────────
        if gap_count == 0:
            recommendation = (
                f"All {dup_count} proposed test(s) already exist in Xray "
                f"({existing_count} existing tests checked) — nothing to push."
            )
        elif dup_count == 0:
            recommendation = (
                f"{gap_count} new test(s) needed — "
                f"none already covered ({existing_count} existing tests checked)."
            )
        else:
            recommendation = (
                f"{gap_count} new test(s) needed, "
                f"{dup_count} already covered in Xray "
                f"({existing_count} existing tests checked)."
            )

        logger.info("QAX JiraGapAnalyzer: %s", recommendation)

        return {
            "new": new_cases,
            "duplicates": duplicate_cases,
            "gap_count": gap_count,
            "duplicate_count": dup_count,
            "recommendation": recommendation,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Private Helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _jaccard(title1: str, title2: str) -> float:
        """
        Compute Jaccard word-overlap similarity between two strings.

        Strips punctuation and compares word sets case-insensitively.

        Args:
            title1 (str): First string to compare.
            title2 (str): Second string to compare.

        Returns:
            float: Similarity between 0.0 (no overlap) and 1.0 (identical).
        """
        w1 = set(re.sub(r"[^\w\s]", "", title1.lower()).split())
        w2 = set(re.sub(r"[^\w\s]", "", title2.lower()).split())
        if not w1 or not w2:
            return 0.0
        return len(w1 & w2) / len(w1 | w2)

    def _is_duplicate(self, title: str, existing_summaries: list[str]) -> bool:
        """
        Check if a proposed title is sufficiently similar to any existing test.

        Uses 60% Jaccard threshold — if more than 60% of words overlap,
        the test is considered a duplicate.

        Args:
            title (str): Proposed test case title.
            existing_summaries (list[str]): Existing Jira test summaries.

        Returns:
            bool: True if the title matches an existing test above threshold.
        """
        for summary in existing_summaries:
            if self._jaccard(title, summary) > 0.60:
                return True
        return False
