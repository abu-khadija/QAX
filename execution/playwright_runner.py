"""
playwright_runner.py — Playwright Browser Lifecycle Manager
============================================================
Manages the Playwright browser, context, and page for test execution.

Handles:
  - Browser launch (Chromium, Firefox, or WebKit)
  - Browser context creation (isolated sessions)
  - Page navigation
  - Screenshot capture
  - Graceful teardown

All methods are async and wrapped in try/except.
None of them crash — they return None or False on any error.

Requires Playwright to be installed:
  pip install playwright==1.44.0
  playwright install chromium

Note: Playwright requires Python 3.11 or 3.12.
On Python 3.14, set ENABLE_SCREENSHOT=false and skip execution
until Playwright adds Python 3.14 support.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Playwright availability guard ─────────────────────────────────────────────
try:
    from playwright.async_api import (
        async_playwright,
        Browser,
        BrowserContext,
        Page,
        Playwright,
    )
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = None        # type: ignore[assignment,misc]
    BrowserContext = None  # type: ignore[assignment,misc]
    Page = None           # type: ignore[assignment,misc]
    Playwright = None     # type: ignore[assignment,misc]
    logger.warning(
        "Playwright is not installed. Execution features are disabled. "
        "Install with: pip install playwright==1.44.0 && playwright install chromium "
        "(requires Python 3.11 or 3.12)"
    )

# ── Default timeout for all Playwright operations (milliseconds) ──────────────
DEFAULT_TIMEOUT_MS = 30_000

# ── Default folder for screenshots ────────────────────────────────────────────
DEFAULT_SCREENSHOT_FOLDER = "output/screenshots"


class PlaywrightRunner:
    """
    Manages the full Playwright browser lifecycle for test execution.

    Creates and tears down a browser, context, and page. Provides
    helpers for navigation and screenshot capture used by TestExecutor.

    Usage:
        runner = PlaywrightRunner()
        await runner.setup(headless=True)
        await runner.navigate("https://your-app.example.com")
        path = await runner.take_screenshot("login_page")
        page = await runner.get_page()
        await runner.teardown()

    Attributes:
        page: Active Playwright Page, or None before setup/after teardown.
        browser: Active Playwright Browser, or None.
        context: Active Playwright BrowserContext, or None.
    """

    def __init__(self) -> None:
        """
        Initialize the runner with empty browser state.

        Call setup() before using any other methods.
        """
        self.page: "Page | None" = None
        self.browser: "Browser | None" = None
        self.context: "BrowserContext | None" = None
        self._playwright: "Playwright | None" = None

        if not PLAYWRIGHT_AVAILABLE:
            logger.warning(
                "PlaywrightRunner created but Playwright is not available. "
                "All methods will return safe defaults."
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Setup
    # ─────────────────────────────────────────────────────────────────────────

    async def setup(
        self,
        headless: bool = True,
        browser_type: str = "chromium",
    ) -> None:
        """
        Launch the Playwright browser, create a context and page.

        Must be called before any other method. Stores the browser,
        context, and page as instance attributes for use by other methods.

        Args:
            headless (bool): Run browser without UI window. Default: True.
                             Set False for debugging (you can watch the browser run).
            browser_type (str): Which browser to launch.
                                One of: "chromium", "firefox", "webkit".
                                Default: "chromium" (fastest for CI/CD).

        Returns:
            None. Sets self.page, self.browser, self.context.
            Does nothing (logs warning) if Playwright is not installed.
        """
        if not PLAYWRIGHT_AVAILABLE:
            logger.warning("Cannot setup browser — Playwright is not installed")
            return

        try:
            self._playwright = await async_playwright().start()

            # Launch the selected browser type
            browser_launcher = getattr(self._playwright, browser_type, None)
            if browser_launcher is None:
                logger.error(
                    "Unknown browser type: '%s'. Use 'chromium', 'firefox', or 'webkit'.",
                    browser_type
                )
                browser_launcher = self._playwright.chromium

            self.browser = await browser_launcher.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],  # Required for Linux/CI
            )

            # Create an isolated browser context (fresh cookies/storage per run)
            self.context = await self.browser.new_context(
                viewport={"width": 1440, "height": 900},
                ignore_https_errors=True,   # EXAMPLE RC env may have self-signed certs
            )
            self.context.set_default_timeout(DEFAULT_TIMEOUT_MS)

            self.page = await self.context.new_page()

            logger.info(
                "Browser launched: %s (headless=%s)",
                browser_type, headless
            )

        except Exception as e:  # noqa: BLE001
            logger.error("Failed to launch browser: %s", e)
            await self.teardown()  # Clean up anything partially created

    async def new_session(self) -> bool:
        """
        Replace the page and its browser context with fresh ones — no cookies, local
        storage or open login from the previous test case. Each case must stand on
        its own: with one shared session a logged-in case made the next case's login
        form disappear (found on linkding, 2026-10-19).

        Returns:
            bool: True when a fresh page is ready. Never raises.
        """
        if not self.browser:
            return False
        try:
            if self.context:
                await self.context.close()
            self.context = await self.browser.new_context(
                viewport={"width": 1440, "height": 900},
                ignore_https_errors=True,
            )
            self.context.set_default_timeout(DEFAULT_TIMEOUT_MS)
            self.page = await self.context.new_page()
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("Could not open a fresh browser session: %s", e)
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Teardown
    # ─────────────────────────────────────────────────────────────────────────

    async def teardown(self) -> None:
        """
        Close the page, context, and browser in reverse order.

        Always safe to call — even if setup() was never called or failed
        partway through. Each close is wrapped in its own try/except.
        """
        if self.page:
            try:
                await self.page.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("Error closing page: %s", e)
            self.page = None

        if self.context:
            try:
                await self.context.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("Error closing context: %s", e)
            self.context = None

        if self.browser:
            try:
                await self.browser.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("Error closing browser: %s", e)
            self.browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:  # noqa: BLE001
                logger.debug("Error stopping playwright: %s", e)
            self._playwright = None

        logger.info("Browser teardown complete")

    # ─────────────────────────────────────────────────────────────────────────
    # Navigation
    # ─────────────────────────────────────────────────────────────────────────

    async def navigate(self, url: str) -> bool:
        """
        Navigate the browser page to a URL and wait for the page to load.

        Waits for the "load" event which fires when the DOM and all
        subresources have loaded (safer than "domcontentloaded" for SPAs).

        Args:
            url (str): Full URL to navigate to
                       (e.g., "https://your-app.example.com/Orders").

        Returns:
            bool: True if navigation succeeded, False on any error.
        """
        if not self.page:
            logger.warning("Cannot navigate — browser is not set up. Call setup() first.")
            return False

        try:
            await self.page.goto(url, wait_until="load", timeout=DEFAULT_TIMEOUT_MS)
            logger.debug("Navigated to: %s", url)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("Navigation failed for URL '%s': %s", url, e)
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Screenshot
    # ─────────────────────────────────────────────────────────────────────────

    async def take_screenshot(
        self,
        name: str,
        folder: str = DEFAULT_SCREENSHOT_FOLDER,
    ) -> str:
        """
        Capture a full-page screenshot of the current browser state.

        Saves the screenshot as a PNG file named {name}_{timestamp}.png
        in the specified folder. Creates the folder if it does not exist.

        Args:
            name (str): Base name for the screenshot file (e.g., "step_3_fail").
                        Spaces are replaced with underscores.
            folder (str): Directory to save screenshots in.
                          Default: "output/screenshots"

        Returns:
            str: Full path to the saved screenshot file.
                 Empty string if screenshot could not be taken.
        """
        if not self.page:
            logger.warning("Cannot take screenshot — browser is not set up")
            return ""

        try:
            # Ensure the output folder exists
            Path(folder).mkdir(parents=True, exist_ok=True)

            # Build the timestamped filename
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            safe_name = name.replace(" ", "_").replace("/", "_")
            filename = f"{safe_name}_{timestamp}.png"
            file_path = str(Path(folder) / filename)

            await self.page.screenshot(path=file_path, full_page=True)
            logger.info("Screenshot saved: %s", file_path)
            return file_path

        except Exception as e:  # noqa: BLE001
            logger.error("Screenshot failed for '%s': %s", name, e)
            return ""

    # ─────────────────────────────────────────────────────────────────────────
    # Page Access
    # ─────────────────────────────────────────────────────────────────────────

    async def get_page(self) -> "Page | None":
        """
        Return the active Playwright Page object.

        Returns:
            Page | None: The current Playwright page, or None if not set up.
        """
        return self.page

    def is_ready(self) -> bool:
        """
        Check if the browser is set up and ready to use.

        Returns:
            bool: True if page, context, and browser are all initialized.
        """
        return (
            PLAYWRIGHT_AVAILABLE
            and self.page is not None
            and self.context is not None
            and self.browser is not None
        )
