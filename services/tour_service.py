"""
services/tour_service.py — TourService (the live-tour capability)
=================================================================
Wraps the browser-driven live tour (execution/live_tour.py) in the service-layer
contract: takes a running app URL, returns a typed TourResult with the app's real
UI surface. This is the UNDERSTAND-mode step that grounds generation in what the
app ACTUALLY exposes — the biggest lever on coverage, and the source of the real
selectors that make UI tests executable.

Never prints, never calls sys.exit(); a missing Playwright or a browser failure is
captured in TourResult.error, not raised. Playwright needs Python 3.11/3.12.

Usage:
    from services.tour_service import TourService
    tour = TourService(ctx).tour(
        "http://localhost:5173",
        login={"email": "demo@x.com", "password": "Passw0rd!"},
    )
    print(tour.screen_count, "screens,", tour.element_count, "elements")
    generation_context = tour.as_context()
"""

from __future__ import annotations

import logging

from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import TourResult

logger = logging.getLogger(__name__)


class TourService:
    """Drive a browser over the running app and return its observed UI surface."""

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Config/credentials. Built from the
                environment when None. (No Anthropic call is made here.)
        """
        self.context = context or QAXContext.from_env()

    def tour(
        self,
        base_url: str,
        *,
        routes: list[str] | None = None,
        login: dict | None = None,
        headless: bool = True,
        max_screens: int = 8,
        capture_screenshots: bool = False,
        screenshot_dir: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> TourResult:
        """
        Tour the running app and return its observed surface.

        Args:
            base_url (str): Root URL of the running app.
            routes (list[str] | None): Extra in-app paths to visit. When None, the
                tour auto-discovers same-origin links.
            login (dict | None): Optional login flow (email/password [+ selectors]).
            headless (bool): Run without a visible window.
            max_screens (int): Hard cap on screens visited.
            progress (ProgressReporter | None): Progress sink.

        Returns:
            TourResult: Observed screens/elements. Never raises at runtime.
        """
        prog = progress or NullProgressReporter()

        if not base_url or not str(base_url).strip():
            return TourResult(error="No app URL to tour.")

        try:
            from execution.live_tour import PLAYWRIGHT_AVAILABLE
        except ImportError:
            PLAYWRIGHT_AVAILABLE = False

        if not PLAYWRIGHT_AVAILABLE:
            msg = ("Playwright is not installed — run: pip install playwright && "
                   "playwright install chromium (requires Python 3.11/3.12)")
            prog.emit("tour", "error", msg)
            return TourResult(base_url=base_url, error=msg)

        prog.emit("tour", "start", f"touring {base_url}")

        import asyncio

        from execution.live_tour import tour_app

        try:
            raw = asyncio.run(tour_app(
                base_url, routes=routes, login=login,
                headless=headless, max_screens=max_screens,
                capture_screenshots=capture_screenshots, screenshot_dir=screenshot_dir,
            ))
        except Exception as exc:
            logger.error("TourService failed: %s", exc)
            prog.emit("tour", "error", str(exc))
            return TourResult(base_url=base_url, error=str(exc))

        result = TourResult(
            base_url=raw.get("base_url", base_url),
            screens=raw.get("screens", []),
            login_ok=raw.get("login_ok"),
            error=raw.get("error"),
        )
        prog.emit("tour", "ok" if result.success else "error",
                  f"{result.screen_count} screen(s), {result.element_count} element(s)")
        return result
