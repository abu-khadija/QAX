"""
screenshot_capture.py — Playwright Screenshot Capture
=======================================================
Captures screenshots of app UI pages using Playwright's async API.
Screenshots are passed to Claude as base64 images so it can see
exact UI element names, button labels, and field names — eliminating
the need_review flag for visible UI elements.

This module is OPTIONAL — if screenshot capture fails for any reason,
the main pipeline continues without a screenshot. Never crashes the flow.

Install Playwright browsers once with:
    playwright install chromium
"""

import base64
import logging
import os
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Module-to-URL path mapping ─────────────────────────────────────────────────
# Loaded at import time from the active project's selectors.json
# under the "_module_urls" key.  Falls back to an empty dict so the
# caller can still provide a manual URL via capture_module(url=...).
#
# To configure for your project, add to config/projects/<project>/selectors.json:
#   "_module_urls": {
#       "MyModule": "/MyModule/Home/Index",
#       ...
#   }

def _load_module_url_map() -> dict:
    """
    Load module → URL-path mapping from the active project's selectors.json.

    Reads the "_module_urls" section from
    config/projects/<DEFAULT_PROJECT>/selectors.json.
    Returns an empty dict if the section is absent or the file cannot be read.
    """
    import json as _json
    project = os.getenv("DEFAULT_PROJECT", "")
    if not project:
        return {}
    config_path = (
        Path(__file__).parent.parent
        / "config" / "projects" / project / "selectors.json"
    )
    if not config_path.exists():
        return {}
    try:
        data = _json.loads(config_path.read_text(encoding="utf-8"))
        return data.get("_module_urls", {})
    except Exception as exc:  # noqa: BLE001
        logger.debug("QAX ScreenshotCapture: could not load _module_urls: %s", exc)
        return {}


MODULE_URL_MAP: dict = _load_module_url_map()


class ScreenshotCapture:
    """
    Captures app UI screenshots using Playwright's async browser automation.

    Used to give Claude visual context so it can see exact button labels
    and field names rather than guessing from backend code alone.

    Usage (async):
        capture = ScreenshotCapture()
        screenshot_b64 = await capture.capture_module("Orders", base_url, user, pwd)
        # Pass screenshot_b64 to claude.analyze_code(screenshot_base64=screenshot_b64)
    """

    def __init__(self):
        """
        Initialize the screenshot capture client.
        Reads credentials from environment variables (loaded via .env).
        """
        self.base_url = os.getenv("APP_BASE_URL", "").rstrip("/")
        self.username = os.getenv("APP_USERNAME", "")
        self.password = os.getenv("APP_PASSWORD", "")
        self.enabled = os.getenv("ENABLE_SCREENSHOT", "false").lower() == "true"

    async def capture_module(
        self,
        module_name: str,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> str | None:
        """
        Capture a screenshot of a EXAMPLE module page and return it as base64.

        Opens a headless Chromium browser, logs in to the app, navigates to
        the module page, and takes a full-page screenshot. Returns None on
        any failure so the calling code can continue without the screenshot.

        Args:
            module_name (str): Module name (e.g., "Orders", "Reports").
                               Must match a key in MODULE_URL_MAP.
            base_url (str | None): App base URL. Falls back to APP_BASE_URL env var.
            username (str | None): Login username. Falls back to APP_USERNAME env var.
            password (str | None): Login password. Falls back to APP_PASSWORD env var.

        Returns:
            str | None: Base64-encoded PNG screenshot string, or None if capture failed.
        """
        # ── Check if screenshot capture is enabled ────────────────────────────
        if not self.enabled:
            logger.debug("Screenshot capture disabled (ENABLE_SCREENSHOT=false)")
            return None

        # Use provided args or fall back to env vars
        url = (base_url or self.base_url).rstrip("/")
        user = username or self.username
        pwd = password or self.password

        if not url or not user or not pwd:
            logger.warning(
                "Screenshot skipped — missing APP_BASE_URL, APP_USERNAME, or APP_PASSWORD in .env"
            )
            return None

        # Get the module page path
        module_path = MODULE_URL_MAP.get(module_name, "/")
        target_url = f"{url}{module_path}"

        try:
            # Import Playwright here so the app doesn't crash if it's not installed
            from playwright.async_api import async_playwright

            logger.info("Capturing screenshot of %s at %s...", module_name, target_url)

            async with async_playwright() as playwright:
                # Launch headless Chromium (invisible browser window)
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    ignore_https_errors=True,   # Allow self-signed certs in test env
                )
                page = await context.new_page()

                # ── Step 1: Log in to EXAMPLE ────────────────────────────────────
                login_success = await self.login(page, url, user, pwd)
                if not login_success:
                    logger.warning("Screenshot login failed — proceeding without screenshot")
                    await browser.close()
                    return None

                # ── Step 2: Navigate to the module page ───────────────────────
                logger.debug("Navigating to module page: %s", target_url)
                await page.goto(target_url, wait_until="networkidle", timeout=15000)

                # ── Step 3: Take screenshot ───────────────────────────────────
                screenshot_bytes = await page.screenshot(full_page=False)
                await browser.close()

                # ── Step 4: Encode to base64 ──────────────────────────────────
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                logger.info(
                    "Screenshot captured successfully (%d bytes)",
                    len(screenshot_bytes)
                )
                return screenshot_b64

        except ImportError:
            logger.warning(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )
            return None

        except Exception as e:
            # Never crash the main flow — screenshot is optional
            logger.warning("Screenshot capture failed (non-critical): %s", str(e))
            return None

    async def login(self, page, base_url: str, username: str, password: str) -> bool:
        """
        Log in to EXAMPLE using the provided credentials.

        Navigate to the login page and fill in the username and password fields.
        Returns True if login appears successful, False otherwise.

        Args:
            page: Playwright Page object (already created).
            base_url (str): EXAMPLE base URL (e.g., https://your-app.example.com).
            username (str): Login username or email.
            password (str): Login password.

        Returns:
            bool: True if login succeeded, False if it failed or timed out.

        TODO: Update the selectors below to match the actual EXAMPLE login page.
              To find selectors: open EXAMPLE in Chrome → F12 → click on the
              username field → right click in Elements → Copy → Copy selector.
        """
        try:
            login_url = f"{base_url}/Account/Login"
            logger.debug("Navigating to login page: %s", login_url)
            await page.goto(login_url, wait_until="domcontentloaded", timeout=15000)

            # TODO: Replace with the actual CSS selector for the username/email input field
            # Example: "#UserName", "input[name='username']", "input[type='email']"
            USERNAME_SELECTOR = "#UserName"   # TODO: Confirm this selector in EXAMPLE login page

            # TODO: Replace with the actual CSS selector for the password input field
            # Example: "#Password", "input[name='password']", "input[type='password']"
            PASSWORD_SELECTOR = "#Password"   # TODO: Confirm this selector in EXAMPLE login page

            # TODO: Replace with the actual CSS selector for the login/submit button
            # Example: "button[type='submit']", "#loginBtn", ".btn-login"
            SUBMIT_SELECTOR = "button[type='submit']"   # TODO: Confirm this selector

            # Fill in credentials
            await page.fill(USERNAME_SELECTOR, username)
            await page.fill(PASSWORD_SELECTOR, password)
            await page.click(SUBMIT_SELECTOR)

            # Wait for navigation after login
            await page.wait_for_load_state("networkidle", timeout=10000)

            # Check if login succeeded by verifying we're no longer on the login page
            current_url = page.url
            if "Account/Login" in current_url:
                logger.warning("Login may have failed — still on login page after submit")
                return False

            logger.debug("Login successful — redirected to: %s", current_url)
            return True

        except Exception as e:
            logger.warning("Login failed: %s", str(e))
            return False
