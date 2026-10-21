"""
bug_fetcher.py — Jira Bug Fetcher & Pattern Extractor
======================================================
Enhancement 5: Bug Pattern Learning.

Fetches resolved bugs from Jira for a given EXAMPLE module using JQL,
then calls Claude to extract the top recurring failure patterns.

These patterns are injected into the system prompt so that Claude
generates test cases specifically designed to catch the same classes
of bugs that have historically affected that module.

Usage (standalone):
    from core.bug_fetcher import BugFetcher
    fetcher = BugFetcher()
    bugs = fetcher.fetch_module_bugs("Orders", max_bugs=20)
    patterns = fetcher.extract_patterns(bugs)
    print(patterns)

Usage (with Claude client):
    patterns = fetcher.get_patterns_for_module("Orders")
    # Returns a formatted string ready to inject into the prompt
"""

import logging
import os

import requests
from dotenv import load_dotenv

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Load environment variables ────────────────────────────────────────────────
load_dotenv()

# ── Jira REST API constants ───────────────────────────────────────────────────
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "EXAMPLE")

# ── Maximum number of bug patterns Claude will extract ────────────────────────
MAX_PATTERNS = 5

# ── Claude model to use for pattern extraction ────────────────────────────────
# Imported lazily inside extract_patterns() to avoid circular imports
PATTERN_MODEL = os.getenv("CLAUDE_MODEL_HEAVY", "claude-sonnet-4-5")
PATTERN_MAX_TOKENS = 800


class BugFetcher:
    """
    Fetches resolved bugs from Jira for a specific EXAMPLE module and extracts
    recurring failure patterns using Claude AI.

    The extracted patterns are injected into the system prompt so Claude
    generates test cases that specifically cover historically problematic areas.

    Attributes:
        base_url (str): Jira Cloud base URL (e.g., https://your-domain.atlassian.net).
        auth (tuple): (email, api_token) tuple for Jira Basic Auth.
        project_key (str): Jira project key (e.g., "EXAMPLE").
    """

    def __init__(self):
        """
        Initialize the BugFetcher with Jira credentials from environment variables.

        Raises:
            ValueError: If required Jira credentials are missing.
        """
        self.base_url = JIRA_BASE_URL.rstrip("/")
        self.auth = (JIRA_EMAIL, JIRA_API_TOKEN)
        self.project_key = JIRA_PROJECT_KEY

        # Warn (not crash) if Jira credentials are not set
        # Bug fetching is an optional enhancement — missing creds just means no patterns
        if not self.base_url or not JIRA_EMAIL or not JIRA_API_TOKEN:
            logger.warning(
                "Jira credentials not fully configured in .env. "
                "Bug pattern fetching will return empty results. "
                "Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN to enable."
            )

        logger.debug("BugFetcher initialized for project: %s", self.project_key)

    # ─────────────────────────────────────────────────────────────────────────
    # Fetch Bugs from Jira
    # ─────────────────────────────────────────────────────────────────────────

    def fetch_module_bugs(
        self,
        module_name: str,
        max_bugs: int = 20,
    ) -> list[dict]:
        """
        Fetch resolved bugs from Jira for the specified EXAMPLE module.

        Uses JQL to search for Done bugs labeled with the module name,
        ordered by resolution date (most recent first).

        JQL used:
            project=EXAMPLE AND issuetype=Bug AND status=Done
            AND labels="{module_name}" ORDER BY resolutiondate DESC

        Args:
            module_name (str): EXAMPLE module name (e.g., "Orders", "Reports").
                               Used as the Jira label filter.
            max_bugs (int): Maximum number of bugs to retrieve. Default: 20.

        Returns:
            list[dict]: List of bug dicts, each with:
                - key (str): Jira issue key (e.g., "EXAMPLE-101")
                - summary (str): Bug summary/title
                - description (str): Bug description (truncated to 500 chars)
                - resolution_date (str): When the bug was resolved
                - labels (list[str]): All labels on the issue
                - components (list[str]): Jira components this bug belongs to
            Returns empty list on any error (safe fallback — never crashes).
        """
        # Guard: if credentials are missing, return empty list silently
        if not self.base_url or not JIRA_EMAIL or not JIRA_API_TOKEN:
            logger.debug("Skipping bug fetch — Jira credentials not configured")
            return []

        # ── Build JQL query ────────────────────────────────────────────────
        # TODO: Adjust the label format if your Jira uses different naming
        #       (e.g., "tracker" vs "Orders" vs "SHOP-Orders")
        jql = (
            f'project="{self.project_key}" '
            f'AND issuetype=Bug '
            f'AND status=Done '
            f'AND labels="{module_name}" '
            f'ORDER BY resolutiondate DESC'
        )

        # ── Jira REST API v3: search endpoint ─────────────────────────────
        url = f"{self.base_url}/rest/api/3/search"
        params = {
            "jql": jql,
            "maxResults": max_bugs,
            "fields": "summary,description,resolutiondate,labels,components,status",
        }

        try:
            logger.info(
                "Fetching resolved bugs for module '%s' from Jira... (JQL: %s)",
                module_name, jql
            )
            response = requests.get(
                url,
                auth=self.auth,
                params=params,
                timeout=15,
            )
            response.raise_for_status()

            data = response.json()
            issues = data.get("issues", [])

            if not issues:
                logger.info(
                    "No resolved bugs found for module '%s' in Jira. "
                    "Tip: Make sure bugs are labeled with '%s' in Jira.",
                    module_name, module_name
                )
                return []

            logger.info(
                "Found %d resolved bug(s) for module '%s'",
                len(issues), module_name
            )

            # ── Extract relevant fields from each issue ────────────────────
            bugs = []
            for issue in issues:
                fields = issue.get("fields", {})

                # Truncate description to 500 chars to avoid prompt bloat
                raw_desc = fields.get("description") or ""
                if isinstance(raw_desc, dict):
                    # Jira API v3 returns description as Atlassian Document Format
                    raw_desc = self._extract_adf_text(raw_desc)
                description = raw_desc[:500] if raw_desc else "No description provided."

                bugs.append({
                    "key": issue.get("key", ""),
                    "summary": fields.get("summary", "No summary"),
                    "description": description,
                    "resolution_date": (fields.get("resolutiondate") or "")[:10],
                    "labels": [lbl for lbl in (fields.get("labels") or [])],
                    "components": [
                        c.get("name", "") for c in (fields.get("components") or [])
                    ],
                })

            return bugs

        except requests.exceptions.ConnectionError:
            logger.warning(
                "Cannot connect to Jira at %s. "
                "Check your JIRA_BASE_URL in .env.",
                self.base_url
            )
            return []
        except requests.exceptions.Timeout:
            logger.warning("Jira API request timed out. Skipping bug pattern fetch.")
            return []
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                logger.warning(
                    "Jira authentication failed (401). "
                    "Check JIRA_EMAIL and JIRA_API_TOKEN in .env."
                )
            elif response.status_code == 403:
                logger.warning(
                    "Jira access denied (403). "
                    "Your account may not have permission to search bugs."
                )
            else:
                logger.warning("Jira API error: %s", e)
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning("Unexpected error fetching bugs from Jira: %s", e)
            return []

    # ─────────────────────────────────────────────────────────────────────────
    # Extract Patterns with Claude
    # ─────────────────────────────────────────────────────────────────────────

    def extract_patterns(self, bugs: list[dict]) -> str:
        """
        Use Claude to analyze a list of resolved bugs and extract the top
        recurring failure patterns.

        The output is a short, structured text block listing 3–5 patterns
        in a format suitable for injection into the system prompt.

        Args:
            bugs (list[dict]): Bug dicts from fetch_module_bugs().

        Returns:
            str: Formatted string with recurring failure patterns, e.g.:
                 "1. Silent success failures — functions return 200 but the success flag=false
                  2. Missing null checks — NullReferenceException on optional fields
                  ..."
                 Returns empty string if bugs list is empty or on any error.
        """
        if not bugs:
            logger.debug("No bugs provided to extract_patterns — returning empty string")
            return ""

        # ── Build a summary of the bugs for the prompt ────────────────────
        bug_lines = []
        for bug in bugs:
            line = (
                f"[{bug['key']}] {bug['summary']}\n"
                f"  Resolved: {bug['resolution_date']}\n"
                f"  Description: {bug['description'][:300]}"
            )
            bug_lines.append(line)

        bugs_text = "\n\n".join(bug_lines)

        extraction_prompt = f"""You are a QA analyst reviewing resolved bugs from the target project.

Here are {len(bugs)} resolved bugs for this module:

{bugs_text}

Analyze these bugs and identify the TOP {min(MAX_PATTERNS, len(bugs))} recurring failure PATTERNS.
A "pattern" is a category of bug that appears multiple times or represents a systemic risk.

Format your response EXACTLY like this (plain text, no markdown, no JSON):
1. [Pattern Name] — [One sentence describing what causes this class of bug and what to test for]
2. [Pattern Name] — [One sentence]
3. [Pattern Name] — [One sentence]

Return ONLY the numbered list. No introduction, no summary, no extra text.
Maximum {MAX_PATTERNS} patterns."""

        try:
            # Lazy import to avoid circular dependency with ClaudeClient
            import anthropic

            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                logger.warning("ANTHROPIC_API_KEY not set — cannot extract bug patterns")
                return ""

            from core.ai_provider import make_ai_client
            client = make_ai_client(api_key=api_key)
            response = client.messages.create(
                model=PATTERN_MODEL,
                max_tokens=PATTERN_MAX_TOKENS,
                messages=[
                    {"role": "user", "content": extraction_prompt}
                ],
            )

            patterns_text = response.content[0].text.strip()
            logger.info(
                "Extracted %d bug pattern(s) from %d resolved bugs",
                patterns_text.count("\n") + 1, len(bugs)
            )
            return patterns_text

        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to extract bug patterns with Claude: %s", e)
            return ""

    # ─────────────────────────────────────────────────────────────────────────
    # Convenience: Fetch + Extract in One Call
    # ─────────────────────────────────────────────────────────────────────────

    def get_patterns_for_module(
        self,
        module_name: str,
        max_bugs: int = 20,
    ) -> str:
        """
        Convenience method: fetch bugs AND extract patterns in one call.

        Use this in the main pipeline when --include-bugs is passed.

        Args:
            module_name (str): EXAMPLE module name (e.g., "Orders").
            max_bugs (int): Max number of bugs to fetch from Jira. Default: 20.

        Returns:
            str: Formatted pattern string, or empty string if unavailable.
        """
        bugs = self.fetch_module_bugs(module_name, max_bugs=max_bugs)
        if not bugs:
            return ""
        return self.extract_patterns(bugs)

    # ─────────────────────────────────────────────────────────────────────────
    # Internal Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_adf_text(self, adf_doc: dict) -> str:
        """
        Extract plain text from an Atlassian Document Format (ADF) JSON object.

        Jira API v3 returns descriptions as ADF instead of plain text.
        This recursively walks the ADF tree and collects all text nodes.

        Args:
            adf_doc (dict): ADF JSON document from Jira API v3.

        Returns:
            str: Plain text extracted from the document.
        """
        if not isinstance(adf_doc, dict):
            return str(adf_doc)

        text_parts = []

        # If this node has a "text" value (it's a text leaf node), collect it
        if "text" in adf_doc:
            text_parts.append(adf_doc["text"])

        # Recursively process child content nodes
        for child in adf_doc.get("content", []):
            text_parts.append(self._extract_adf_text(child))

        return " ".join(filter(None, text_parts)).strip()
