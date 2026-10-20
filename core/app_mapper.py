"""
core/app_mapper.py — read a running app from its URL (functional QA, step 1)
===========================================================================
`python main.py map --url URL --project P` walks the app in a real browser and writes
what it found, so test generation uses the app's REAL pages and elements instead of
guessing them:

  1. optional login (credentials from environment variables — never from a file of QAX);
  2. breadth-first over same-origin links, GET navigations only — a link that looks
     like it logs out or changes data (logout, delete, remove, …) is never followed,
     and no form is ever submitted;
  3. each page's interactive elements named deterministically (no AI);
  4. config/projects/<P>/app_map.json (pages → element keys → selectors) and the same
     keys merged into config/projects/<P>/selectors.json, which the browser engine reads.

AppMap.as_context() is the "OBSERVED UI SURFACE" block generation is grounded in;
check_cases() flags generated steps that use an element the map does not have.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config" / "projects"

#: Links never followed while mapping: they log out or change data on a GET.
RISKY_LINK = re.compile(r"(log-?out|sign-?out|log_out|sign_out|delete|remove|destroy|"
                        r"unsubscribe|deactivate|reset|revoke|/admin/)", re.I)

#: Action verbs: risky only when a path segment (or a query value) STARTS with one —
#: /create-api-token, /run, /export.csv, ?action=send — not inside a name
#: (/docs/measuring_script_run_time is a page, found on Healthchecks).
RISKY_VERB = re.compile(r"^(create|generate|regenerate|approve|reject|toggle|purge|clear|send|"
                        r"import|export|run|execute|trigger|pause|resume|close|cancel|disable)([-_.]|$)", re.I)

#: Pages read per parent path (/docs/a, /docs/b … share "/docs"). The next page read is
#: always one whose parent has been visited least, so one large section (e.g. /docs) cannot
#: use up the budget before the app's own pages are read (found on Healthchecks: 17 of 20
#: pages were /docs); the cap bounds a huge section even when nothing else is left.
PER_PARENT = 6

_LINKS_JS = """() => [...document.querySelectorAll('a[href]')].map(a => ({
  href: a.getAttribute('href') || '', text: (a.innerText || '').trim().slice(0, 40)}))"""

_FORMS_JS = """() => [...document.querySelectorAll('form')].map(f => ({
  method: (f.getAttribute('method') || 'get').toUpperCase(),
  action: f.getAttribute('action') || '',
  fields: [...f.querySelectorAll('input, select, textarea')]
    .filter(e => e.type !== 'hidden').map(e => e.getAttribute('name') || e.id || '').filter(Boolean)}))"""

_BUILTIN_KEYS = {"page", "page_text", "body"}


@dataclass
class AppMap:
    """What `map` saw: pages, their element keys and selectors, their forms."""

    base_url: str = ""
    login_ok: bool | None = None
    pages: list = field(default_factory=list)      # [{path, title, elements: {key: selector}, forms}]
    skipped_links: list = field(default_factory=list)
    explored_actions: list = field(default_factory=list)   # risky links followed (--explore-actions)
    writes: list = field(default_factory=list)             # data-changing requests sent while mapping
    error: str | None = None

    @property
    def keys(self) -> set:
        """Every element key on the map (plus the built-in `page`)."""
        out = set(_BUILTIN_KEYS)
        for p in self.pages:
            out.update((p.get("elements") or {}).keys())
        return out

    @property
    def selectors(self) -> set:
        """Every selector on the map (a generated step may use one directly)."""
        return {s for p in self.pages for s in (p.get("elements") or {}).values()}

    def as_context(self, max_pages: int = 25, max_elements: int = 40) -> str:
        """
        The OBSERVED UI SURFACE block injected into generation.

        Returns:
            str: Pages with their element keys and selectors, or "" when empty.
        """
        if not self.pages:
            return ""
        lines = ["OBSERVED UI SURFACE (read from the running app — these pages and elements are REAL).",
                 "In automation_steps use `navigate` with these paths and set selector_key to one of the",
                 "element KEYS below (left column). Use selector_key \"page\" to check a text anywhere on",
                 "the page (e.g. an error that appears after a click). Do not invent pages or elements."]
        for p in self.pages[:max_pages]:
            tag = "  [action page — opening it changes data or the session]" if p.get("action") else ""
            lines.append(f"\nPage {p.get('path')}" + (f" — {p['title']}" if p.get("title") else "") + tag)
            hidden = set(p.get("hidden") or [])
            opened_by = p.get("opened_by") or {}
            for k, sel in list((p.get("elements") or {}).items())[:max_elements]:
                note = ""
                if k in opened_by:
                    note = f"   (appears after clicking {opened_by[k]} — click it first)"
                elif k in hidden:
                    note = "   (hidden until its menu/dialog is opened — click the control that opens it first)"
                lines.append(f"  - {k}: {sel}{note}")
            for f in p.get("forms") or []:
                if f.get("fields"):
                    lines.append(f"  form {f.get('method')} {f.get('action') or p.get('path')}: "
                                 + ", ".join(f["fields"][:12]))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """JSON-safe dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AppMap":
        """Rebuild from to_dict() output (unknown keys ignored)."""
        d = d or {}
        return cls(base_url=d.get("base_url", ""), login_ok=d.get("login_ok"),
                   pages=list(d.get("pages") or []), skipped_links=list(d.get("skipped_links") or []),
                   explored_actions=list(d.get("explored_actions") or []),
                   writes=list(d.get("writes") or []), error=d.get("error"))


_READ_RPC = re.compile(r"^(get|list|batch_?get|batchget|search|find|query|count|read|fetch|check|"
                       r"validate|preview|refresh|lookup|describe|stat|head)", re.I)


def is_write_request(method: str, url: str, post_data: str | None = None) -> bool:
    """
    True when a request can change data. Not every POST does: RPC/gRPC-web apps read with
    POST (Memos: POST /memos.api.v1.AttachmentService/ListAttachments — found 2026-10-20)
    and GraphQL reads are POSTed queries. A POST whose RPC method name starts with a read
    verb, or a GraphQL body that is a `query` (no `mutation`), is a read.
    """
    m = (method or "GET").upper()
    if m in ("GET", "HEAD", "OPTIONS"):
        return False
    last = urlparse(url or "").path.rstrip("/").rsplit("/", 1)[-1]
    if m == "POST" and (_READ_RPC.match(last) or re.search(r"(^|[-_])refresh$|^realtime$|^subscribe", last, re.I)):
        return False                       # a read, a session refresh, a live-updates subscription
    body = (post_data or "")[:2000]
    if m == "POST" and urlparse(url or "").path.rstrip("/").endswith("graphql") and body:
        return bool(re.search(r"\bmutation\b", body))
    return True


def map_path(project: str) -> Path:
    """config/projects/<project>/app_map.json"""
    return CONFIG_DIR / project / "app_map.json"


def load_map(project: str | None) -> AppMap | None:
    """The saved map of a project, or None. Never raises."""
    if not project or not re.fullmatch(r"[A-Za-z0-9_-]+", project):
        return None
    try:
        return AppMap.from_dict(json.loads(map_path(project).read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return None


def _write_atomic(path: Path, text: str) -> None:
    """Write a file so a reader never sees half of it (temp file + rename)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_map(app_map: AppMap, project: str) -> Path:
    """
    Write app_map.json and merge the element keys into selectors.json (one section per
    page, e.g. "bookmarks_new"), keeping every section that is already there.
    """
    folder = CONFIG_DIR / project
    folder.mkdir(parents=True, exist_ok=True)
    path = map_path(project)
    _write_atomic(path, json.dumps(app_map.to_dict(), indent=2) + "\n")
    sel_path = folder / "selectors.json"
    try:
        sel = json.loads(sel_path.read_text(encoding="utf-8")) if sel_path.exists() else {}
    except Exception:  # noqa: BLE001
        sel = {}
    sel.setdefault("_meta", {"auto_discovered": True, "manual_overrides": []})
    meta = sel.get("_meta") if isinstance(sel.get("_meta"), dict) else {}
    hand_written = meta.get("auto_discovered") is False
    manual = set(meta.get("manual_overrides") or [])
    for p in app_map.pages:
        section = re.sub(r"[^a-z0-9]+", "_", (p.get("path") or "/").lower()).strip("_") or "home"
        fresh = dict(p.get("elements") or {})
        old = sel.get(section) if isinstance(sel.get(section), dict) else {}
        # Re-reading must not break existing test cases: a hand-written file (or a key the
        # operator pinned in manual_overrides) keeps its names; an auto-discovered name
        # survives when its selector is still on the page (an alias). Stale ones go.
        merged = dict(fresh)
        live = set(fresh.values())
        for k, v in old.items():
            if hand_written or k in manual or f"{section}.{k}" in manual:
                merged[k] = v
            elif k not in merged and v in live:
                merged[k] = v
        sel[section] = merged
    _write_atomic(sel_path, json.dumps(sel, indent=2) + "\n")
    return path


def page_template(url: str) -> str:
    """
    One key per kind of page: the path with ids replaced by {id}, query ignored —
    /bookmarks/2/edit?return_url=… and /bookmarks/1/edit are the same page
    (/bookmarks/{id}/edit), and ?page=1 / ?details=2 views do not use up the budget.
    """
    u = urlparse(url)

    def _tpl(path: str) -> str:
        path = path.rstrip("/") or "/"
        parts = [("{id}" if re.fullmatch(r"\d+|[0-9a-f]{8,}(-[0-9a-f]{4,})*|[a-z0-9]{15}", seg, re.I)
                  and re.search(r"\d", seg) else seg) for seg in path.split("/")]
        return "/".join(parts) or "/"
    out = _tpl(u.path)
    # Single-page apps route in the fragment: /_/#/collections is its own page.
    if u.fragment.startswith("/"):
        out += "#" + _tpl(u.fragment.split("?")[0])
    return out


def is_risky_link(href: str, text: str = "") -> bool:
    """True for a link that would log out or change data if followed."""
    if RISKY_LINK.search(href or "") or RISKY_LINK.search(text or ""):
        return True
    p = urlparse(href or "")
    parts = [seg for seg in p.path.split("/") if seg]
    parts += [v for kv in p.query.split("&") for v in kv.split("=")[1:] if v]
    if p.fragment.startswith("/"):                       # SPA route: #/settings/export-collections
        frag = urlparse(p.fragment)
        parts += [seg for seg in frag.path.split("/") if seg]
        parts += [v for kv in frag.query.split("&") for v in kv.split("=")[1:] if v]
    return any(RISKY_VERB.match(seg) for seg in parts)


async def map_app(base_url: str, *, start_paths: list | None = None, max_pages: int = 20,
                  username: str = "", password: str = "", headless: bool = True,
                  insecure: bool = False, explore_actions: bool = False,
                  open_menus: bool = True, storage_state: "str | None" = None) -> AppMap:
    """
    Walk the running app and return its map (see module docstring). Never raises.

    Args:
        base_url (str): Root URL of the app.
        start_paths (list | None): Extra paths to start from (e.g. ["/bookmarks"]).
        max_pages (int): Hard cap on pages visited.
        username, password (str): Optional login (values, not names — the CLI reads
            them from environment variables).
        storage_state (str | None): A session the operator recorded by hand
            (core.session_state — SSO / 2FA apps); the walk starts logged in.
        headless (bool): Run without a window.
        insecure (bool): Accept an unverified TLS certificate (staging only).
        explore_actions (bool): ALSO follow the risky links (create/edit/delete/logout…)
            — for a disposable staging app: pages behind them are mapped too (marked
            "action"), the session is re-opened after a logout, and every data-changing
            request is recorded in `writes`. Off by default: reading must not change data.
        open_menus (bool): Click controls that open a menu or dialog (aria-haspopup,
            aria-expanded, data-(bs-)toggle, <summary>) and record what they reveal
            ("opened_by"), so generation knows "click the account menu, then Log out".
    """
    from core.dom_analyzer import (DOMAnalyzer, _goto_settled, _wait_dom_stable,
                                   label_elements_deterministic, label_page)

    base_url = base_url.rstrip("/")
    origin = urlparse(base_url)
    result = AppMap(base_url=base_url)
    try:
        from playwright.async_api import async_playwright
    except Exception:  # noqa: BLE001
        result.error = "Playwright is not installed (pip install playwright && playwright install chromium)."
        return result

    def _norm(u: str) -> str:
        return page_template(u)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless)
            context = await browser.new_context(ignore_https_errors=insecure,
                                                **({"storage_state": storage_state} if storage_state else {}))

            logging_in = {"now": False}       # a login POST is expected, not a data change

            def _on_request(req):
                try:
                    if is_write_request(req.method, req.url, req.post_data):
                        result.writes.append({"method": req.method.upper(), "path": urlparse(req.url).path,
                                              **({"login": True} if logging_in["now"] else {})})
                except Exception:  # noqa: BLE001
                    pass
            context.on("request", _on_request)
            page = await context.new_page()

            async def _relogin() -> None:
                """After a logout (explore mode) open a session again."""
                if not (username and password):
                    return
                saved = {k: os.environ.get(k) for k in ("APP_USERNAME", "APP_PASSWORD", "APP_BASE_URL")}
                os.environ["APP_USERNAME"], os.environ["APP_PASSWORD"] = username, password
                os.environ["APP_BASE_URL"] = base_url        # the login stays on this app
                try:
                    # never fill a form on the current page (it may be "add user"): start over
                    await _goto_settled(page, base_url + "/")
                    logging_in["now"] = True
                    await DOMAnalyzer().login_if_needed(page)
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    logging_in["now"] = False
                    for k, v in saved.items():
                        if v is None:
                            os.environ.pop(k, None)
                        else:
                            os.environ[k] = v

            try:
                if username and password:
                    saved = {k: os.environ.get(k) for k in ("APP_USERNAME", "APP_PASSWORD", "APP_BASE_URL")}
                    os.environ["APP_USERNAME"], os.environ["APP_PASSWORD"] = username, password
                    os.environ["APP_BASE_URL"] = base_url        # the login stays on this app
                    try:
                        await _goto_settled(page, base_url + "/")
                        if not await page.query_selector("input[type='password']"):
                            # the home page is public: follow ITS login link (Flaskr: /auth/login),
                            # then the usual paths
                            candidates = []
                            try:
                                candidates = await page.evaluate(_LOGIN_LINKS_JS)
                            except Exception:  # noqa: BLE001
                                pass
                            candidates += [base_url + p for p in ("/login/", "/login", "/accounts/login/",
                                                                  "/auth/login", "/users/sign_in", "/signin",
                                                                  "/user/login")]
                            for cand in candidates:
                                if urlparse(cand).netloc != origin.netloc:
                                    continue
                                try:
                                    resp = await page.goto(cand, wait_until="load")
                                    await _wait_dom_stable(page, max_ms=3_000)
                                except Exception:  # noqa: BLE001
                                    continue
                                if (resp is None or resp.status < 400) and \
                                        await page.query_selector("input[type='password']"):
                                    break
                        if await page.query_selector("input[type='password']"):
                            # record the login page itself — test cases log in through it
                            login_url = page.url
                            l_el, l_hidden = await label_page(page)            # raises if broken
                            lu = urlparse(login_url)
                            result.pages.append({
                                "path": page_template(login_url),
                                "example": lu.path + (("#" + lu.fragment) if lu.fragment.startswith("/") else ""),
                                "title": await page.title(),
                                "elements": l_el, "hidden": l_hidden,
                                "forms": await page.evaluate(_FORMS_JS)})
                        # logged in only if a login really happened and the form is gone — not
                        # merely "no password field here" (a 404 page has none: found on Flaskr)
                        logging_in["now"] = True
                        try:
                            did_login = bool(await DOMAnalyzer().login_if_needed(page))
                        finally:
                            logging_in["now"] = False
                        result.login_ok = did_login and \
                            not await page.query_selector("input[type='password']")
                        # SPA: the login form and the signed-in home share one URL (Beszel "/"):
                        # keep the login page under its own name so the home page is still read
                        if result.login_ok and result.pages and \
                                result.pages[-1]["path"] == page_template(page.url):
                            result.pages[-1]["path"] += " [login]"
                    finally:
                        for k, v in saved.items():
                            if v is None:
                                os.environ.pop(k, None)
                            else:
                                os.environ[k] = v
                queue = [base_url + "/"] + [urljoin(base_url + "/", s.lstrip("/")) for s in (start_paths or [])]
                if result.login_ok and page.url and urlparse(page.url).netloc == origin.netloc:
                    queue.insert(0, page.url)                      # where the login landed
                queue += await _sitemap_urls(page, base_url)       # routes the app lists itself
                action_queue: list = []                            # risky links, explore mode only
                seen: set = {p["path"] for p in result.pages}
                per_parent: dict = {}

                def _parent(k: str) -> str:
                    return k.rsplit("/", 1)[0] or "/"

                while (queue or action_queue) and len(result.pages) < max_pages:
                    is_action = not queue
                    if is_action:
                        # explore mode: risky links last, logout-style ones at the very end
                        action_queue.sort(key=lambda u: 1 if RISKY_LINK.search(u) and
                                          re.search(r"log-?out|sign-?out", u, re.I) else 0)
                        url = action_queue.pop(0)
                        result.explored_actions.append(urlparse(url).path + (("#" + urlparse(url).fragment)
                                                                            if urlparse(url).fragment else ""))
                    else:
                        # least-visited parent first (stable: earlier links win ties)
                        i = min(range(len(queue)), key=lambda j: per_parent.get(_parent(_norm(queue[j])), 0))
                        url = queue.pop(i)
                    key = _norm(url)
                    if key in seen or urlparse(url).netloc != origin.netloc:
                        continue
                    if per_parent.get(_parent(key), 0) >= PER_PARENT:
                        continue
                    per_parent[_parent(key)] = per_parent.get(_parent(key), 0) + 1
                    seen.add(key)
                    try:
                        resp = await page.goto(url, wait_until="load", timeout=30_000)
                        from core.dom_analyzer import _settle
                        await _settle(page)
                        await _wait_dom_stable(page)
                    except Exception as exc:  # noqa: BLE001
                        logger.info("QAX map: could not open %s: %s", url, exc)
                        continue
                    if resp is not None and resp.status >= 400:
                        logger.info("QAX map: %s answered %s — not a page of the app", url, resp.status)
                        continue
                    from core.origin_guard import is_allowed
                    if not is_allowed(page.url, base_url):
                        # a link or redirect left the app: never read (or log in to) another site
                        logger.info("QAX map: %s led off the app to %s — skipped", url, page.url[:100])
                        result.skipped_links.append(f"{url} → {page.url[:80]} (off-origin)")
                        continue
                    landed = _norm(page.url)
                    if is_action and username and re.search(r"log-?out|sign-?out", url, re.I):
                        await _relogin()                           # the action ended the session
                        continue
                    if landed != key and landed in seen:
                        continue                                   # a redirect to a page we have
                    seen.add(landed)
                    try:
                        elements, hidden = await label_page(page)
                        elements, hidden = await _stable_labels(page, elements, hidden)
                    except RuntimeError as exc:
                        result.error = str(exc)            # a broken reader is an error, not "0 elements"
                        break
                    try:
                        forms = await page.evaluate(_FORMS_JS)
                    except Exception:  # noqa: BLE001
                        forms = []
                    p_url = urlparse(page.url)
                    example = p_url.path + (("?" + p_url.query) if p_url.query else "") \
                        + (("#" + p_url.fragment) if p_url.fragment.startswith("/") else "")
                    opened_by: dict = {}
                    if open_menus and not is_action:
                        elements, hidden, opened_by = await _open_menus(page, elements, hidden,
                                                                         allow_risky=explore_actions)
                    entry = {"path": landed, "example": example,
                             "title": await page.title(), "elements": elements,
                             "hidden": hidden, "forms": forms}
                    if opened_by:
                        entry["opened_by"] = opened_by
                    if is_action:
                        entry["action"] = True
                    result.pages.append(entry)
                    try:
                        links = await page.evaluate(_LINKS_JS)
                    except Exception:  # noqa: BLE001
                        links = []
                    for ln in links:
                        href = ln.get("href", "")
                        if not href or href.startswith(("mailto:", "javascript:", "tel:")):
                            continue
                        if href.startswith("#") and not href.startswith("#/"):
                            continue                               # an anchor, not an SPA route
                        nxt = urljoin(page.url, href)
                        if urlparse(nxt).netloc != origin.netloc:
                            continue
                        if is_risky_link(href, ln.get("text", "")):
                            if explore_actions:
                                if _norm(nxt) not in seen and nxt not in action_queue:
                                    action_queue.append(nxt)
                            elif href not in result.skipped_links:
                                result.skipped_links.append(href)
                            continue
                        if _norm(nxt) not in seen:
                            queue.append(nxt)
            finally:
                await context.close()
                await browser.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("QAX map failed: %s", exc)
        result.error = str(exc)[:300]
    return result


async def _sitemap_urls(page, base_url: str, limit: int = 50) -> list:
    """Same-origin URLs from /sitemap.xml, if the app has one. Never raises."""
    try:
        resp = await page.request.get(base_url + "/sitemap.xml", timeout=5_000)
        if not resp.ok:
            return []
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", await resp.text())
        host = urlparse(base_url).netloc
        return [u for u in locs if urlparse(u).netloc == host][:limit]
    except Exception:  # noqa: BLE001
        return []


_LOGIN_LINKS_JS = r"""() => [...document.querySelectorAll('a[href]')]
  .filter(a => /\blog\s?-?in\b|\bsign\s?-?in\b/i.test((a.innerText || '') + ' ' + a.getAttribute('href')))
  .map(a => a.href)"""


_OPENERS_JS = r"""() => [...document.querySelectorAll(
  "[aria-haspopup]:not([aria-haspopup='false']), [aria-expanded='false'], [data-bs-toggle], [data-toggle], summary")]
  .filter(e => e.offsetWidth || e.offsetHeight)
  // e.type is what the browser does: a <button> with no type inside a form is "submit" and
  // would SEND the form — never click those while reading (found on Django admin, linkding)
  .filter(e => !((e.tagName === 'BUTTON' || e.tagName === 'INPUT') && e.form && (e.type || '').toLowerCase() !== 'button'))
  .map(e => ({text: (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 40),
              type: (e.type || e.getAttribute('type') || '').toLowerCase(), tag: e.tagName.toLowerCase()}))"""


async def _open_menus(page, elements: dict, hidden: list, allow_risky: bool = False,
                      max_openers: int = 8) -> "tuple[dict, list, dict]":
    """
    Click the controls that open a menu or dialog and record what each one reveals, so a
    generated test knows the step it needs first (Healthchecks: "Log Out" lives in the
    account menu; PocketBase: "Logout" in the user menu). Elements that only exist once the
    dialog is open are added. A control whose text looks risky (delete/logout…) is not
    clicked unless allow_risky. The page is restored (Escape, then reload if needed).
    Returns (elements, hidden, opened_by {revealed_key: opener_key}). Never raises.
    """
    from core.dom_analyzer import _settle, _wait_dom_stable, label_page
    opened_by: dict = {}
    try:
        openers = await page.evaluate(_OPENERS_JS)
    except Exception:  # noqa: BLE001
        return elements, hidden, opened_by
    start_url = page.url
    by_sel = {v: k for k, v in elements.items()}
    for op in openers[:max_openers]:
        text = op.get("text") or ""
        if op.get("type") == "submit" or (not allow_risky and is_risky_link("", text)):
            continue
        opener_key = None
        for k, sel in elements.items():           # find the opener's own key by its text
            if text and text.lower() in k.replace("_", " ") or (text and f"'{text}'" in sel):
                opener_key = k
                break
        if not opener_key or opener_key in hidden:
            continue
        try:
            await page.click(elements[opener_key], timeout=3_000)
            await page.wait_for_timeout(400)
            await _wait_dom_stable(page, max_ms=2_000)
            if page.url != start_url:
                # it navigated (a route change), it did not open a menu — attribute nothing
                await page.goto(start_url, wait_until="load")
                await _settle(page)
                continue
            now, now_hidden = await label_page(page)
            for k, sel in now.items():
                if k == opener_key or k in now_hidden:
                    continue
                if k not in elements and sel not in by_sel:
                    elements[k] = sel                  # exists only while the dialog is open
                    hidden.append(k)
                    opened_by[k] = opener_key
                elif k in hidden and k not in opened_by:
                    opened_by[k] = opener_key          # was hidden, this control shows it
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
            if page.url != start_url:
                await page.goto(start_url, wait_until="load")
                await _settle(page)
        except Exception:  # noqa: BLE001
            try:
                await page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            continue
    return elements, hidden, opened_by


async def _stable_labels(page, elements: dict, hidden: list) -> "tuple[dict, list]":
    """
    Reload the page and keep only selectors that still match exactly one element.
    Frameworks generate ids per render (Svelte: #accent_xleodvxk — found on PocketBase);
    such an id is unique now but gone after a reload, so every test using it would fail.
    Those ids are avoided and the page is named again with stable selectors. Never raises
    for a reload problem (the first reading is kept).
    """
    from core.dom_analyzer import _settle, _wait_dom_stable, label_page
    try:
        await page.reload(wait_until="load")
        await _settle(page)
        await _wait_dom_stable(page)
    except Exception:  # noqa: BLE001
        return elements, hidden
    unstable = []
    for sel in elements.values():
        if not sel.startswith("#"):
            continue
        try:
            if await page.locator(sel).count() != 1:
                unstable.append(sel)
        except Exception:  # noqa: BLE001
            unstable.append(sel)
    if not unstable:
        return await _drop_ambiguous(page, elements, hidden)
    ids = []
    for sel in unstable:
        raw = sel[1:].split(" ")[0]
        ids.append(re.sub(r"\\(.)", r"\1", raw))       # undo CSS.escape (meta\.appName → meta.appName)
    # also avoid the CURRENT render's version of the same generated ids: same prefix, new suffix
    prefixes = {re.sub(r"[_-]?[a-z0-9]{6,}$", "", i, flags=re.I) for i in ids}
    current = await page.evaluate("() => [...document.querySelectorAll('[id]')].map(e => e.id)")
    ids += [i for i in current if any(i.startswith(pf) and i != pf for pf in prefixes if pf)]
    elements, hidden = await label_page(page, avoid_ids=ids)
    return await _drop_ambiguous(page, elements, hidden)


async def _drop_ambiguous(page, elements: dict, hidden: list) -> "tuple[dict, list]":
    """
    Last check, with Playwright itself: a selector that does not match exactly one
    element is dropped — a missing name gives a clear ERROR, a wrong one clicks something
    else. Never raises.
    """
    import asyncio

    async def _counts() -> dict:
        out = {}
        for k, sel in elements.items():
            try:
                out[k] = await page.locator(sel).count()
            except Exception:  # noqa: BLE001
                out[k] = -1                                   # invalid selector
        return out
    # An SPA renders parts of the page after its own API calls (PocketBase's settings
    # form): give missing elements a few short chances to appear before dropping them.
    counts = await _counts()
    for _ in range(8):
        if not any(n == 0 for n in counts.values()):
            break
        await asyncio.sleep(0.5)
        counts = await _counts()
    keep = {}
    for k, sel in elements.items():
        if counts.get(k) == 1:
            keep[k] = sel
        else:
            logger.info("QAX map: dropped %s (%s) — %s element(s) after a reload", k, sel, counts.get(k))
    return keep, [h for h in hidden if h in keep]


def check_cases(cases: list, app_map: AppMap | None) -> int:
    """
    Flag generated steps that use an element the map does not have.

    Each case gets `unmapped_elements` (list) and, when not empty, needs_review=True —
    an AI-proposed step on a non-existent element would fail for the wrong reason. The executor
    holds such cases back (SKIP, cause "test") until reviewed — see test_executor.grounding_block.

    Returns:
        int: Number of cases flagged. 0 when there is no map. Never raises.
    """
    if not app_map or not app_map.pages:
        return 0
    keys, sels, flagged = app_map.keys, app_map.selectors, 0
    for tc in cases or []:
        try:
            missing = []
            for st in tc.get("automation_steps") or []:
                k = str(st.get("selector_key") or "").strip()
                if not k or k in keys or k in sels or k.lower() in _BUILTIN_KEYS:
                    continue
                if (st.get("playwright_action") or "").lower() in ("navigate", "goto", "open", "visit", "wait"):
                    continue
                missing.append(k)
            tc["unmapped_elements"] = sorted(set(missing))
            if missing:
                tc["needs_review"] = True
                flagged += 1
        except Exception:  # noqa: BLE001
            continue
    return flagged
