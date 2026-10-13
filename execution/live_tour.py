"""
execution/live_tour.py — Live App Tour (UNDERSTAND mode, browser-driven)
========================================================================
Drives a real browser over the RUNNING app to enumerate its true surface —
every screen, and on each screen every interactive element with a REAL, stable
selector (data-testid → id → name → role). This is the "tour the live app before
you write a test" discipline: the code is a hypothesis, the running app is truth.

Two payoffs:
  1. Coverage — the generator now has the app's actual surface to cover, not a
     guess from source.
  2. Executable UI tests — real selectors replace TODO_ placeholders, so the
     generated automation_steps can actually run.

All Playwright use is async and guarded by PLAYWRIGHT_AVAILABLE (Playwright needs
Python 3.11/3.12). Nothing here crashes the caller — errors surface in the result.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover - env without playwright (e.g. Python 3.14)
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None  # type: ignore[assignment]

DEFAULT_TIMEOUT_MS = 15_000

# JS that enumerates the interactive surface of the current page, each element
# reduced to a stable selector + a human label. Runs in the page context.
_SURFACE_JS = r"""
() => {
  const stableSelector = (el) => {
    const testid = el.getAttribute('data-testid');
    if (testid) return `[data-testid="${testid}"]`;
    if (el.id) return `#${el.id}`;
    const name = el.getAttribute('name');
    if (name) return `${el.tagName.toLowerCase()}[name="${name}"]`;
    return null;
  };
  const nodes = document.querySelectorAll(
    'input, select, textarea, button, a[href], [role="button"], [data-testid]'
  );
  const seen = new Set();
  const out = [];
  for (const el of nodes) {
    const label = (
      el.getAttribute('aria-label') ||
      (el.tagName === 'A' || el.tagName === 'BUTTON' ? el.textContent : '') ||
      el.getAttribute('placeholder') ||
      el.getAttribute('value') ||
      el.getAttribute('name') || ''
    ).trim().replace(/\s+/g, ' ').slice(0, 60);
    const info = {
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      testid: el.getAttribute('data-testid') || '',
      selector: stableSelector(el),
      label: label,
    };
    const key = info.selector || (info.tag + '|' + info.type + '|' + info.label);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(info);
  }
  return out;
}
"""

_LINKS_JS = r"""
() => [...document.querySelectorAll('a[href]')]
  .map(a => a.getAttribute('href'))
  .filter(h => h && h.startsWith('/') && !h.startsWith('//'))
"""


async def tour_app(
    base_url: str,
    *,
    routes: list[str] | None = None,
    login: dict | None = None,
    headless: bool = True,
    max_screens: int = 8,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    capture_screenshots: bool = False,
    screenshot_dir: str | None = None,
) -> dict:
    """
    Tour the running app and return its observed surface.

    Args:
        base_url (str): Root URL of the running app (e.g. http://localhost:5173).
        routes (list[str] | None): Extra in-app paths to visit (e.g. ["/categories"]).
            When None, the tour auto-discovers same-origin links from each screen.
        login (dict | None): Optional login flow. Keys (all optional, with sensible
            fallbacks): path, email, password, email_selector, password_selector,
            submit_selector.
        headless (bool): Run without a visible window.
        max_screens (int): Hard cap on screens visited.
        timeout_ms (int): Per-navigation timeout.

    Returns:
        dict: {base_url, login_ok, screens: [{url, title, elements, errors}],
               error}. `error` is set only on an outright failure.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return {"base_url": base_url, "screens": [], "login_ok": None,
                "error": "Playwright is not installed (needs Python 3.11/3.12)."}

    result: dict = {"base_url": base_url, "screens": [], "login_ok": None, "error": None}
    origin = _origin(base_url)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless)
            context = await browser.new_context()
            page = await context.new_page()

            # Collect console/page errors and failed API calls per screen (mutable buckets).
            errors: list[str] = []
            net_errors: list[str] = []
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}"[:200])
                    if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"[:200]))

            def _on_response(r):
                try:
                    if r.status >= 400 and r.request.resource_type in ("xhr", "fetch", "document"):
                        net_errors.append(f"{r.status} {r.request.method} {r.url}"[:200])
                except Exception:
                    pass
            page.on("response", _on_response)
            page.on("requestfailed", lambda r: net_errors.append(
                f"failed {getattr(r, 'method', '')} {getattr(r, 'url', '')}"[:200])
                if getattr(getattr(r, "request", r), "resource_type", "") in ("xhr", "fetch") else None)

            if capture_screenshots and screenshot_dir:
                import os
                os.makedirs(screenshot_dir, exist_ok=True)

            try:
                if login is not None:
                    result["login_ok"] = await _do_login(page, base_url, login, timeout_ms)

                visited: set[str] = set()
                queue: list[str] = [base_url]
                for r in (routes or []):
                    queue.append(urljoin(base_url + "/", r.lstrip("/")))

                while queue and len(result["screens"]) < max_screens:
                    url = queue.pop(0)
                    norm = _norm(url)
                    if norm in visited or _origin(url) != origin:
                        continue
                    visited.add(norm)

                    errors.clear()
                    net_errors.clear()
                    screen = await _capture_screen(page, url, timeout_ms)
                    screen["errors"] = list(errors)
                    screen["net_errors"] = list(net_errors)
                    if capture_screenshots and screenshot_dir:
                        import os
                        shot = os.path.join(screenshot_dir, f"screen_{len(result['screens']):02d}.png")
                        try:
                            await page.screenshot(path=shot, full_page=True)
                            screen["screenshot"] = shot
                        except Exception as exc:
                            logger.warning("screenshot failed on %s: %s", url, exc)
                    result["screens"].append(screen)

                    if routes is None:  # auto-discover further links
                        try:
                            hrefs = await page.evaluate(_LINKS_JS)
                        except Exception:
                            hrefs = []
                        for h in hrefs:
                            nxt = urljoin(base_url + "/", h.lstrip("/"))
                            if _norm(nxt) not in visited:
                                queue.append(nxt)
            finally:
                await context.close()
                await browser.close()
    except Exception as exc:  # pragma: no cover - real browser failures
        logger.error("live tour failed: %s", exc)
        result["error"] = str(exc)

    return result


async def _capture_screen(page, url: str, timeout_ms: int) -> dict:
    """Navigate to a URL and enumerate its interactive surface."""
    try:
        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
    except Exception:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            return {"url": url, "title": "", "elements": [], "nav_error": str(exc)[:150]}
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        elements = await page.evaluate(_SURFACE_JS)
    except Exception as exc:
        elements = []
        logger.warning("surface eval failed on %s: %s", url, exc)
    return {"url": _norm(url), "title": title, "elements": elements}


async def _do_login(page, base_url: str, login: dict, timeout_ms: int) -> bool:
    """
    Perform a best-effort login. Tries the given selectors, then falls back to
    type-based ones (input[type=email], input[type=password], submit button).
    """
    path = login.get("path", "/login")
    url = urljoin(base_url + "/", path.lstrip("/"))
    try:
        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
    except Exception:
        return False

    email_sel = login.get("email_selector") or 'input[type="email"], [data-testid$="email-input"]'
    pass_sel = login.get("password_selector") or 'input[type="password"], [data-testid$="password-input"]'
    submit_sel = login.get("submit_selector") or '[data-testid$="submit"], button[type="submit"]'

    login_path = path.rstrip("/") or "/login"
    try:
        await page.fill(email_sel, login.get("email", ""), timeout=timeout_ms)
        await page.fill(pass_sel, login.get("password", ""), timeout=timeout_ms)
        await page.click(submit_sel, timeout=timeout_ms)
        # SPA redirects client-side after the auth call — wait for the URL to
        # leave the login screen rather than for a full page load.
        try:
            await page.wait_for_url(
                lambda u: login_path not in urlparse(u).path.rstrip("/"),
                timeout=timeout_ms,
            )
        except Exception:
            await page.wait_for_timeout(1500)
    except Exception as exc:
        logger.warning("login flow failed: %s", exc)
        return False
    # Logged in if we left the login screen.
    return login_path not in urlparse(page.url).path.rstrip("/")


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _norm(url: str) -> str:
    """Normalise a URL for de-dup: drop trailing slash and fragment."""
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return f"{p.scheme}://{p.netloc}{path}"
