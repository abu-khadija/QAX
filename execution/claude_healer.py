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
        from core.ai_provider import resolved_key
        self._api_key = resolved_key()
        self._model = os.getenv("CLAUDE_MODEL") or os.getenv("CLAUDE_MODEL_HEAVY", "claude-sonnet-4-5")

        if not self._api_key:
            logger.warning(
                "ClaudeHealer: no AI provider configured. "
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

            from core.ai_provider import make_ai_client
            client = make_ai_client(api_key=self._api_key)
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
    # Public: persist_selector
    # ─────────────────────────────────────────────────────────────────────────

    def persist_selector(self, broken_selector: str, healed_selector: str, project: str | None = None) -> bool:
        """
        Write the selector that just WORKED over the broken one in the project's selectors.json.

        No AI call: heal_step already found `healed_selector` and the retry proved it. (refresh_selectors used to
        ask the AI again after the step, when the page had already moved on, and stored an unrelated element:
        a login button became the dashboard's "add note" button.) Only an entry whose value IS the broken
        selector is changed; nothing is invented when none matches.

        Args:
            broken_selector (str): The selector that failed.
            healed_selector (str): The selector that passed on retry.
            project (str | None): Project for selectors.json; default DEFAULT_PROJECT.

        Returns:
            bool: True when an entry was updated.
        """
        project = project or os.getenv("DEFAULT_PROJECT", "")
        if not (broken_selector and healed_selector and project) or broken_selector == healed_selector:
            return False
        try:
            from core.paths import user_config_dir
            path = user_config_dir() / "projects" / project / "selectors.json"
            if not path.exists():
                return False
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            updated = False
            for section, entries in data.items():
                if section.startswith("_") or not isinstance(entries, dict):
                    continue
                for key, val in entries.items():
                    if val == broken_selector:
                        entries[key] = healed_selector
                        updated = True
            if not updated:
                return False
            if isinstance(data.get("_meta"), dict):
                from datetime import datetime, timezone
                data["_meta"]["last_discovered"] = datetime.now(timezone.utc).isoformat()
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
            logger.info("Auto-updated selector: %s → %s", broken_selector, healed_selector)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("persist_selector error: %s", e)
            return False

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
