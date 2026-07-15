"""
dom_analyzer.py — AI-Powered DOM Selector Discovery
=====================================================
C1: Uses Playwright to open a page, extracts all interactive elements,
then sends a summary to Claude which identifies semantic CSS selectors.

Replaces the manual "fill in selectors.json" workflow — the agent
discovers and names selectors automatically from a live running app.

Usage:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()
        analyzer = DOMAnalyzer()
        selectors = await analyzer.discover_selectors(
            page_url="https://app.example.com/checkout",
            page_name="checkout",
            playwright_page=page,
        )
        analyzer.save_selectors("checkout", selectors, project="example")
"""

import base64
import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Playwright availability guard ─────────────────────────────────────────────
try:
    from playwright.async_api import Page as PlaywrightPage
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    PlaywrightPage = None   # type: ignore[assignment,misc]

# ── Interactive element CSS query ──────────────────────────────────────────────
INTERACTIVE_SELECTOR = (
    "button, input, select, textarea, "
    "a[href], [onclick], [role='button'], "
    "[role='textbox'], form"
)

# ── Maximum elements to process (keep prompt reasonable) ──────────────────────
MAX_ELEMENTS = 80

# ── Config dir for saving selectors ───────────────────────────────────────────
CONFIG_DIR = Path(__file__).parent.parent / "config"


class DOMAnalyzer:
    """
    Discovers CSS selectors for a live page by extracting its interactive
    elements and sending a summary to Claude for semantic labelling.

    Requires:
        - Playwright installed (pip install playwright && playwright install)
        - ANTHROPIC_API_KEY in .env
        - APP_BASE_URL / APP_USERNAME / APP_PASSWORD for login support

    All methods degrade gracefully if dependencies are missing.
    """

    def __init__(self) -> None:
        """
        Initialize the DOMAnalyzer.

        Loads APP_BASE_URL, APP_USERNAME, APP_PASSWORD, APP_LOGIN_URL from
        the .env file. Reads ANTHROPIC_API_KEY for Claude calls.
        Logs a warning (but never crashes) if any value is missing.
        """
        load_dotenv(override=True)

        self.base_url = os.getenv("APP_BASE_URL", "").rstrip("/")
        self.username = os.getenv("APP_USERNAME", "")
        self.password = os.getenv("APP_PASSWORD", "")
        self.login_url = os.getenv("APP_LOGIN_URL", "").rstrip("/") or (
            f"{self.base_url}/login" if self.base_url else ""
        )
        self._api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._model = os.getenv(
            "CLAUDE_MODEL_HEAVY", "claude-sonnet-4-5"
        )

        if not self._api_key:
            logger.warning(
                "QAX DOMAnalyzer: ANTHROPIC_API_KEY not set — "
                "selector discovery will be unavailable."
            )
        if not self.base_url:
            logger.warning(
                "QAX DOMAnalyzer: APP_BASE_URL not set in .env. "
                "Discovery will still work if you pass the full URL directly."
            )

    # ─────────────────────────────────────────────────────────────────────────
    # C1 — Main Discovery Method
    # ─────────────────────────────────────────────────────────────────────────

    async def discover_selectors(
        self,
        page_url: str,
        page_name: str,
        playwright_page: "PlaywrightPage",
    ) -> dict:
        """
        Navigate to a page and discover semantic CSS selectors using Claude.

        Steps:
          1. Navigate to page_url and wait for networkidle
          2. Auto-login if a login form is detected (login_if_needed)
          3. Extract all interactive elements from the DOM
          4. Build an element summary string
          5. Send to Claude to label elements with semantic names
          6. Parse and return the selector dict

        Args:
            page_url (str): Full URL of the page to analyze.
            page_name (str): Human-readable name for the page (e.g., "checkout").
            playwright_page (Page): An active Playwright Page instance.
                                    Must already be initialized (browser open).

        Returns:
            dict: {semantic_name: css_selector, ...}
                  Empty dict if anything fails.
        """
        if not PLAYWRIGHT_AVAILABLE:
            logger.warning(
                "QAX DOMAnalyzer: Playwright not installed — "
                "install with: pip install playwright && playwright install"
            )
            return {}

        if not self._api_key:
            logger.warning(
                "QAX DOMAnalyzer: ANTHROPIC_API_KEY not set — "
                "cannot call Claude for selector labelling."
            )
            return {}

        try:
            # ── Step 1: Navigate ───────────────────────────────────────────
            logger.info("QAX DOMAnalyzer: Navigating to %s", page_url)
            await playwright_page.goto(page_url, wait_until="networkidle", timeout=30_000)

            # ── Step 2: Auto-login if needed ───────────────────────────────
            await self.login_if_needed(playwright_page)

            # ── Step 3: Extract interactive elements ───────────────────────
            elements_summary = await self._extract_elements_summary(playwright_page)

            if not elements_summary.strip():
                logger.warning(
                    "QAX DOMAnalyzer: No interactive elements found on %s", page_url
                )
                return {}

            logger.info(
                "QAX DOMAnalyzer: Found interactive elements on '%s' — "
                "sending to Claude for labelling",
                page_name,
            )

            # ── Step 4-5: Ask Claude to label the elements ─────────────────
            selectors = await self._ask_claude_for_selectors(
                page_name, elements_summary
            )

            logger.info(
                "QAX DOMAnalyzer: Discovered %d selector(s) for page '%s'",
                len(selectors), page_name,
            )
            return selectors

        except Exception as e:  # noqa: BLE001
            logger.error(
                "QAX DOMAnalyzer: discover_selectors failed for %s: %s",
                page_url, e,
            )
            return {}

    # ─────────────────────────────────────────────────────────────────────────
    # C1 — Auto-login
    # ─────────────────────────────────────────────────────────────────────────

    async def login_if_needed(
        self,
        playwright_page: "PlaywrightPage",
    ) -> bool:
        """
        Detect if a login form is present and log in automatically.

        Heuristics:
          - Looks for input[type=password] on the current page
          - Or checks if the current URL contains "/login"

        If login credentials (APP_USERNAME, APP_PASSWORD) are not set,
        logs a warning and returns False.

        Args:
            playwright_page (Page): The active Playwright page.

        Returns:
            bool: True if login was attempted and appeared to succeed,
                  False if no login was needed or login failed.
        """
        if not playwright_page:
            return False

        try:
            current_url = playwright_page.url
            has_login_url = "/login" in current_url.lower()

            # Check for password input on page
            password_input = await playwright_page.query_selector(
                "input[type='password']"
            )

            if not has_login_url and not password_input:
                return False   # No login needed

            if not self.username or not self.password:
                logger.warning(
                    "QAX DOMAnalyzer: Login page detected but "
                    "APP_USERNAME / APP_PASSWORD not set in .env."
                )
                return False

            logger.info("QAX DOMAnalyzer: Login page detected — attempting auto-login")

            # ── Fill username ──────────────────────────────────────────────
            username_sel = (
                "input[name='username'], input[name='email'], "
                "input[type='email'], input[type='text']"
            )
            username_input = await playwright_page.query_selector(username_sel)
            if username_input:
                await username_input.fill(self.username)

            # ── Fill password ──────────────────────────────────────────────
            if password_input:
                await password_input.fill(self.password)

            # ── Submit the form ────────────────────────────────────────────
            submit_sel = "button[type='submit'], input[type='submit'], button.login-btn"
            submit_btn = await playwright_page.query_selector(submit_sel)
            if submit_btn:
                await submit_btn.click()
                await playwright_page.wait_for_load_state("networkidle", timeout=15_000)

            post_url = playwright_page.url
            success = "/login" not in post_url.lower()
            if success:
                logger.info("QAX DOMAnalyzer: Auto-login succeeded → %s", post_url)
            else:
                logger.warning(
                    "QAX DOMAnalyzer: Auto-login may have failed — still on login page"
                )
            return success

        except Exception as e:  # noqa: BLE001
            logger.warning(
                "QAX DOMAnalyzer: login_if_needed error (non-fatal): %s", e
            )
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # C1 — Save Discovered Selectors
    # ─────────────────────────────────────────────────────────────────────────

    def save_selectors(
        self,
        page_name: str,
        selectors: dict,
        project: str | None = None,
    ) -> None:
        """
        Merge discovered selectors into the project selectors.json file.

        Discovered selectors are added under the page_name key. Existing
        confirmed selectors (those NOT prefixed with "TODO") are never
        overwritten — only TODO placeholders are replaced.

        Updates the _meta.last_discovered timestamp.

        Args:
            page_name (str): Name of the page (e.g., "checkout", "login").
            selectors (dict): Discovered {semantic_name: selector} mapping.
            project (str | None): Project name for path resolution.
                Defaults to DEFAULT_PROJECT env var.
        """
        import os as _os
        project = project or _os.getenv("DEFAULT_PROJECT", "")
        if not selectors:
            logger.warning(
                "QAX DOMAnalyzer: No selectors to save for page '%s'", page_name
            )
            return

        selectors_path = (
            CONFIG_DIR / "projects" / project / "selectors.json"
        )
        selectors_path.parent.mkdir(parents=True, exist_ok=True)

        # ── Load existing selectors ────────────────────────────────────────
        existing: dict = {}
        if selectors_path.exists():
            try:
                with open(selectors_path, encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(
                    "QAX DOMAnalyzer: Could not read existing selectors: %s", e
                )

        # ── Update _meta ───────────────────────────────────────────────────
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if "_meta" not in existing or not isinstance(existing["_meta"], dict):
            existing["_meta"] = {
                "last_discovered": now,
                "auto_discovered": True,
                "manual_overrides": [],
            }
        else:
            existing["_meta"]["last_discovered"] = now
            existing["_meta"]["auto_discovered"] = True

        # ── Merge selectors for this page ──────────────────────────────────
        page_section = existing.get(page_name, {})
        merged_count = 0

        for name, selector in selectors.items():
            existing_val = page_section.get(name, "")
            # Only replace if the existing value is empty or a TODO placeholder
            if not existing_val or existing_val.upper().startswith("TODO"):
                page_section[name] = selector
                merged_count += 1
            else:
                logger.debug(
                    "QAX DOMAnalyzer: Preserving confirmed selector '%s.%s' = %s",
                    page_name, name, existing_val,
                )

        existing[page_name] = page_section

        # ── Write back ─────────────────────────────────────────────────────
        try:
            with open(selectors_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            logger.info(
                "QAX DOMAnalyzer: Saved %d new selector(s) for '%s' to %s",
                merged_count, page_name, selectors_path,
            )
        except IOError as e:
            logger.error(
                "QAX DOMAnalyzer: Could not save selectors: %s", e
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Private: element extraction
    # ─────────────────────────────────────────────────────────────────────────

    async def _extract_elements_summary(
        self,
        playwright_page: "PlaywrightPage",
    ) -> str:
        """
        Extract a text summary of all interactive elements on the current page.

        For each element collects: tag, id, name, type, class (first class),
        inner text (first 30 chars), aria-label, and placeholder.
        Formats each element as a single line for the Claude prompt.

        Args:
            playwright_page (Page): The active Playwright page.

        Returns:
            str: Multi-line text summary, one element per line.
        """
        try:
            elements = await playwright_page.query_selector_all(
                INTERACTIVE_SELECTOR
            )

            lines = []
            for el in elements[:MAX_ELEMENTS]:
                try:
                    tag      = (await el.evaluate("e => e.tagName")).lower()
                    el_id    = await el.get_attribute("id") or ""
                    el_class = await el.get_attribute("class") or ""
                    el_name  = await el.get_attribute("name") or ""
                    el_type  = await el.get_attribute("type") or ""
                    el_label = await el.get_attribute("aria-label") or ""
                    el_ph    = await el.get_attribute("placeholder") or ""
                    el_role  = await el.get_attribute("role") or ""
                    el_href  = await el.get_attribute("href") or ""

                    # Inner text (capped)
                    try:
                        text = (await el.inner_text()).strip()[:30]
                    except Exception:  # noqa: BLE001
                        text = ""

                    # Build compact descriptor
                    parts = [tag]
                    if el_id:
                        parts.append(f"#{el_id}")
                    if el_class:
                        first_class = el_class.split()[0] if el_class.split() else ""
                        if first_class:
                            parts.append(f".{first_class}")
                    if el_name:
                        parts.append(f"[name={el_name}]")
                    if el_type:
                        parts.append(f"[type={el_type}]")
                    if el_role:
                        parts.append(f"[role={el_role}]")
                    if el_href:
                        parts.append(f"[href={el_href[:40]}]")

                    attrs = []
                    if text:
                        attrs.append(f"text='{text}'")
                    if el_label:
                        attrs.append(f"aria-label='{el_label}'")
                    if el_ph:
                        attrs.append(f"placeholder='{el_ph}'")

                    line = "".join(parts)
                    if attrs:
                        line += ": " + ", ".join(attrs)
                    lines.append(line)

                except Exception:  # noqa: BLE001
                    continue

            return "\n".join(lines)

        except Exception as e:  # noqa: BLE001
            logger.warning(
                "QAX DOMAnalyzer: _extract_elements_summary error: %s", e
            )
            return ""

    # ─────────────────────────────────────────────────────────────────────────
    # Private: Claude call
    # ─────────────────────────────────────────────────────────────────────────

    async def _ask_claude_for_selectors(
        self,
        page_name: str,
        elements_summary: str,
    ) -> dict:
        """
        Send the elements summary to Claude and parse the returned selector map.

        Uses the assistant-prefill trick ({ prefix) to force a JSON object
        response without markdown fencing.

        Args:
            page_name (str): Page name for context in the prompt.
            elements_summary (str): Multi-line element descriptor text.

        Returns:
            dict: Parsed {semantic_name: css_selector} mapping.
                  Empty dict if the call fails or response is not parseable.
        """
        try:
            import anthropic

            prompt = (
                f"This is the DOM of the '{page_name}' page of a web application.\n\n"
                f"Interactive elements found:\n{elements_summary}\n\n"
                "Identify and return a JSON object where:\n"
                "  - Keys are semantic, snake_case names for each element\n"
                "    (e.g., submit_button, username_input, error_message)\n"
                "  - Values are the most stable CSS selector for each element\n"
                "    (prefer #id, then [name=x], then .specific-class)\n\n"
                "Focus on: forms, buttons, inputs, error/success messages, navigation.\n"
                "Ignore purely decorative or invisible elements.\n"
                "Return ONLY valid JSON, no text outside the JSON object."
            )

            client = anthropic.Anthropic(api_key=self._api_key)
            response = client.messages.create(
                model=self._model,
                max_tokens=1024,
                system="You are a QA automation expert specializing in CSS selector discovery.",
                messages=[
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "{"},   # prefill
                ],
            )

            raw = response.content[0].text.strip()

            # Reconstruct: prepend the "{" we used as prefill
            if raw.startswith("{"):
                json_text = raw
            else:
                json_text = "{" + raw

            result = self._parse_selector_response(json_text)
            return result

        except Exception as e:  # noqa: BLE001
            logger.error(
                "QAX DOMAnalyzer: Claude call failed: %s", e
            )
            return {}

    def _parse_selector_response(self, raw_text: str) -> dict:
        """
        Parse the JSON selector map from Claude's response.

        Handles markdown fences and partial JSON with multiple attempts.

        Args:
            raw_text (str): Raw response text from Claude.

        Returns:
            dict: Parsed selector map, or empty dict on failure.
        """
        # Attempt 1: direct parse
        try:
            result = json.loads(raw_text)
            if isinstance(result, dict):
                # Filter out non-string values and empty selectors
                return {
                    k: v for k, v in result.items()
                    if isinstance(v, str) and v.strip()
                }
        except json.JSONDecodeError:
            pass

        # Attempt 2: strip markdown fences
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())
        try:
            result = json.loads(cleaned)
            if isinstance(result, dict):
                return {k: v for k, v in result.items() if isinstance(v, str) and v.strip()}
        except json.JSONDecodeError:
            pass

        # Attempt 3: find first { ... } block
        match = re.search(r"\{[^{}]*\}", raw_text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                if isinstance(result, dict):
                    return {k: v for k, v in result.items() if isinstance(v, str) and v.strip()}
            except json.JSONDecodeError:
                pass

        logger.warning(
            "QAX DOMAnalyzer: Could not parse Claude's selector response"
        )
        return {}
