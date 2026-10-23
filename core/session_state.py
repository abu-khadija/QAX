"""
core/session_state.py — log in once by hand, reuse the session (SSO, 2FA, CAPTCHA)
=================================================================================
Apps behind single sign-on, two-factor codes or a CAPTCHA cannot (and must not) be
logged into by QAX. Instead the operator logs in ONCE in a real browser window that QAX
opens; when they close the window, QAX keeps that browser session (cookies + local
storage, Playwright "storage state") for the project. `map` and every browser run of the
project then start already logged in.

The state file is a credential: it is written with owner-only permissions under
config/projects/<project>/.auth/ (git-ignored), never served by the web UI, never
copied into reports, and can be deleted at any time (`forget`). Never raises.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from core.paths import projects_dir  # noqa: E402 — the checkout, or the folder QAX runs from (wheel)
CONFIG_DIR = projects_dir()


def _safe(project: str) -> str:
    """A project name usable as a folder name."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", project or "").strip("-")


def state_path(project: str) -> Path:
    """Where the project's saved session lives."""
    return CONFIG_DIR / _safe(project) / ".auth" / "state.json"


def has_state(project: str) -> bool:
    """True when a saved session exists for the project."""
    return bool(_safe(project)) and state_path(project).is_file()


def state_for(project: str) -> "str | None":
    """The saved session file to start a browser context with, or None."""
    return str(state_path(project)) if has_state(project) else None


def forget(project: str) -> bool:
    """Delete the project's saved session. Returns True when one was deleted."""
    try:
        p = state_path(project)
        if p.is_file():
            p.unlink()
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def saved_at(project: str) -> "str | None":
    """When the session was saved (ISO local time), or None."""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(state_path(project).stat().st_mtime))
    except Exception:  # noqa: BLE001
        return None


async def record_session(url: str, project: str, *, timeout_s: int = 600, insecure: bool = False) -> "tuple[bool, str]":
    """
    Open a visible browser at the app, let the operator log in (SSO, 2FA …), and save the
    session when they close the window (or after timeout_s). Returns (ok, message).
    """
    if not _safe(project):
        return False, "A project name is needed to keep the session."
    try:
        from playwright.async_api import async_playwright
    except Exception:  # noqa: BLE001
        return False, "Playwright is not installed (run `make install`)."
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False)
            context = await browser.new_context(ignore_https_errors=insecure, viewport=None)
            page = await context.new_page()
            await page.goto(url, wait_until="load", timeout=60_000)
            closed = {"v": False}
            page.on("close", lambda *_: closed.__setitem__("v", True))
            browser.on("disconnected", lambda *_: closed.__setitem__("v", True))
            start = time.monotonic()
            last_state = None
            while not closed["v"] and time.monotonic() - start < timeout_s:
                try:
                    last_state = await context.storage_state()      # keep the latest while open
                except Exception:  # noqa: BLE001
                    break
                await page.wait_for_timeout(1000) if not page.is_closed() else None
            if last_state is None:
                return False, "The browser closed before any session could be read."
            path = state_path(project)
            path.parent.mkdir(parents=True, exist_ok=True)
            import json
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(last_state, fh)
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass
            n = len(last_state.get("cookies") or [])
            return True, f"Session saved for project '{project}' ({n} cookie(s)). Runs now start logged in."
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not record the session: {str(exc)[:200]}"
