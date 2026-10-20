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
import re
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
        browser_type: str = "",
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

        browser_type = (browser_type or os.getenv("QAX_BROWSER", "") or "chromium").strip().lower()
        self.browser_type = browser_type
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

            # Watch mode (`--watch`): a visible browser, slowed down so a person can follow it.
            try:
                slow_mo = max(0, int(os.getenv("QAX_SLOW_MO_MS", "0") or 0))
            except ValueError:
                slow_mo = 0
            self.browser = await browser_launcher.launch(
                headless=headless,
                slow_mo=slow_mo,
                args=["--no-sandbox", "--disable-dev-shm-usage"],  # Required for Linux/CI
            )

            # Create an isolated browser context (fresh cookies/storage per run)
            await self._open_context()

            logger.info(
                "Browser launched: %s (headless=%s)",
                browser_type, headless
            )

        except Exception as e:  # noqa: BLE001
            logger.error("Failed to launch browser: %s", e)
            await self.teardown()  # Clean up anything partially created

    _DEVICES = {"mobile": "Pixel 7", "mobile-ios": "iPhone 13", "tablet": "iPad (gen 7)"}

    def _device_options(self) -> dict:
        """
        Screen and device for the run (QAX_DEVICE / `--device`): desktop (1440×900, the
        default), mobile (Pixel 7: touch, mobile user agent, 412×915), mobile-ios
        (iPhone 13), tablet (iPad). Firefox has no mobile emulation — it gets the
        viewport and touch only. Never raises.
        """
        device = os.getenv("QAX_DEVICE", "desktop").strip().lower()
        name = self._DEVICES.get(device)
        if not name:
            return {"viewport": {"width": 1440, "height": 900}}
        try:
            opts = dict(self._playwright.devices[name])
            opts.pop("default_browser_type", None)
            if getattr(self, "browser_type", "chromium") == "firefox":
                opts.pop("is_mobile", None)
            return opts
        except Exception:  # noqa: BLE001
            return {"viewport": {"width": 390, "height": 844}, "has_touch": True}

    async def _open_context(self) -> None:
        """
        Open a fresh context + page. TLS certificates are verified unless the
        operator opted in with QAX_INSECURE=true (`--insecure`) — the same rule as
        the scan; it used to be ignored silently for every target. Every request
        that can change data (not GET/HEAD/OPTIONS) is recorded, so a run can say
        what it sent to the target (see take_writes()).
        """
        insecure = os.getenv("QAX_INSECURE", "false").strip().lower() == "true"
        # a session the operator recorded by hand (SSO / 2FA apps): start logged in
        from core.session_state import state_for
        storage = state_for(os.getenv("DEFAULT_PROJECT", ""))
        self.context = await self.browser.new_context(
            **self._device_options(),
            ignore_https_errors=insecure,
            **({"storage_state": storage} if storage else {}),
        )
        self.context.set_default_timeout(DEFAULT_TIMEOUT_MS)
        self._writes = []

        def _on_request(req):
            try:
                from core.app_mapper import is_write_request
                if is_write_request(req.method, req.url, req.post_data):
                    from urllib.parse import urlparse
                    u = urlparse(req.url)
                    self._writes.append({"method": req.method.upper(),
                                         "host": u.netloc, "path": u.path or "/"})
            except Exception:  # noqa: BLE001
                pass
        self.context.on("request", _on_request)
        self._throttled = 0

        def _on_response(resp):
            try:
                if resp.status == 429:
                    self._throttled += 1
            except Exception:  # noqa: BLE001
                pass
        self.context.on("response", _on_response)
        # Playwright trace (`--trace`): every action, network call and DOM snapshot of the
        # case, viewable with `playwright show-trace <file>`.
        self._tracing = os.getenv("QAX_TRACE", "false").strip().lower() == "true"
        if self._tracing:
            try:
                await self.context.tracing.start(screenshots=True, snapshots=True)
            except Exception as e:  # noqa: BLE001
                logger.debug("trace start failed: %s", e)
                self._tracing = False
        self.page = await self.context.new_page()
        # What went wrong IN the page while a case ran — JavaScript exceptions, console
        # errors, server errors and failed requests. Reported as observed problems even
        # when every assertion passed: a QA engineer would notice them too.
        self._page_errors = []
        self._latest_page = None
        origin_host = None

        def _note(kind, text):
            if len(self._page_errors) < 50:
                self._page_errors.append({"kind": kind, "text": str(text)[:300]})

        def _watch(pg):
            try:
                pg.on("pageerror", lambda exc: _note("javascript exception", exc))
                pg.on("console", lambda m: _note("console error", m.text) if m.type == "error" else None)
            except Exception:  # noqa: BLE001
                pass

        def _on_resp(resp):
            try:
                if resp.status >= 500:
                    from urllib.parse import urlparse as _u
                    _note(f"HTTP {resp.status}", f"{resp.request.method} {_u(resp.url).path}")
            except Exception:  # noqa: BLE001
                pass

        def _on_failed(req):
            try:
                if req.resource_type in ("document", "xhr", "fetch"):
                    from urllib.parse import urlparse as _u
                    _note("request failed", f"{req.method} {_u(req.url).path} ({req.failure or ''})")
            except Exception:  # noqa: BLE001
                pass

        def _on_new_page(pg):
            # a click that opens a NEW TAB: later steps continue there
            self._latest_page = pg
            _watch(pg)
        try:
            self.context.on("response", _on_resp)
            self.context.on("requestfailed", _on_failed)
            self.context.on("page", _on_new_page)
            _watch(self.page)
        except Exception as e:  # noqa: BLE001
            logger.debug("page watchers not attached: %s", e)
        # Browser dialogs (confirm/alert/prompt): Playwright DISMISSES them by default, so a
        # "Delete → Are you sure?" did nothing and a delete test failed for the wrong reason
        # (found on Flaskr). Accept by default (QAX_DIALOGS=dismiss to refuse) and record them.
        self._dialogs = []

        async def _on_dialog(dialog):
            try:
                self._dialogs.append({"type": dialog.type, "message": dialog.message[:200]})
                if os.getenv("QAX_DIALOGS", "accept").strip().lower() == "dismiss":
                    await dialog.dismiss()
                else:
                    await dialog.accept()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.page.on("dialog", _on_dialog)
        except Exception as e:  # noqa: BLE001 — never crash on a page without events
            logger.debug("dialog handler not attached: %s", e)

    async def stop_trace(self, name: str, folder: str = "output/traces") -> str:
        """Save the current context's trace as <folder>/<name>_<ts>.zip ("" when not tracing)."""
        if not getattr(self, "_tracing", False) or not self.context:
            return ""
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            path = str(Path(folder) / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}_{ts}.zip")
            await self.context.tracing.stop(path=path)
            self._tracing = False
            return path
        except Exception as e:  # noqa: BLE001
            logger.debug("trace stop failed: %s", e)
            return ""

    def take_page_errors(self) -> list:
        """Return and clear the page problems (JS exceptions, console errors, 5xx, failed requests)."""
        out, self._page_errors = list(getattr(self, "_page_errors", []) or []), []
        return out

    def take_dialogs(self) -> list:
        """Return and clear the browser dialogs (confirm/alert) seen since the last call."""
        out, self._dialogs = list(getattr(self, "_dialogs", []) or []), []
        return out

    def take_throttled(self) -> int:
        """Return and clear how many HTTP 429 answers the app sent since the last call."""
        n, self._throttled = int(getattr(self, "_throttled", 0) or 0), 0
        return n

    def take_writes(self) -> list:
        """Return and clear the write requests (method/host/path) sent since the last call."""
        out, self._writes = list(getattr(self, "_writes", []) or []), []
        return out

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
            await self._open_context()
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
            Page | None: The newest open page (a tab a click opened), else the
                         main page; None if not set up.
        """
        latest = getattr(self, "_latest_page", None)
        try:
            if latest is not None and not latest.is_closed():
                return latest
        except Exception:  # noqa: BLE001
            pass
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
