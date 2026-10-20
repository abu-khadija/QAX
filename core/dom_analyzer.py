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



async def _settle(page, timeout_ms: int = 15_000, idle_ms: int = 3_000) -> None:
    """
    Wait for the page to load, then briefly for network idle. Apps that keep a
    connection open (live reload, Turbo/SSE, analytics polling) never reach
    "networkidle"; waiting for it alone timed out and discovered nothing
    (found on linkding, 2026-10-19). Never raises.
    """
    try:
        await page.wait_for_load_state("load", timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=idle_ms)
    except Exception:  # noqa: BLE001
        pass   # still busy — the DOM is loaded, carry on


_LOGIN_FORM_JS = r"""() => {
  const looksLogin = (t) => /\blog\s?-?in\b|\bsign\s?-?in\b|\bauth|\bsession/i.test(t || '');
  for (const f of document.querySelectorAll('form')) {
    const pw = f.querySelectorAll("input[type='password']");
    const user = f.querySelectorAll("input[type='email'], input[type='text'], input:not([type])");
    if (pw.length !== 1 || user.length < 1) continue;
    const btnText = [...f.querySelectorAll('button, input[type=submit]')].map(b => b.innerText || b.value).join(' ');
    if (looksLogin(location.href) || looksLogin(f.getAttribute('action')) || looksLogin(btnText)) return true;
  }
  return false;
}"""


async def _wait_dom_stable(page, max_ms: int = 6_000, quiet_polls: int = 3) -> None:
    """
    Wait until the number of interactive elements stops changing (same count on
    `quiet_polls` polls 250 ms apart), at most max_ms. An SPA route change (#/settings)
    fires no new "load" event and a realtime connection keeps the network busy, so the
    page was read before its form had rendered (found on PocketBase). Never raises.
    """
    import asyncio
    js = "() => document.querySelectorAll('input, select, textarea, button, a[href]').length"
    last, same, waited = None, 0, 0
    while waited < max_ms:
        try:
            # the URL too: an SPA's redirect after login lands a moment after the click
            # (PocketBase: #/collections replaced a #/settings navigation that came first)
            n = (await page.evaluate(js), page.url)
        except Exception:  # noqa: BLE001
            return
        same = same + 1 if n == last else 0
        if same >= quiet_polls:
            return
        last = n
        await asyncio.sleep(0.25)
        waited += 250


async def _goto_settled(page, url: str, timeout_ms: int = 30_000) -> None:
    """Navigate (waiting for "load", not "networkidle"), then settle. Raises on navigation errors."""
    await page.goto(url, wait_until="load", timeout=timeout_ms)
    await _settle(page, timeout_ms=timeout_ms)
    await _wait_dom_stable(page)


def ai_discovery_enabled() -> bool:
    """
    True only when the operator opted in (discover --ai or QAX_AI_DISCOVER=true).
    Labelling with Claude sends the page's element list to the AI provider; the
    default is the deterministic labeller, which works without a key or credit.
    """
    return os.getenv("QAX_AI_DISCOVER", "false").strip().lower() == "true"


_ELEMENTS_JS = r"""(avoid) => {
  avoid = new Set(avoid || []);   // ids seen to change between loads (generated by the framework)
  // ids a framework generates (React useId ":r52:" / "_r_3u_", Base UI, Radix, MUI…) are stable
  // across a reload but SHIFT when the page changes (a new memo → every id moves): never select
  // by them (found on Memos, 2026-10-20). Mirrors _looks_generated() in Python.
  const generated = (id) => /:r[0-9a-z]*:|(^|[-_])_?r_[0-9a-z]+_|(^|[-_])(radix|headlessui|base-ui|react-aria|mui|downshift)[-_]/i.test(id || '')
    || (id || '').split(/[_\-.:]/).some(c => c.length >= 6 && /\d/.test(c) && /[a-z]/i.test(c));
  const okId = (id) => id && !avoid.has(id) && !generated(id);
  // For each interactive element, pick a selector that matches EXACTLY ONE element on the
  // page (checked here, in the browser): a[href='#'] or button:has-text('Save') can match
  // several elements and a click would hit the wrong one (found on Healthchecks).
  const one = (sel) => { try { return document.querySelectorAll(sel).length === 1; } catch (e) { return false; } };
  const q = (v) => String(v).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  const cssPath = (el) => {
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && n !== document.body) {
      if (okId(n.id) && one('#' + CSS.escape(n.id))) { parts.unshift('#' + CSS.escape(n.id)); break; }
      let i = 1, s = n;
      while ((s = s.previousElementSibling)) if (s.tagName === n.tagName) i++;
      parts.unshift(n.tagName.toLowerCase() + ':nth-of-type(' + i + ')');
      n = n.parentElement;
    }
    return parts.join(' > ');
  };
  const out = [];
  // + editable divs: rich editors (Memos, Notion-likes) are contenteditable, not <textarea>
  // + ARIA widgets: Base UI / Radix / MUI menus are <div role="menuitem">, not buttons (found on Memos)
  const els = document.querySelectorAll("input, select, textarea, button, a[href], [role='button'], [contenteditable='true'], [contenteditable=''], [role='textbox'], [role='menuitem'], [role='menuitemcheckbox'], [role='menuitemradio'], [role='option'], [role='tab'], [role='checkbox'], [role='switch'], [role='link'], [role='combobox']");
  for (const e of els) {
    const st = getComputedStyle(e);
    if (e.type === 'hidden' || st.display === 'none' || st.visibility === 'hidden') continue;
    let label = '';
    if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) label = l.innerText; }
    if (!label && e.closest('label')) label = e.closest('label').innerText;
    const tag = e.tagName.toLowerCase(), name = e.getAttribute('name') || '', href = e.getAttribute('href') || '';
    const testid = e.getAttribute('data-testid') || '', aria = e.getAttribute('aria-label') || '';
    const value = (tag === 'input' ? (e.getAttribute('value') || '') : '');
    const cands = [];
    if (okId(e.id)) cands.push('#' + CSS.escape(e.id));
    if (testid) cands.push(`[data-testid='${q(testid)}']`);
    // value is DATA for a text field (it changes when the user saves) — use it only to tell
    // apart radio/checkbox options and submit-style buttons (found on the SPA demo)
    const typ = (e.getAttribute('type') || '').toLowerCase();
    const valueIsId = ['radio', 'checkbox', 'submit', 'button', 'reset', 'image'].includes(typ);
    if (name && value && valueIsId) cands.push(`${tag}[name='${q(name)}'][value='${q(value)}']`);
    if (name) cands.push(`${tag}[name='${q(name)}']`);
    if (aria) cands.push(`${tag}[aria-label='${q(aria)}']`);
    const ph = e.getAttribute('placeholder') || e.getAttribute('aria-placeholder') || e.getAttribute('data-placeholder') || '';
    if (ph) cands.push(`${tag}[placeholder='${q(ph)}']`);
    if (e.isContentEditable) {
      // rich editors: their library class is stable (CodeMirror .cm-content on Memos, ProseMirror/Tiptap, Quill)
      for (const c of ['cm-content', 'ProseMirror', 'tiptap', 'ql-editor'])
        if (e.classList.contains(c)) cands.push(`.${c}[contenteditable]`);
      if (!ph) cands.push(`${tag}[contenteditable]`);
    }
    if (tag === 'a' && href && href !== '#' && !href.startsWith('javascript:') && !/\/(\d+|[0-9a-f]{8,}(-[0-9a-f]{4,})*)(\/|$)/i.test(href))
      cands.push(`a[href='${q(href)}']`);
    if (tag === 'input' && value && valueIsId) cands.push(`input[type='${q(typ || 'submit')}'][value='${q(value)}']`);
    let sel = cands.find(one) || '';
    // textContent (what :text-is matches), not innerText — CSS text-transform shows
    // "Checks" as "CHECKS" (found on Healthchecks); multi-line labels are skipped.
    const norm = (x) => (x.textContent || '').replace(/\s+/g, ' ').trim();
    const text = norm(e);
    const role = e.getAttribute('role') || '';
    if (!sel && text && text.length <= 60 && role && role !== 'textbox') {
      // an ARIA widget by its role and exact name (descendant text included)
      const same = [...document.querySelectorAll(`[role='${role}']`)].filter(x => norm(x) === text);
      if (same.length === 1 && !text.includes("'")) sel = `xpath=//*[@role='${role}'][normalize-space(.)='${text}']`;
    }
    if (!sel && text && text.length <= 60 && (tag === 'a' || tag === 'button')) {
      // stable when the page layout changes: the only <a>/<button> whose text CONTAINS this
      // text (has-text is a case-insensitive substring match and works when the text is in a
      // child element — text-is did not: PocketBase's <button><span>Login</span></button>)
      const low = text.toLowerCase();
      const same = [...document.querySelectorAll(tag)].filter(x => norm(x).toLowerCase().includes(low));
      if (same.length === 1) sel = `${tag}:has-text('${q(text)}')`;
      // exact text (descendants included): "Save" when other buttons merely CONTAIN "save" —
      // a positional path broke as soon as typing changed the page (found on Memos)
      if (!sel && !text.includes("'")) {
        const exact = [...document.querySelectorAll(tag)].filter(x => norm(x) === text);
        if (exact.length === 1) sel = `xpath=//${tag}[normalize-space(.)='${text}']`;
      }
    }
    if (!sel && e.form && e.type === 'submit') {
      // a form's submit button, anchored on its own form: "Add system" in the dialog has the same
      // text as the button that opens the dialog (found on Beszel)
      for (const f of e.form.elements) {
        if (!okId(f.id) || f === e) continue;
        const tsel = e.getAttribute('type') ? `[type='${q(e.getAttribute('type'))}']` : ':not([type])';
        const c = `form:has(#${CSS.escape(f.id)}) ${tag}${tsel}`;
        if (one(c)) { sel = c; break; }
      }
    }
    if (!sel) sel = cssPath(e);
    out.push({tag, id: e.id || '', name, type: (e.getAttribute('type') || '').toLowerCase(), testid, aria,
      role: e.getAttribute('role') || '',
      ph: e.getAttribute('placeholder') || '', value, text: (e.innerText || '').trim().slice(0, 40), href,
      label: (label || '').trim().slice(0, 40), sel,
      // visible now? (an element in a closed menu/modal is in the DOM but needs a click first)
      vis: (typeof e.checkVisibility === 'function') ? e.checkVisibility() : !!(e.offsetWidth || e.offsetHeight)});
  }
  return out;
}"""


def _slug(text: str) -> str:
    """'Add bookmark…' → 'add_bookmark' (ASCII words, max 4)."""
    import re
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return "_".join(words[:4])


def _looks_generated(ident: str) -> bool:
    """An id a framework generated per render (cols_8ckrlr1bemail, collection_upsert_vanoz1cscol_name):
    a 6+ character chunk mixing letters and digits. Never used to NAME an element."""
    ident = ident or ""
    # React useId / UI-library ids: ":r52:", "base-ui-:r3:", "radix-:R1:", "mui-12", "headlessui-menu-button-:r1:"
    if re.search(r":r[0-9a-z]*:|(^|[-_])_?r_[0-9a-z]+_|(^|[-_])(radix|headlessui|base-ui|react-aria|mui|downshift)[-_]",
                 ident, re.I):
        return True
    return any(len(c) >= 6 and re.search(r"\d", c) and re.search(r"[a-z]", c, re.I)
               for c in re.split(r"[_\-.:]", ident))


def label_elements(elements: list) -> dict:
    """
    Name interactive elements and pick a stable selector for each — no AI.

    Key: from the visible label, then name / id (Django's "id_" prefix dropped),
    aria-label, placeholder, button text or value; buttons get "_button", links
    "_link", selects "_select". Selector: #id, then [data-testid], tag[name=…],
    [aria-label], then a text/href selector. Duplicate keys get _2, _3 ….

    Args:
        elements (list[dict]): Element descriptors (see _ELEMENTS_JS).

    Returns:
        dict: {key: selector}. Never raises.
    """
    out: dict = {}
    for e in elements or []:
        try:
            tag, typ = e.get("tag", ""), e.get("type", "")
            role = e.get("role", "")
            is_button = tag == "button" or typ in ("submit", "button", "reset") or role == "button"
            if role in ("menuitem", "menuitemcheckbox", "menuitemradio", "option", "tab") and tag != "a":
                is_button = False
            is_link = tag == "a"
            ident = e.get("id", "")
            name_id = "" if _looks_generated(ident) else ident
            base = (e.get("label") or e.get("name") or (name_id[3:] if name_id.startswith("id_") else name_id)
                    or e.get("aria") or e.get("ph") or e.get("text") or e.get("value") or e.get("testid"))
            if is_button or is_link:
                base = e.get("text") or e.get("value") or e.get("aria") or base
            key = _slug(base)
            if not key:
                continue
            if role in ("menuitem", "menuitemcheckbox", "menuitemradio") and not key.endswith("item"):
                key += "_menu_item"
            elif role == "option" and not key.endswith("option"):
                key += "_option"
            elif role == "tab" and not key.endswith("tab"):
                key += "_tab"
            elif is_button and not key.endswith("button"):
                key += "_button"
            elif is_link and not key.endswith("link"):
                key += "_link"
            elif tag == "select" and not key.endswith("select"):
                key += "_select"
            href = e.get("href", "")
            href_has_id = bool(re.search(r"/(\d+|[0-9a-f]{8,}(-[0-9a-f]{4,})*)(/|$)", href, re.I))
            if e.get("sel"):
                sel = e["sel"]          # chosen in the page and checked to match one element
            elif ident and not any(c in ident for c in " .:[]#"):
                sel = f"#{ident}"
            elif is_link and href_has_id and e.get("text"):
                # an href with a record id only matches THIS database — the text is stable
                sel = f"a:has-text('{e['text']}')"
            elif e.get("testid"):
                sel = f"[data-testid='{e['testid']}']"
            elif e.get("name"):
                sel = f"{tag}[name='{e['name']}']"
            elif e.get("aria"):
                sel = f"{tag}[aria-label='{e['aria']}']"
            elif is_link and e.get("href"):
                sel = f"a[href='{e['href']}']"
            elif tag == "input" and e.get("value"):
                sel = f"input[type='{typ or 'submit'}'][value='{e['value']}']"
            elif e.get("text"):
                sel = f"{tag}:has-text('{e['text']}')"
            else:
                continue
            k = key
            if k in out and out[k] != sel and name_id:
                # a clearer name than name_2: the element's own id (add-check-name → add_check_name)
                k = _slug(name_id[3:] if name_id.startswith("id_") else name_id.replace("-", " "))
            n = 2
            base_k = k
            while k in out and out[k] != sel:
                k, n = f"{base_k}_{n}", n + 1
            out[k] = sel
        except Exception:  # noqa: BLE001
            continue
    return out


async def label_page(page, avoid_ids: "list | None" = None) -> "tuple[dict, list]":
    """
    Label the page's elements and say which are hidden right now (inside a closed menu or
    modal — a step must open it first; found on Healthchecks: "Log Out" in the account menu).

    Returns:
        (elements {key: selector}, hidden_keys [key, …]). Raises like label_elements_deterministic.
    """
    try:
        raw = await page.evaluate(_ELEMENTS_JS, list(avoid_ids or []))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"QAX could not read the page's elements: {str(e)[:200]}") from e
    if avoid_ids:
        for e in raw:                       # the Python fallback must not use them either
            if e.get("id") in avoid_ids:
                e["id"] = ""
    elements = label_elements(raw)
    hidden_sel = {e.get("sel") for e in raw if e.get("sel") and e.get("vis") is False}
    return elements, [k for k, sel in elements.items() if sel in hidden_sel]


async def label_elements_deterministic(page) -> dict:
    """
    Read the page's interactive elements and label them with label_elements().

    Raises:
        RuntimeError: when the page script itself fails — a silent {} made a broken
            labeller look like "0 elements on every page".
    """
    try:
        elements = await page.evaluate(_ELEMENTS_JS, [])
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"QAX could not read the page's elements: {str(e)[:200]}") from e
    return label_elements(elements)

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
        from core.env_loader import load_env
        load_env()

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

        try:
            # ── Step 1: Navigate ───────────────────────────────────────────
            logger.info("QAX DOMAnalyzer: Navigating to %s", page_url)
            await _goto_settled(playwright_page, page_url)

            # ── Step 2: Auto-login if needed ───────────────────────────────
            # Not when the page asked for IS the login page: logging in first labelled
            # the page behind it, so the login form's own fields were never recorded
            # (found on linkding, 2026-10-19).
            from urllib.parse import urlparse as _up
            where = f"{page_name} {_up(page_url).path}".lower()
            if not any(w in where for w in ("login", "signin", "sign_in", "sign-in", "logon")):
                await self.login_if_needed(playwright_page)

            # ── Step 3: Extract interactive elements ───────────────────────
            elements_summary = await self._extract_elements_summary(playwright_page)

            if not elements_summary.strip():
                logger.warning(
                    "QAX DOMAnalyzer: No interactive elements found on %s", page_url
                )
                return {}

            # ── Step 4-5: Label the elements ───────────────────────────────
            # Deterministic by default (nothing leaves the machine). Claude labels
            # them only when the operator opted in (discover --ai / QAX_AI_DISCOVER),
            # and its answer falls back to the deterministic one if it fails.
            selectors = {}
            if ai_discovery_enabled() and self._api_key:
                logger.info("QAX DOMAnalyzer: sending the element list of '%s' to Claude "
                            "for labelling (--ai)", page_name)
                selectors = await self._ask_claude_for_selectors(page_name, elements_summary)
            if not selectors:
                selectors = await label_elements_deterministic(playwright_page)

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

            # Only a REAL login form: a page with password fields is not always one (Django's
            # "add user" and "change password" pages) — filling those with the operator's
            # credentials submitted the add-user form (found on linkding, 2026-10-19).
            if not await playwright_page.evaluate(_LOGIN_FORM_JS):
                logger.info("QAX DOMAnalyzer: password field present but this is not a login form — skipped")
                return False

            if not self.username or not self.password:
                logger.warning(
                    "QAX DOMAnalyzer: Login page detected but "
                    "APP_USERNAME / APP_PASSWORD not set in .env."
                )
                return False

            logger.info("QAX DOMAnalyzer: Login page detected — attempting auto-login")

            # ── Fill username — in the SAME form as the password field ─────
            form = "form:has(input[type='password']) "
            username_sel = ", ".join(form + x for x in (
                "input[name='username']", "input[name='email']", "input[name='identity']",
                "input[name='login']", "input[type='email']", "input[type='text']"))
            username_input = (await playwright_page.query_selector(username_sel)
                              or await playwright_page.query_selector(
                                  "input[name='username'], input[name='email'], "
                                  "input[type='email'], input[type='text']"))
            if username_input:
                await username_input.fill(self.username)

            # ── Fill password ──────────────────────────────────────────────
            if password_input:
                await password_input.fill(self.password)

            # ── Submit the form ────────────────────────────────────────────
            # A <button> with no type submits its form (PocketBase's "Login" has none);
            # with no button at all, Enter in the password field submits.
            submit_sel = (form + "button[type='submit'], " + form + "input[type='submit'], "
                          + form + "button:not([type]), button.login-btn")
            submit_btn = await playwright_page.query_selector(submit_sel)
            if submit_btn:
                await submit_btn.click()
            elif password_input:
                await password_input.press("Enter")
            await _settle(playwright_page, timeout_ms=15_000)

            # ── Logged in? The password field is gone (SPA routes like #/login change
            # the URL a moment AFTER the auth call, so wait for it, bounded) ─────────
            import asyncio as _asyncio
            success = False
            for _ in range(20):
                still = await playwright_page.query_selector("input[type='password']")
                visible = bool(still) and await still.is_visible()
                if not visible and "login" not in playwright_page.url.lower().split("?")[0][-12:]:
                    success = True
                    break
                await _asyncio.sleep(0.4)
            post_url = playwright_page.url
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
