"""
claude_healer.py — AI-Powered CSS Selector Self-Healing
========================================================
When a Playwright step fails because a CSS selector no longer matches,
ClaudeHealer sends the failed step + error message + page HTML to Claude
and asks it to suggest a corrected CSS selector.

Healing flow:
  1. ActionMapper runs a step → FAIL (element not found)
  2. TestExecutor calls ClaudeHealer.heal_step()
  3. Healer grabs page HTML (stripped of script/style tags)
  4. Sends to Claude: "suggest the correct CSS selector"
  5. Claude responds with JSON: { suggested_selector, explanation, confidence }
  6. If confidence >= 7 → return suggestion (retry the step)
  7. If confidence < 7  → return None (mark step as permanently failed)

All methods are async and wrapped in try/except — they return None on
any error so the test executor can fail gracefully.
"""

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Playwright availability guard ─────────────────────────────────────────────
try:
    from playwright.async_api import Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Page = None  # type: ignore[assignment,misc]

# ── Minimum confidence score required to use a healed selector ────────────────
HEALING_CONFIDENCE_THRESHOLD = 7

# ── Prompt template sent to Claude ───────────────────────────────────────────
HEALING_PROMPT_TEMPLATE = """You are a QA automation expert helping fix a broken Playwright test.

A test step FAILED with a CSS selector error. Your job is to analyze the page HTML
and suggest a corrected CSS selector that will find the intended UI element.

FAILED STEP:
  Action     : {action_type}
  Description: {step_description}
  Selector   : {selector_used}
  Error      : {error_message}

EXPECTED RESULT: {expected_result}

PAGE HTML (first {html_length} chars):
{page_html}

Respond with ONLY valid JSON (no markdown, no explanation outside JSON):
{{
  "suggested_selector": "<CSS selector string, or null if you cannot find it>",
  "explanation": "<brief explanation of why this selector should work>",
  "confidence": <integer 1-10, where 10 = very confident this selector is correct>
}}"""


class ClaudeHealer:
    """
    Uses the Claude API to suggest corrected CSS selectors when Playwright
    test steps fail due to selector mismatches.

    Requires ANTHROPIC_API_KEY to be set in the environment.
    Degrades gracefully if the API key is missing or if any call fails.

    Usage:
        healer = ClaudeHealer()
        html = await healer.get_page_html(page)
        suggestion = await healer.heal_step(failed_step, error_msg, html)
        if suggestion:
            # retry the step with suggestion["suggested_selector"]
    """

    def __init__(self) -> None:
        """
        Initialize the healer.

        Reads ANTHROPIC_API_KEY from environment. Logs a warning if not set
        (healing will return None for all calls).
        """
        self._api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

        if not self._api_key:
            logger.warning(
                "ClaudeHealer: ANTHROPIC_API_KEY not set. "
                "Selector healing is disabled."
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Public: heal_step
    # ─────────────────────────────────────────────────────────────────────────

    async def heal_step(
        self,
        failed_step: dict,
        error_message: str,
        page_html: str,
    ) -> dict | None:
        """
        Ask Claude to suggest a corrected CSS selector for a failed test step.

        Sends the failed step details, error message, and a truncated snapshot
        of the current page HTML to Claude. Returns the suggestion only if
        Claude's confidence score is >= HEALING_CONFIDENCE_THRESHOLD (7).

        Args:
            failed_step (dict): The step dict that failed. Expected keys:
                                  - step_number (int)
                                  - action (str)
                                  - expected_result (str)
                                  - selector_used (str, optional)
                                  - action_type (str, optional)
                                  - error (str, optional)
            error_message (str): The Playwright error text (e.g., "Timeout
                                  waiting for selector '#submit-btn'").
            page_html (str): Snapshot of the current page HTML. Use
                             get_page_html() to obtain a clean version.

        Returns:
            dict | None: Suggestion dict with keys:
                           - suggested_selector (str): The CSS selector to try.
                           - explanation (str): Why Claude chose this selector.
                           - confidence (int): Score 1-10.
                         Returns None if:
                           - Confidence < HEALING_CONFIDENCE_THRESHOLD
                           - Claude could not find a selector (null)
                           - API key not configured
                           - Any error occurred
        """
        if not self._api_key:
            logger.debug("heal_step: skipping — no API key")
            return None

        if not page_html:
            logger.debug("heal_step: skipping — no page HTML provided")
            return None

        try:
            # ── Build the prompt ───────────────────────────────────────────
            prompt = HEALING_PROMPT_TEMPLATE.format(
                action_type=failed_step.get("action_type", "unknown"),
                step_description=failed_step.get("action", ""),
                selector_used=failed_step.get("selector_used", "N/A"),
                error_message=error_message[:300],
                expected_result=failed_step.get("expected_result", ""),
                html_length=len(page_html),
                page_html=page_html,
            )

            # ── Call Claude API ────────────────────────────────────────────
            import anthropic  # Local import — not required at module load time

            client = anthropic.Anthropic(api_key=self._api_key)
            response = client.messages.create(
                model=self._model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )

            raw_text = response.content[0].text.strip()
            logger.debug("ClaudeHealer raw response: %s", raw_text[:200])

            # ── Parse the JSON response ────────────────────────────────────
            suggestion = self._parse_healing_response(raw_text)
            if suggestion is None:
                logger.warning("ClaudeHealer: could not parse JSON from response")
                return None

            # ── Confidence gate ────────────────────────────────────────────
            confidence = suggestion.get("confidence", 0)
            selector = suggestion.get("suggested_selector")

            if not selector:
                logger.info(
                    "ClaudeHealer: no selector suggestion for step %d",
                    failed_step.get("step_number", "?"),
                )
                return None

            if confidence < HEALING_CONFIDENCE_THRESHOLD:
                logger.info(
                    "ClaudeHealer: low confidence (%d/10) for step %d — not healing",
                    confidence,
                    failed_step.get("step_number", "?"),
                )
                return None

            logger.info(
                "ClaudeHealer: step %d healed with '%s' (confidence %d/10) — %s",
                failed_step.get("step_number", "?"),
                selector,
                confidence,
                suggestion.get("explanation", "")[:80],
            )
            return suggestion

        except Exception as e:  # noqa: BLE001
            logger.error("ClaudeHealer.heal_step error: %s", e)
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Public: get_page_html
    # ─────────────────────────────────────────────────────────────────────────

    async def get_page_html(
        self,
        page: "Page",
        max_chars: int = 1500,
    ) -> str:
        """
        Get a clean, truncated snapshot of the current page's HTML content.

        Fetches the full page HTML from Playwright, strips <script> and
        <style> tag content to reduce noise, and returns the first
        max_chars characters for use in the healing prompt.

        Args:
            page (Page): Active Playwright page object.
            max_chars (int): Maximum characters to return.
                             Default: 1500 (fits within a short prompt).

        Returns:
            str: Cleaned, truncated HTML string.
                 Empty string if page is None or any error occurs.
        """
        if page is None:
            logger.warning("get_page_html: page is None")
            return ""

        try:
            html = await page.content()

            # ── Strip <script> tags (content is not needed for selector work)
            html = re.sub(
                r"<script[^>]*>.*?</script>",
                "<script>…</script>",
                html,
                flags=re.DOTALL | re.IGNORECASE,
            )

            # ── Strip <style> tags ─────────────────────────────────────────
            html = re.sub(
                r"<style[^>]*>.*?</style>",
                "<style>…</style>",
                html,
                flags=re.DOTALL | re.IGNORECASE,
            )

            # ── Collapse repeated whitespace ──────────────────────────────
            html = re.sub(r"\s{3,}", "  ", html)

            return html[:max_chars]

        except Exception as e:  # noqa: BLE001
            logger.error("get_page_html error: %s", e)
            return ""

    # ─────────────────────────────────────────────────────────────────────────
    # Public: refresh_selectors
    # ─────────────────────────────────────────────────────────────────────────

    async def refresh_selectors(
        self,
        broken_selector: str,
        element_description: str,
        playwright_page: "Page",
        project: str | None = None,
        page_name: str | None = None,
    ) -> "str | None":
        """
        Ask Claude for a replacement selector and auto-update selectors.json.

        Called after a step is successfully healed (by heal_step) to persist
        the discovered fix for future test runs. If Claude returns a selector
        with confidence >= 8, selectors.json is updated automatically.

        Search order when writing the update:
          1. Find the section+key whose value equals broken_selector → update in place.
          2. If not found, append to the page_name section (or create it).

        Also bumps _meta.last_discovered timestamp if present.

        Args:
            broken_selector (str): The original CSS selector that failed.
            element_description (str): Human-readable name of the element.
            playwright_page (Page): Active Playwright page for DOM context.
            project (str | None): Project name for selectors.json lookup.
                Defaults to DEFAULT_PROJECT env var.
            page_name (str | None): Selector section to write into.
                                    Derived from current URL path if not provided.

        Returns:
            str | None: The new selector if auto-updated, or None if update was
                        skipped (low confidence, missing API key, or any error).
        """
        project = project or os.getenv("DEFAULT_PROJECT", "")
        if not self._api_key:
            logger.debug("refresh_selectors: skipping — no API key")
            return None

        if not playwright_page:
            logger.debug("refresh_selectors: skipping — no page provided")
            return None

        try:
            page_html = await self.get_page_html(playwright_page, max_chars=2000)
            current_url = playwright_page.url

            # ── Derive page_name from URL if not supplied ──────────────────
            if not page_name:
                from urllib.parse import urlparse
                url_path = urlparse(current_url).path.rstrip("/")
                page_name = url_path.split("/")[-1] if url_path else "general"

            prompt = (
                f"The CSS selector '{broken_selector}' for element '{element_description}'"
                f" is broken on page '{current_url}'.\n\n"
                f"Page HTML:\n{page_html}\n\n"
                "Suggest a reliable CSS selector that will work on this page.\n"
                "Respond with ONLY valid JSON (no markdown):\n"
                '{"selector": "<CSS_selector_string>", "confidence": <integer 1-10>}'
            )

            import anthropic
            client = anthropic.Anthropic(api_key=self._api_key)
            response = client.messages.create(
                model=self._model,
                max_tokens=128,
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()
            result = self._parse_healing_response(raw)
            if not result:
                logger.debug("refresh_selectors: could not parse Claude response")
                return None

            new_selector = result.get("selector")
            confidence = result.get("confidence", 0)

            if not new_selector or confidence < 8:
                logger.debug(
                    "refresh_selectors: confidence %s too low for '%s' — skipping update",
                    confidence, element_description,
                )
                return None

            # ── Locate and update selectors.json ───────────────────────────
            config_dir = Path(__file__).parent.parent / "config"
            selectors_path = (
                config_dir / "projects" / project / "selectors.json"
            )
            if not selectors_path.exists():
                logger.debug(
                    "refresh_selectors: selectors.json not found at %s", selectors_path
                )
                return None

            with open(selectors_path, encoding="utf-8") as f:
                selectors_data = json.load(f)

            updated = False
            # Search by value — find wherever broken_selector is used
            for section, entries in selectors_data.items():
                if section.startswith("_"):
                    continue
                if isinstance(entries, dict):
                    for key, val in entries.items():
                        if val == broken_selector:
                            selectors_data[section][key] = new_selector
                            updated = True
                            break
                if updated:
                    break

            # If not found by value, add to the page_name section
            if not updated:
                norm_key = element_description.lower().replace(" ", "_")[:40]
                if page_name not in selectors_data:
                    selectors_data[page_name] = {}
                selectors_data[page_name][norm_key] = new_selector

            # Bump _meta timestamp if present
            if "_meta" in selectors_data and isinstance(selectors_data["_meta"], dict):
                from datetime import datetime, timezone
                selectors_data["_meta"]["last_discovered"] = (
                    datetime.now(timezone.utc).isoformat()
                )

            with open(selectors_path, "w", encoding="utf-8") as f:
                json.dump(selectors_data, f, indent=2, ensure_ascii=False)

            logger.info(
                "Auto-updated selector: %s → %s", broken_selector, new_selector
            )
            return new_selector

        except Exception as e:  # noqa: BLE001
            logger.error("refresh_selectors error: %s", e)
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_healing_response(self, raw_text: str) -> dict | None:
        """
        Parse the JSON response from Claude's healing prompt.

        Handles common issues: markdown fences, leading/trailing whitespace,
        and partial JSON. Returns None if parsing fails after all attempts.

        Args:
            raw_text (str): Raw text response from Claude.

        Returns:
            dict | None: Parsed suggestion dict, or None on parse failure.
        """
        # ── Try direct parse ───────────────────────────────────────────────
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            pass

        # ── Strip markdown fences ──────────────────────────────────────────
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # ── Extract first JSON object ──────────────────────────────────────
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return None
