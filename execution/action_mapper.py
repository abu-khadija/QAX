"""
action_mapper.py — Natural Language Step → Playwright Action Mapper
=====================================================================
Translates human-readable test step descriptions into Playwright
browser actions. Works by analysing the text of each manual_step
and matching keywords to action types.

Selector resolution uses config/selectors/example_selectors.json.
When a selector is not found, the step is attempted with a best-guess
or marked as SKIP with an explanatory note.

Action detection rules (first match wins):
  "navigate" | "go to" | "open" | "visit"  → page.goto(url)
  "click" | "press" | "tap"                 → page.click(selector)
  "type" | "enter" | "fill" | "input"       → page.fill(selector, value)
  "select" | "choose" | "pick"              → page.select_option(selector, value)
  "verify" | "check" | "assert" | "should"
    | "must" | "ensure" | "confirm"          → assertion check
  "wait" | "pause"                          → page.wait_for_timeout(2000)
  anything else                             → SKIP with note
"""

import json
import logging
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

# ── Path to selectors file — resolved per project ─────────────────────────────
def _resolve_selectors_path(project: str | None = None) -> Path:
    """Return the selectors.json path for the given project (or env default)."""
    resolved = project or os.getenv("DEFAULT_PROJECT", "example")
    config_dir = Path(__file__).parent.parent / "config"
    new_path = config_dir / "projects" / resolved / "selectors.json"
    if new_path.exists():
        return new_path
    # Fall back to legacy location (generic name)
    legacy = config_dir / "selectors" / "selectors.json"
    return legacy

# ── Action keyword groups (order matters — more specific first) ───────────────
NAVIGATE_KEYWORDS = {"navigate", "go to", "goto", "open", "visit", "browse", "load"}
CLICK_KEYWORDS    = {"click", "press", "tap", "hit", "select the", "choose the"}
FILL_KEYWORDS     = {"type", "enter", "fill", "input", "write", "set"}
SELECT_KEYWORDS   = {"select from", "choose from", "pick from", "dropdown"}
ASSERT_KEYWORDS   = {"verify", "check", "assert", "should", "must", "ensure",
                     "confirm", "validate", "expect", "see", "visible", "displayed"}
WAIT_KEYWORDS     = {"wait", "pause", "delay", "sleep"}

# ── Stop words to ignore during selector name extraction ─────────────────────
STOP_WORDS = {
    "the", "a", "an", "and", "or", "on", "in", "at", "to", "for",
    "with", "is", "are", "was", "be", "of", "that", "this", "it",
    "button", "field", "input", "link", "tab", "page", "form",
    "text", "message", "label", "icon", "section", "panel",
}


class ActionMapper:
    """
    Maps human-readable test step descriptions to Playwright actions.

    Loads example_selectors.json at startup for CSS/XPath selector lookup.
    Detects the action type from the step's natural language description
    and executes the corresponding Playwright call.

    Usage:
        mapper = ActionMapper()
        result = await mapper.map_and_execute(step_dict, page)
    """

    def __init__(self, project: str | None = None) -> None:
        """
        Initialize the ActionMapper and load the selectors JSON.

        Args:
            project (str | None): Project name for selector file resolution.
                Reads DEFAULT_PROJECT from env if not provided.

        If the selectors file is missing or malformed, an empty dict
        is used — steps will proceed but selectors will be marked TODO.
        """
        self.project = project or os.getenv("DEFAULT_PROJECT", "example")
        self.selectors: dict = {}
        self._discovery_cache: dict = {}  # keyed by URL; caches DOMAnalyzer results per session
        self._load_selectors()

    # ─────────────────────────────────────────────────────────────────────────
    # Selector Loading
    # ─────────────────────────────────────────────────────────────────────────

    def _load_selectors(self) -> None:
        """
        Load CSS/XPath selectors from config/projects/{project}/selectors.json.

        Flattens the nested structure into a single dict keyed by
        "{section}.{key}" and "{key}" for fast lookup.

        E.g., { "login.username": "...", "username": "...", ... }
        """
        selectors_path = _resolve_selectors_path(self.project)
        if not selectors_path.exists():
            logger.warning(
                "Selectors file not found: %s — all selectors will be TODO",
                selectors_path
            )
            return

        try:
            with open(selectors_path, encoding="utf-8") as f:
                raw = json.load(f)

            # Flatten: add both "section.key" and "key" entries
            for section, entries in raw.items():
                # Skip metadata/comment keys (e.g. _meta, _comment, _instructions)
                if section.startswith("_"):
                    continue
                if isinstance(entries, dict):
                    for key, selector in entries.items():
                        self.selectors[f"{section}.{key}"] = selector
                        # Shorter key (may be overwritten by later sections — last wins)
                        self.selectors[key] = selector

            logger.debug(
                "Loaded %d selector(s) from %s", len(self.selectors), selectors_path
            )

        except (json.JSONDecodeError, IOError) as e:
            logger.error("Failed to load selectors: %s", e)

    # ─────────────────────────────────────────────────────────────────────────
    # Selector Lookup
    # ─────────────────────────────────────────────────────────────────────────

    def find_selector(self, element_name: str) -> "str | None":
        """
        Find a CSS/XPath selector for the given element name.

        Search order:
          1. Exact match on element_name (lowercased, underscored)
          2. Partial match: element_name appears in a key
          3. Any key containing the first meaningful word

        Args:
            element_name (str): Name of the UI element to find a selector for
                                (e.g., "save_button", "username", "submit").

        Returns:
            str | None: The selector string, or None if not found.
                        Note: TODO-prefixed selectors are returned as-is and
                        flagged by the caller.
        """
        if not element_name:
            return None

        normalized = element_name.lower().strip().replace(" ", "_").replace("-", "_")

        # ── 1. Exact match ─────────────────────────────────────────────────
        if normalized in self.selectors:
            return self.selectors[normalized]

        # ── 2. Partial match (key contains the element name) ───────────────
        for key, selector in self.selectors.items():
            if normalized in key.lower() or key.lower() in normalized:
                return selector

        # ── 3. First meaningful word match ─────────────────────────────────
        words = [w for w in re.split(r"[_\s]+", normalized) if w not in STOP_WORDS]
        if words:
            first_word = words[0]
            for key, selector in self.selectors.items():
                if first_word in key.lower():
                    return selector

        return None

    async def find_selector_with_fallback(
        self,
        element_name: str,
        playwright_page: "Page",
    ) -> "tuple[str | None, str]":
        """
        Find a CSS selector for an element using a 3-level fallback strategy.

        Level 1 — Static (fast): looks up config/projects/{project}/selectors.json.
        Level 2 — Dynamic (live): runs DOMAnalyzer on the current page URL.
                  Results are cached per-session (self._discovery_cache keyed by URL)
                  so the page is only queried once per URL during a test run.
        Level 3 — AI Visual (screenshot): takes a PNG screenshot of the page and
                  asks Claude to identify the CSS selector for the element.

        Args:
            element_name (str): Human-readable element name (e.g. "save_button").
            playwright_page (Page): Active Playwright page for Level 2 & 3 fallback.

        Returns:
            tuple[str | None, str]: (selector, level_description) where
                level_description is one of:
                  "Level 1 (static)"     — found in selectors.json
                  "Level 2 (discovered)" — found via live DOM discovery
                  "Level 3 (AI visual)"  — found via screenshot + Claude vision
                  "not found"            — all three levels exhausted
        """
        # ── Level 1: Static selectors.json ────────────────────────────────
        selector = self.find_selector(element_name)
        if selector and not selector.startswith("TODO"):
            logger.debug("Selector found: Level 1 (static) for '%s'", element_name)
            return selector, "Level 1 (static)"

        # ── Level 2: Live DOM discovery via DOMAnalyzer ────────────────────
        try:
            current_url = playwright_page.url if playwright_page else ""
            if current_url and current_url not in ("about:blank", ""):
                if current_url not in self._discovery_cache:
                    from core.dom_analyzer import DOMAnalyzer
                    analyzer = DOMAnalyzer()
                    # Derive page name from URL path
                    from urllib.parse import urlparse as _urlparse
                    url_path = _urlparse(current_url).path.rstrip("/")
                    page_name = url_path.split("/")[-1] if url_path else "page"
                    logger.debug("Level 2: discovering selectors for '%s'", current_url)
                    discovered = await analyzer.discover_selectors(
                        current_url, page_name, playwright_page
                    )
                    self._discovery_cache[current_url] = discovered
                else:
                    discovered = self._discovery_cache[current_url]

                # Search in discovered dict
                norm = element_name.lower().strip().replace(" ", "_").replace("-", "_")
                for key, sel in discovered.items():
                    if norm in key.lower() or key.lower() in norm:
                        logger.info(
                            "Selector found: Level 2 (discovered) for '%s': %s",
                            element_name, sel
                        )
                        return sel, "Level 2 (discovered)"
        except Exception as _l2_err:
            logger.debug("Level 2 fallback failed (non-fatal): %s", _l2_err)

        # ── Level 3: AI Visual — screenshot + Claude vision ────────────────
        try:
            if playwright_page:
                import base64
                import anthropic as _anthropic
                _api_key = os.getenv("ANTHROPIC_API_KEY", "")
                _model = os.getenv("CLAUDE_MODEL_HEAVY", "claude-sonnet-4-5")
                # A screenshot of the app goes to the AI provider: opt-in only.
                _opted_in = os.getenv("QAX_AI_HEAL", "false").strip().lower() == "true"
                if _api_key and _opted_in:
                    screenshot_bytes = await playwright_page.screenshot()
                    screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
                    client = _anthropic.Anthropic(api_key=_api_key)
                    response = client.messages.create(
                        model=_model,
                        max_tokens=100,
                        messages=[{
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": screenshot_b64,
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": (
                                        f"What is the CSS selector for the '{element_name}' element"
                                        " on this page? Return ONLY the selector string, nothing else."
                                    ),
                                },
                            ],
                        }],
                    )
                    ai_selector = response.content[0].text.strip()
                    if ai_selector and len(ai_selector) < 200:
                        logger.info(
                            "Selector found: Level 3 (AI visual) for '%s': %s",
                            element_name, ai_selector
                        )
                        return ai_selector, "Level 3 (AI visual)"
        except Exception as _l3_err:
            logger.debug("Level 3 fallback failed (non-fatal): %s", _l3_err)

        logger.warning(
            "Selector NOT found after 3 levels for '%s' — SKIP", element_name
        )
        return None, "not found"

    def _extract_element_name(self, action_text: str) -> str:
        """
        Extract the most likely UI element name from an action description.

        Tries quoted strings first (they are usually element labels):
          'Click the "Save Workflow" button' → "Save Workflow"

        Falls back to stripping stop words from the action text.

        Args:
            action_text (str): The full action description text.

        Returns:
            str: A normalised element name suitable for selector lookup.
        """
        # ── Try quoted string first ────────────────────────────────────────
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", action_text)
        if quoted:
            return quoted[0].lower().replace(" ", "_")

        # ── Strip action verbs and stop words, join remainder ──────────────
        words = action_text.lower().split()
        action_verbs = {
            "click", "press", "type", "enter", "fill", "navigate", "go",
            "select", "verify", "check", "assert", "wait", "open", "visit"
        }
        meaningful = [
            w for w in words
            if w not in STOP_WORDS and w not in action_verbs and len(w) > 2
        ]
        return "_".join(meaningful[:3]) if meaningful else action_text.lower()[:20]

    def _extract_url(self, action_text: str) -> str:
        """
        Extract a URL or URL path from an action description.

        Args:
            action_text (str): Action text that should contain a URL.

        Returns:
            str: Extracted URL/path, or the EXAMPLE base URL as fallback.
        """
        base_url = os.getenv("APP_BASE_URL", "http://localhost")

        # Look for quoted URL
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", action_text)
        for q in quoted:
            if "/" in q or q.startswith("http"):
                return q if q.startswith("http") else f"{base_url}{q}"

        # Look for bare URL patterns
        url_pattern = re.findall(r"(https?://\S+|/[A-Za-z0-9/_-]+)", action_text)
        if url_pattern:
            candidate = url_pattern[0]
            return candidate if candidate.startswith("http") else f"{base_url}{candidate}"

        return base_url

    def _extract_fill_value(self, action_text: str) -> str:
        """
        Extract the value to fill into an input field from an action description.

        E.g., 'Type "hello world" in the name field' → "hello world"

        Args:
            action_text (str): Action text describing a fill/type action.

        Returns:
            str: The value to type, or empty string if not determinable.
        """
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", action_text)
        # Return the last quoted string (usually the value, not the field name)
        return quoted[-1] if quoted else ""

    # ─────────────────────────────────────────────────────────────────────────
    # Action Detection
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_action_type(self, action_text: str) -> str:
        """
        Detect what type of Playwright action a step description implies.

        Checks keyword groups in priority order. Returns the first match.

        Args:
            action_text (str): The action description text (lowercased internally).

        Returns:
            str: Action type — one of: "navigate", "click", "fill", "select",
                 "assert", "wait", "skip".
        """
        text = action_text.lower()

        # Multi-word checks first (more specific)
        for keyword in {"go to", "navigate to", "select from", "choose from"}:
            if keyword in text:
                if "navigate" in keyword or "go to" in keyword:
                    return "navigate"
                return "select"

        # Single-word checks
        for keyword in NAVIGATE_KEYWORDS:
            if keyword in text:
                return "navigate"

        # "select" is tricky — could be select_option or just click
        # If "from" or "dropdown" follows, it's select_option, else click
        if any(k in text for k in {"select", "choose", "pick"}):
            if any(k in text for k in {"from", "dropdown", "option", "list"}):
                return "select"
            return "click"

        for keyword in CLICK_KEYWORDS:
            if keyword in text:
                return "click"

        for keyword in FILL_KEYWORDS:
            if keyword in text:
                return "fill"

        for keyword in ASSERT_KEYWORDS:
            if keyword in text:
                return "assert"

        for keyword in WAIT_KEYWORDS:
            if keyword in text:
                return "wait"

        return "skip"

    # ─────────────────────────────────────────────────────────────────────────
    # Main Execute Method
    # ─────────────────────────────────────────────────────────────────────────

    async def map_and_execute(
        self,
        step: dict,
        page: "Page",
        selector_override: "str | None" = None,
    ) -> dict:
        """
        Translate and execute one manual test step on the Playwright page.

        Detects the action type from the step text, looks up the CSS selector,
        and performs the Playwright action. Returns a structured result dict.

        Args:
            step (dict): A manual_steps entry with keys:
                - step_number (int)
                - action (str): Human-readable action description
                - expected_result (str): What should happen
                - notes (str, optional)
            page (Page): Active Playwright Page to execute on.
            selector_override (str | None): If set, use this CSS selector instead
                                            of looking one up. Used by ClaudeHealer
                                            when retrying with a suggested selector.

        Returns:
            dict: Execution result with keys:
                - step_number (int)
                - action_type (str): Detected action category
                - status (str): "PASS" | "FAIL" | "SKIP"
                - actual_result (str): What actually happened
                - error (str | None): Error message if failed
                - screenshot (str | None): Screenshot path if taken on failure
                - selector_used (str | None): The CSS selector that was used
        """
        import os as _os

        # Automation steps carry an explicit playwright_action and are dispatched
        # directly (no natural-language parsing). Manual steps — which have an
        # "action" description instead — fall through to the existing path below.
        if step.get("playwright_action"):
            return await self._execute_automation_step(step, page, selector_override)

        step_num = step.get("step_number", 0)
        action_text = step.get("action", "")
        expected = step.get("expected_result", "")

        base_result = {
            "step_number": step_num,
            "action_type": "unknown",
            "status": "FAIL",
            "actual_result": "",
            "error": None,
            "screenshot": None,
            "selector_used": None,
        }

        if not PLAYWRIGHT_AVAILABLE:
            return {
                **base_result,
                "status": "SKIP",
                "actual_result": "Playwright not installed — step skipped",
            }

        if page is None:
            return {
                **base_result,
                "status": "SKIP",
                "actual_result": "Browser page not available",
            }

        # ── Detect action type ─────────────────────────────────────────────
        action_type = self._detect_action_type(action_text)
        base_result["action_type"] = action_type

        try:
            if action_type == "navigate":
                url = self._extract_url(action_text)
                base_url = _os.getenv("APP_BASE_URL", "http://localhost")
                full_url = url if url.startswith("http") else f"{base_url}{url}"
                await page.goto(full_url, wait_until="load", timeout=30_000)
                current_url = page.url
                base_result.update({
                    "status": "PASS",
                    "actual_result": f"Navigated to: {current_url}",
                    "selector_used": None,
                })

            elif action_type == "click":
                element_name = self._extract_element_name(action_text)
                if selector_override:
                    selector = selector_override
                    level = "override"
                else:
                    selector, level = await self.find_selector_with_fallback(element_name, page)
                    selector = selector or f"[data-testid='{element_name}']"
                base_result["selector_used"] = selector
                logger.debug("click selector (%s): %s", level, selector)
                await page.click(selector, timeout=15_000)
                base_result.update({
                    "status": "PASS",
                    "actual_result": f"Clicked element: {selector}",
                })

            elif action_type == "fill":
                element_name = self._extract_element_name(action_text)
                value = self._extract_fill_value(action_text)
                if selector_override:
                    selector = selector_override
                    level = "override"
                else:
                    selector, level = await self.find_selector_with_fallback(element_name, page)
                    selector = selector or f"input[name='{element_name}']"
                base_result["selector_used"] = selector
                logger.debug("fill selector (%s): %s", level, selector)
                await page.fill(selector, value, timeout=15_000)
                base_result.update({
                    "status": "PASS",
                    "actual_result": f"Filled '{selector}' with value: '{value}'",
                })

            elif action_type == "select":
                element_name = self._extract_element_name(action_text)
                value = self._extract_fill_value(action_text)
                if selector_override:
                    selector = selector_override
                    level = "override"
                else:
                    selector, level = await self.find_selector_with_fallback(element_name, page)
                    selector = selector or f"select[name='{element_name}']"
                base_result["selector_used"] = selector
                logger.debug("select selector (%s): %s", level, selector)
                await page.select_option(selector, label=value or None, timeout=15_000)
                base_result.update({
                    "status": "PASS",
                    "actual_result": f"Selected '{value}' in: {selector}",
                })

            elif action_type == "assert":
                # Run assertion based on expected_result text
                result = await self._run_assertion(page, action_text, expected)
                base_result.update(result)

            elif action_type == "wait":
                await page.wait_for_timeout(2000)
                base_result.update({
                    "status": "PASS",
                    "actual_result": "Waited 2000ms",
                })

            else:  # skip
                base_result.update({
                    "status": "SKIP",
                    "actual_result": f"Action type not recognised — skipped. Text: '{action_text[:60]}'",
                })

        except Exception as e:  # noqa: BLE001
            base_result.update({
                "status": "FAIL",
                "actual_result": f"Step execution failed",
                "error": str(e)[:300],
            })

        return base_result

    async def _execute_automation_step(
        self,
        step: dict,
        page: "Page",
        selector_override: "str | None" = None,
    ) -> dict:
        """
        Execute a single automation_steps entry directly on the Playwright page.

        Unlike the manual-step path, automation steps already carry an explicit
        playwright_action, a selector_key, and a value — so no natural-language
        parsing is needed. The action-name groups mirror APIRunner.execute_step()
        (execution/api_runner.py) so both execution modes agree on what each
        playwright_action means.

        Args:
            step (dict): An automation_steps entry with keys:
                - step_number (int)
                - playwright_action (str): navigate | click | fill | select | wait | assert
                - selector_key (str): key resolved against the project selectors.json
                - value (str): text to fill, URL to visit, or option to select
                - assertion_type (str, optional): "url" | "text" | "visible"
                - assertion_value (str, optional): expected value for the assertion
            page (Page): Active Playwright Page to execute on.
            selector_override (str | None): CSS selector supplied by ClaudeHealer
                                            on a healing retry.

        Returns:
            dict: Same result shape as map_and_execute() — step_number,
                  action_type, status, actual_result, error, screenshot,
                  selector_used.
        """
        import os as _os

        step_num       = step.get("step_number", 0)
        action         = (step.get("playwright_action") or "").strip().lower()
        selector_key   = step.get("selector_key") or ""
        value          = step.get("value") or ""
        assertion_type = (step.get("assertion_type") or "").strip().lower()
        assertion_val  = step.get("assertion_value") or ""

        base_result = {
            "step_number": step_num,
            "action_type": action or "unknown",
            "status": "FAIL",
            "actual_result": "",
            "error": None,
            "screenshot": None,
            "selector_used": None,
        }

        if not PLAYWRIGHT_AVAILABLE:
            return {
                **base_result,
                "status": "SKIP",
                "actual_result": "Playwright not installed — step skipped",
            }

        if page is None:
            return {
                **base_result,
                "status": "SKIP",
                "actual_result": "Browser page not available",
            }

        async def _resolve_selector() -> str:
            """Resolve selector_key → CSS via override, then the 3-level fallback."""
            if selector_override:
                return selector_override
            selector, level = await self.find_selector_with_fallback(selector_key, page)
            logger.debug("automation selector (%s) for '%s': %s", level, selector_key, selector)
            return selector or f"[data-testid='{selector_key}']"

        try:
            if action in ("navigate", "goto", "open", "visit"):
                target = value or assertion_val
                base_url = _os.getenv("APP_BASE_URL", "http://localhost")
                full_url = target if target.startswith("http") else f"{base_url}{target}"
                await page.goto(full_url, wait_until="load", timeout=30_000)
                base_result.update({
                    "status": "PASS",
                    "actual_result": f"Navigated to: {page.url}",
                })

            elif action in ("click", "press", "tap", "submit"):
                selector = await _resolve_selector()
                base_result["selector_used"] = selector
                await page.click(selector, timeout=15_000)
                base_result.update({
                    "status": "PASS",
                    "actual_result": f"Clicked element: {selector}",
                })

            elif action in ("fill", "type", "enter", "input", "set_value"):
                selector = await _resolve_selector()
                base_result["selector_used"] = selector
                await page.fill(selector, value, timeout=15_000)
                base_result.update({
                    "status": "PASS",
                    "actual_result": f"Filled '{selector}' with value: '{value}'",
                })

            elif action in ("select", "choose", "pick"):
                selector = await _resolve_selector()
                base_result["selector_used"] = selector
                await page.select_option(selector, label=value or None, timeout=15_000)
                base_result.update({
                    "status": "PASS",
                    "actual_result": f"Selected '{value}' in: {selector}",
                })

            elif action in ("wait", "pause"):
                await page.wait_for_timeout(2000)
                base_result.update({
                    "status": "PASS",
                    "actual_result": "Waited 2000ms",
                })

            elif action in ("assert", "verify", "check", "expect"):
                # Assertion-only step: evaluate directly against the current page.
                selector = await _resolve_selector() if selector_key else None
                base_result["selector_used"] = selector
                base_result.update(
                    await self._check_automation_assertion(
                        page, assertion_type, assertion_val, selector
                    )
                )

            else:
                base_result.update({
                    "status": "SKIP",
                    "actual_result": f"Action '{action}' not recognised — skipped",
                })

            # ── Optional post-action assertion ─────────────────────────────
            # Automation steps often pair an action with a check (e.g. click +
            # assertion_type="visible"). When the action passed and a separate
            # assertion is declared, enforce it so a green result means something.
            if (
                base_result["status"] == "PASS"
                and assertion_type
                and action not in ("assert", "verify", "check", "expect")
            ):
                check = await self._check_automation_assertion(
                    page, assertion_type, assertion_val, base_result["selector_used"]
                )
                if check["status"] != "PASS":      # FAIL, or a check that never ran
                    base_result.update(check)

        except Exception as e:  # noqa: BLE001
            base_result.update({
                "status": "FAIL",
                "actual_result": "Step execution failed",
                "error": str(e)[:300],
            })

        return base_result

    async def _check_automation_assertion(
        self,
        page: "Page",
        assertion_type: str,
        assertion_value: str,
        selector: "str | None",
    ) -> dict:
        """
        Evaluate a structured assertion from an automation step.

        Supports the assertion_type values the generator emits:
          - "url"     → assertion_value must appear in the current URL
          - "text"    → assertion_value must appear in the selector's inner text
          - "visible" → the selector's element must exist and be visible

        When an assertion cannot be evaluated (no expected value, unresolved
        TODO selector, or unknown type), it passes with a note rather than
        failing the step — the same permissive stance the manual assertion path
        takes, so unfilled selectors don't produce false failures.

        Args:
            page (Page): Active Playwright Page.
            assertion_type (str): One of "url" | "text" | "visible" (others noted).
            assertion_value (str): Expected value for the assertion (may be empty).
            selector (str | None): Resolved CSS selector for text/visible checks.

        Returns:
            dict: Partial result with status / actual_result / error keys.
        """
        try:
            if assertion_type == "url":
                if not assertion_value:
                    return {"status": "SKIP",
                            "actual_result": f"URL assertion not evaluated — no expected value (URL is {page.url})"}
                if assertion_value.lower() in page.url.lower():
                    return {"status": "PASS", "actual_result": f"URL contains '{assertion_value}'"}
                return {
                    "status": "FAIL",
                    "actual_result": f"URL '{page.url}' missing '{assertion_value}'",
                    "error": "URL assertion failed",
                }

            # text / visible need a resolvable selector. Treat any unfilled TODO
            # placeholder as unresolvable — this catches both a raw "TODO..." value
            # from selectors.json and a guessed "[data-testid='TODO_*']" fallback,
            # so an assert-only step on an unfilled selector skips gracefully
            # instead of emitting a false "element not found" failure.
            if not selector or "TODO" in selector:
                return {
                    "status": "SKIP",
                    "actual_result": f"Assertion '{assertion_type}' not evaluated — no resolvable selector",
                }

            element = await page.query_selector(selector)
            if not element:
                return {
                    "status": "FAIL",
                    "actual_result": f"Element not found for assertion: {selector}",
                    "error": f"{assertion_type} assertion failed",
                }

            if assertion_type == "visible":
                if await element.is_visible():
                    return {"status": "PASS", "actual_result": f"Element visible: {selector}"}
                return {
                    "status": "FAIL",
                    "actual_result": f"Element present but not visible: {selector}",
                    "error": "Visibility assertion failed",
                }

            if assertion_type == "text":
                actual = await element.inner_text()
                if not assertion_value:
                    return {"status": "SKIP",
                            "actual_result": f"Text assertion not evaluated — no expected value (text: '{actual[:80]}')"}
                if assertion_value.lower() in actual.lower():
                    return {"status": "PASS", "actual_result": f"Text OK: '{actual[:80]}'"}
                return {
                    "status": "FAIL",
                    "actual_result": f"Text mismatch. Expected '{assertion_value}', got '{actual[:80]}'",
                    "error": "Text assertion failed",
                }

            return {"status": "SKIP",
                    "actual_result": f"Assertion type '{assertion_type}' not supported — not evaluated"}

        except Exception as e:  # noqa: BLE001
            # Never let an assertion crash the step — but an assertion that could not
            # run proved nothing, so it is an ERROR, never a PASS.
            return {
                "status": "ERROR",
                "actual_result": f"Assertion '{assertion_type}' could not be evaluated",
                "error": str(e)[:200],
            }

    async def _run_assertion(
        self,
        page: "Page",
        action_text: str,
        expected_result: str,
    ) -> dict:
        """
        Run an assertion check on the current page state.

        Detects assertion type from the text (URL, text content, visibility)
        and uses the appropriate Playwright assertion method.

        Args:
            page (Page): Active Playwright Page.
            action_text (str): The assertion action description.
            expected_result (str): What the expected result should be.

        Returns:
            dict: Partial result dict with status, actual_result, error keys.
        """
        text = action_text.lower()

        try:
            # URL assertion
            if "url" in text or "page" in text and "address" in text:
                current_url = page.url
                expected_fragment = self._extract_fill_value(expected_result) or expected_result[:50]
                if expected_fragment.lower() in current_url.lower():
                    return {"status": "PASS", "actual_result": f"URL contains '{expected_fragment}': {current_url}"}
                return {
                    "status": "FAIL",
                    "actual_result": f"URL mismatch. Expected fragment: '{expected_fragment}', got: '{current_url}'",
                    "error": "URL assertion failed",
                }

            # Text content assertion
            if "text" in text or "message" in text or "title" in text:
                element_name = self._extract_element_name(action_text)
                selector = self.find_selector(element_name)
                if selector and not selector.startswith("TODO"):
                    element = await page.query_selector(selector)
                    if element:
                        actual_text = await element.inner_text()
                        expected_text = self._extract_fill_value(expected_result) or expected_result[:50]
                        if expected_text.lower() in actual_text.lower():
                            return {"status": "PASS", "actual_result": f"Text found: '{actual_text[:100]}'"}
                        return {
                            "status": "FAIL",
                            "actual_result": f"Text mismatch. Expected: '{expected_text}', got: '{actual_text[:100]}'",
                            "error": "Text assertion failed",
                        }

            # Visibility / existence assertion
            element_name = self._extract_element_name(action_text)
            selector = self.find_selector(element_name)
            if selector and not selector.startswith("TODO"):
                element = await page.query_selector(selector)
                if element:
                    is_visible = await element.is_visible()
                    if is_visible:
                        return {"status": "PASS", "actual_result": f"Element visible: {selector}"}
                    return {
                        "status": "FAIL",
                        "actual_result": f"Element found but not visible: {selector}",
                        "error": "Visibility assertion failed",
                    }

            # Can't determine the assertion — it was not checked, so it is not a pass
            return {
                "status": "SKIP",
                "actual_result": (
                    f"Assertion not evaluated (no selector for '{element_name}'). "
                    "Add it to the project's selectors.json."
                ),
            }

        except Exception as e:  # noqa: BLE001
            return {
                "status": "FAIL",
                "actual_result": "Assertion check failed with exception",
                "error": str(e)[:200],
            }


# ── Module-level import for os (used in map_and_execute) ─────────────────────
import os  # noqa: E402 — imported here to keep it near usage
