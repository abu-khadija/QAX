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
            lines.append(f"\nPage {p.get('path')}" + (f" — {p['title']}" if p.get("title") else ""))
            hidden = set(p.get("hidden") or [])
            for k, sel in list((p.get("elements") or {}).items())[:max_elements]:
                lines.append(f"  - {k}: {sel}" + ("   (hidden until its menu/dialog is opened — "
                                                   "click the control that opens it first)" if k in hidden else ""))
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
                   error=d.get("error"))


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


def save_map(app_map: AppMap, project: str) -> Path:
    """
    Write app_map.json and merge the element keys into selectors.json (one section per
    page, e.g. "bookmarks_new"), keeping every section that is already there.
    """
    folder = CONFIG_DIR / project
    folder.mkdir(parents=True, exist_ok=True)
    path = map_path(project)
    path.write_text(json.dumps(app_map.to_dict(), indent=2) + "\n", encoding="utf-8")
    sel_path = folder / "selectors.json"
    try:
        sel = json.loads(sel_path.read_text(encoding="utf-8")) if sel_path.exists() else {}
    except Exception:  # noqa: BLE001
        sel = {}
    sel.setdefault("_meta", {"auto_discovered": True, "manual_overrides": []})
    for p in app_map.pages:
        section = re.sub(r"[^a-z0-9]+", "_", (p.get("path") or "/").lower()).strip("_") or "home"
        sel[section] = dict(p.get("elements") or {})
    sel_path.write_text(json.dumps(sel, indent=2) + "\n", encoding="utf-8")
    return path


def page_template(url: str) -> str:
    """
    One key per kind of page: the path with ids replaced by {id}, query ignored —
    /bookmarks/2/edit?return_url=… and /bookmarks/1/edit are the same page
    (/bookmarks/{id}/edit), and ?page=1 / ?details=2 views do not use up the budget.
    """
    path = urlparse(url).path.rstrip("/") or "/"
    parts = [("{id}" if re.fullmatch(r"\d+|[0-9a-f]{8,}(-[0-9a-f]{4,})*", seg, re.I) else seg)
             for seg in path.split("/")]
    return "/".join(parts) or "/"


def is_risky_link(href: str, text: str = "") -> bool:
    """True for a link that would log out or change data if followed."""
    if RISKY_LINK.search(href or "") or RISKY_LINK.search(text or ""):
        return True
    p = urlparse(href or "")
    parts = [seg for seg in p.path.split("/") if seg]
    parts += [v for kv in p.query.split("&") for v in kv.split("=")[1:] if v]
    return any(RISKY_VERB.match(seg) for seg in parts)


async def map_app(base_url: str, *, start_paths: list | None = None, max_pages: int = 20,
                  username: str = "", password: str = "", headless: bool = True,
                  insecure: bool = False) -> AppMap:
    """
    Walk the running app and return its map (see module docstring). Never raises.

    Args:
        base_url (str): Root URL of the app.
        start_paths (list | None): Extra paths to start from (e.g. ["/bookmarks"]).
        max_pages (int): Hard cap on pages visited.
        username, password (str): Optional login (values, not names — the CLI reads
            them from environment variables).
        headless (bool): Run without a window.
        insecure (bool): Accept an unverified TLS certificate (staging only).
    """
    from core.dom_analyzer import DOMAnalyzer, _goto_settled, label_elements_deterministic, label_page

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
            context = await browser.new_context(ignore_https_errors=insecure)
            page = await context.new_page()
            try:
                if username and password:
                    saved = {k: os.environ.get(k) for k in ("APP_USERNAME", "APP_PASSWORD")}
                    os.environ["APP_USERNAME"], os.environ["APP_PASSWORD"] = username, password
                    try:
                        await _goto_settled(page, base_url + "/")
                        if not await page.query_selector("input[type='password']"):
                            # the home page is public: open the usual login path
                            for path in ("/login/", "/login", "/accounts/login/", "/signin"):
                                try:
                                    await _goto_settled(page, base_url + path)
                                except Exception:  # noqa: BLE001
                                    continue
                                if await page.query_selector("input[type='password']"):
                                    break
                        if await page.query_selector("input[type='password']"):
                            # record the login page itself — test cases log in through it
                            login_url = page.url
                            result.pages.append({
                                "path": page_template(login_url),
                                "example": urlparse(login_url).path,
                                "title": await page.title(),
                                "elements": await label_elements_deterministic(page),  # raises if broken
                                "forms": await page.evaluate(_FORMS_JS)})
                        await DOMAnalyzer().login_if_needed(page)
                        result.login_ok = not await page.query_selector("input[type='password']")
                    finally:
                        for k, v in saved.items():
                            if v is None:
                                os.environ.pop(k, None)
                            else:
                                os.environ[k] = v
                queue = [base_url + "/"] + [urljoin(base_url + "/", s.lstrip("/")) for s in (start_paths or [])]
                if username and page.url and urlparse(page.url).netloc == origin.netloc:
                    queue.insert(0, page.url)                      # where the login landed
                seen: set = {p["path"] for p in result.pages}
                per_parent: dict = {}

                def _parent(k: str) -> str:
                    return k.rsplit("/", 1)[0] or "/"

                while queue and len(result.pages) < max_pages:
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
                        await _goto_settled(page, url)
                    except Exception as exc:  # noqa: BLE001
                        logger.info("QAX map: could not open %s: %s", url, exc)
                        continue
                    landed = _norm(page.url)
                    if landed != key and landed in seen:
                        continue                                   # a redirect to a page we have
                    seen.add(landed)
                    try:
                        elements, hidden = await label_page(page)
                    except RuntimeError as exc:
                        result.error = str(exc)            # a broken reader is an error, not "0 elements"
                        break
                    try:
                        forms = await page.evaluate(_FORMS_JS)
                    except Exception:  # noqa: BLE001
                        forms = []
                    p_url = urlparse(page.url)
                    result.pages.append({"path": landed, "example": p_url.path + (("?" + p_url.query) if p_url.query else ""),
                                         "title": await page.title(), "elements": elements,
                                         "hidden": hidden, "forms": forms})
                    try:
                        links = await page.evaluate(_LINKS_JS)
                    except Exception:  # noqa: BLE001
                        links = []
                    for ln in links:
                        href = ln.get("href", "")
                        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
                            continue
                        nxt = urljoin(page.url, href)
                        if urlparse(nxt).netloc != origin.netloc:
                            continue
                        if is_risky_link(href, ln.get("text", "")):
                            if href not in result.skipped_links:
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


def check_cases(cases: list, app_map: AppMap | None) -> int:
    """
    Flag generated steps that use an element the map does not have.

    Each case gets `unmapped_elements` (list) and, when not empty, needs_review=True —
    an AI-proposed step on a non-existent element would fail for the wrong reason.

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
