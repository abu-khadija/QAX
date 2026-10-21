"""
core/recording_import.py — a flow recorded in the browser becomes a QAX test case
=================================================================================
Record the flow once, run it on every change. No AI and no credit: the recording is the test.

Formats:
  * Chrome DevTools Recorder — JSON export ({"title", "steps": [{"type": "navigate"|"click"|"change"|
    "keyDown"|"waitForElement"|…, "selectors": [[…]], "assertedEvents": […]}]})
  * Playwright codegen — Python (`page.get_by_role("button", name="Save").click()`) or
    JavaScript/TypeScript (`await page.getByRole('button', { name: 'Save' }).click();`)

What it guarantees:
  * selectors become Playwright selectors the engine runs as they are (role=, text=, xpath=, CSS,
    internal:label=); the most stable one is preferred (id / data-testid / CSS, then role, then text);
  * a typed password NEVER reaches the case file: it becomes {{env:QAX_VAR_PASSWORD}} (listed in
    `needs_env`) — the value is dropped;
  * URLs become paths, so the same case runs on staging, CI or a laptop (the recorded origin is kept as
    `recorded_on`);
  * recorded checks become assertions (Chrome: waitForElement, navigation events; Playwright: expect(…));
    a recording with no check is imported but marked `needs_review` — it would only prove the flow
    clicks through, not that the app did the right thing.
Never raises: unknown lines/steps are listed in `skipped`.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

PASSWORD_VAR = "{{env:QAX_VAR_PASSWORD}}"


def detect(text: str) -> str:
    """'chrome' | 'playwright-python' | 'playwright-js' | 'qax' | '' for unknown."""
    t = (text or "").lstrip()
    if t.startswith("{"):
        try:
            d = json.loads(t)
        except Exception:  # noqa: BLE001
            return ""
        if isinstance(d, dict) and isinstance(d.get("steps"), list) and any(isinstance(s, dict) and "type" in s
                                                                          for s in d["steps"]):
            return "chrome"
        if isinstance(d, dict) and "test_cases" in d:
            return "qax"
        return ""
    if re.search(r"\bpage\.(getBy\w+|waitFor\w*)\(|\bawait\s+page\.|\btest\(", t):
        return "playwright-js"
    if re.search(r"\bpage\.(goto|get_by_\w+|locator)\(", t):
        return "playwright-python"
    return ""


def convert(text: str, *, case_id: str = "", title: str = "") -> dict:
    """
    Convert a recording into one QAX test case.

    Returns:
        dict: {"format", "case": {...} | None, "needs_env": [...], "skipped": [...], "warnings": [...]}
    """
    fmt = detect(text)
    try:
        if fmt == "chrome":
            steps, meta = _from_chrome(json.loads(text))
        elif fmt in ("playwright-python", "playwright-js"):
            steps, meta = _from_playwright(text, fmt)
        else:
            return {"format": fmt or "unknown", "case": None, "needs_env": [], "skipped": [],
                    "warnings": ["not a Chrome Recorder JSON export or a Playwright codegen script"]}
    except Exception as exc:  # noqa: BLE001
        return {"format": fmt, "case": None, "needs_env": [], "skipped": [], "warnings": [f"could not read it: {exc}"]}
    for i, s in enumerate(steps, 1):
        s["step_number"] = i
    has_check = any(s.get("playwright_action") == "assert" for s in steps)
    warnings = list(meta["warnings"])
    if not has_check:
        warnings.append("the recording has no check — add an assertion (what the page must show), or the case only "
                        "proves the flow clicks through")
    name = title or meta.get("title") or "Recorded flow"
    case = {"test_case_id": case_id or "REC-" + re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:24],
            "title": name, "test_type": "functional", "priority": "medium", "expected_outcome": "PASS",
            "source": f"recording ({fmt})", "recorded_on": meta.get("origin", ""),
            "needs_review": not has_check, "automation_steps": steps}
    if meta["needs_env"]:
        case["needs_env"] = sorted(meta["needs_env"])
    return {"format": fmt, "case": case, "needs_env": sorted(meta["needs_env"]), "skipped": meta["skipped"],
            "warnings": warnings}


# ── Chrome DevTools Recorder ────────────────────────────────────────────────


def _pick_chrome(selectors: list) -> str:
    """The most stable selector of a Chrome step: CSS (#id, [data-*]), then aria, xpath, text."""
    flat = [s[0] if isinstance(s, list) and s else s for s in (selectors or [])]
    flat = [str(s) for s in flat if s and not (isinstance(s, list) and len(s) > 1)]
    css = [s for s in flat if not re.match(r"^(aria|xpath|pierce|text)/", s)]
    for s in css:
        if s.startswith("#") or "data-testid" in s or "data-test" in s:
            return s
    aria = [s for s in flat if s.startswith("aria/")]
    for s in aria:
        m = re.match(r'^aria/(.+?)\[role="([a-z]+)"\]$', s)
        if m:
            return f'role={m.group(2)}[name="{_q(m.group(1))}"]'
    if css:
        return css[0]
    if aria:
        return f'internal:label="{_q(aria[0][5:])}"'
    for s in flat:
        if s.startswith("xpath/"):
            return "xpath=" + s[6:]
        if s.startswith("text/"):
            return f'text="{_q(s[5:])}"'
    return ""


def _from_chrome(d: dict):
    steps, skipped, warnings, env, origin = [], [], [], set(), ""
    for st in d.get("steps") or []:
        typ = st.get("type")
        sel = _pick_chrome(st.get("selectors"))
        if typ == "navigate":
            path, origin = _path(st.get("url", ""), origin)
            steps.append({"playwright_action": "navigate", "value": path})
        elif typ in ("click", "doubleClick"):
            if not sel:
                skipped.append(f"{typ}: no usable selector")
                continue
            steps.append({"playwright_action": "click", "selector_key": sel})
        elif typ == "change":
            value = str(st.get("value", ""))
            if _is_password(sel, st):
                value = PASSWORD_VAR
                env.add("QAX_VAR_PASSWORD")
            steps.append({"playwright_action": "fill", "selector_key": sel, "value": value})
        elif typ == "keyDown" and st.get("key") == "Enter":
            steps.append({"playwright_action": "key", "selector_key": steps[-1].get("selector_key", "")
                          if steps else "", "value": "Enter"})
        elif typ == "keyUp" or typ == "setViewport" or typ == "scroll" or typ == "hover":
            continue
        elif typ == "waitForElement":
            if not sel:
                skipped.append("waitForElement: no usable selector")
                continue
            visible = st.get("visible", True)
            steps.append({"playwright_action": "assert", "selector_key": sel,
                          "assertion_type": "visible" if visible else "not_visible"})
        else:
            skipped.append(f"step type '{typ}'")
            continue
        for ev in st.get("assertedEvents") or []:
            if ev.get("type") == "navigation" and ev.get("url"):
                path, origin = _path(ev["url"], origin)
                steps.append({"playwright_action": "assert", "assertion_type": "url", "assertion_value": path})
    return steps, {"title": d.get("title"), "origin": origin, "skipped": skipped, "warnings": warnings,
                   "needs_env": env}


def _is_password(sel: str, st: dict) -> bool:
    """A field that takes a password (by its selector or what the recorder saw)."""
    blob = (sel + " " + json.dumps(st.get("selectors") or [])).lower()
    return "password" in blob or "passwd" in blob or "[type=\\\"password\\\"]" in blob


# ── Playwright codegen (Python / JS) ────────────────────────────────────────

_STR = r"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')"""


def _s(m, i):
    """The i-th quoted string of a match (either quote style)."""
    return m.group(i) if m.group(i) is not None else (m.group(i + 1) or "")


def _locator(expr: str) -> str:
    """page.get_by_role("button", name="Save") / page.getByRole('button', { name: 'Save' }) / … → a selector."""
    e = expr.strip()
    m = re.match(r"(?:get_by_role|getByRole)\(\s*" + _STR + r"(?:\s*,\s*(?:\{\s*)?name\s*[:=]\s*" + _STR + r")?", e)
    if m:
        role, name = _s(m, 1), (m.group(3) if m.group(3) is not None else m.group(4))
        return f'role={role}[name="{_q(name)}"]' if name else f"role={role}"
    for fn, fmt in (("get_by_label|getByLabel", 'internal:label="{}"'), ("get_by_text|getByText", 'text="{}"'),
                    ("get_by_placeholder|getByPlaceholder", '[placeholder="{}"]'),
                    ("get_by_test_id|getByTestId", '[data-testid="{}"]'),
                    ("get_by_alt_text|getByAltText", '[alt="{}"]'), ("get_by_title|getByTitle", '[title="{}"]')):
        m = re.match(r"(?:" + fn + r")\(\s*" + _STR, e)
        if m:
            return fmt.format(_q(_s(m, 1)))
    m = re.match(r"locator\(\s*" + _STR, e)
    if m:
        return _s(m, 1)
    return ""


def _from_playwright(text: str, fmt: str):
    steps, skipped, env, origin = [], [], set(), ""
    for raw in text.splitlines():
        line = raw.strip().rstrip(";")
        if not line or line.startswith(("#", "//", "import", "from ", "def ", "with ", "const ", "test(", "})", "}",
                                         "browser", "context", "page = ", "return", "run(", "async ")):
            if "page.goto" not in line:
                continue
        line = re.sub(r"^await\s+", "", line)
        m = re.match(r"page\.goto\(\s*" + _STR, line)
        if m:
            path, origin = _path(_s(m, 1), origin)
            steps.append({"playwright_action": "navigate", "value": path})
            continue
        m = re.match(r"(?:await\s+)?expect\(\s*page\s*\)\.(?:to_have_url|toHaveURL)\(\s*(?:re\.compile\(|/)?" + _STR, line)
        if m:
            path, origin = _path(_contained(_s(m, 1)), origin)
            steps.append({"playwright_action": "assert", "assertion_type": "url", "assertion_value": path})
            continue
        m = re.match(r"(?:await\s+)?expect\(\s*page\.(.+?)\)\.(not_to_be_visible|to_be_visible|not\.toBeVisible|toBeVisible|"
                     r"to_have_text|toHaveText|to_contain_text|toContainText|to_have_value|toHaveValue)\((.*)\)$", line)
        if m:
            sel, check, arg = _locator(m.group(1)), m.group(2), m.group(3)
            if not sel:
                skipped.append(raw.strip()[:100])
                continue
            val = re.match(r"\s*" + _STR, arg or "")
            kind = {"not_to_be_visible": "not_visible", "not.toBeVisible": "not_visible", "to_be_visible": "visible",
                    "toBeVisible": "visible", "to_have_value": "value", "toHaveValue": "value"}.get(check, "text")
            st = {"playwright_action": "assert", "selector_key": sel, "assertion_type": kind}
            if kind in ("text", "value"):
                st["assertion_value"] = _s(val, 1) if val else ""
            steps.append(st)
            continue
        m = re.match(r"page\.(.+?)\.(click|dblclick|check|uncheck)\(\s*\)$", line)
        if m:
            sel = _locator(m.group(1))
            if sel:
                steps.append({"playwright_action": "click", "selector_key": sel})
                continue
        m = re.match(r"page\.(.+?)\.(fill|type|press_sequentially|pressSequentially)\(\s*" + _STR + r"\s*\)$", line)
        if m:
            sel, value = _locator(m.group(1)), _s(m, 3)
            if sel:
                if "password" in (sel + m.group(1)).lower():
                    value = PASSWORD_VAR
                    env.add("QAX_VAR_PASSWORD")
                steps.append({"playwright_action": "fill", "selector_key": sel, "value": value})
                continue
        m = re.match(r"page\.(.+?)\.press\(\s*" + _STR + r"\s*\)$", line)
        if m and _locator(m.group(1)):
            steps.append({"playwright_action": "key", "selector_key": _locator(m.group(1)), "value": _s(m, 2)})
            continue
        m = re.match(r"page\.(.+?)\.select_option\(\s*" + _STR + r"|page\.(.+?)\.selectOption\(\s*" + _STR, line)
        if m:
            target = m.group(1) or m.group(4)
            value = _s(m, 2) if m.group(1) else _s(m, 5)
            if _locator(target):
                steps.append({"playwright_action": "select", "selector_key": _locator(target), "value": value})
                continue
        if line.startswith(("page.", "expect(")):
            skipped.append(raw.strip()[:100])
    return steps, {"title": "", "origin": origin, "skipped": skipped, "warnings": [], "needs_env": env}


def _path(url: str, origin: str) -> "tuple[str, str]":
    """'http://staging:8000/a?b' → ('/a?b', 'http://staging:8000'); a path stays as it is."""
    u = urlparse(url)
    if not u.scheme:
        return url or "/", origin
    path = (u.path or "/") + (f"?{u.query}" if u.query else "") + (f"#{u.fragment}" if u.fragment else "")
    return path, origin or f"{u.scheme}://{u.netloc}"


def _contained(pattern: str) -> str:
    """A URL pattern as the text the URL must contain: '.*dashboard' / '^/a/b$' → 'dashboard' / '/a/b'
    (QAX's URL check is "contains"); escaped characters lose their backslash."""
    p = re.sub(r"^\^|\$$", "", str(pattern))
    p = re.sub(r"^(\.\*)+|(\.\*)+$", "", p)
    return re.sub(r"\\(.)", r"\1", p)


def _q(s: str) -> str:
    """Escape a value for a quoted Playwright selector."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')
