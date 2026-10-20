"""
core/page_quality.py — accessibility and performance basics of each page a run visits
=====================================================================================
What a QA engineer notices on the way while testing a flow: images with no text
alternative, fields with no label, buttons and links with no name, missing page
language or title, duplicate ids, skipped heading levels, positive tabindex, text
with too little contrast (WCAG 2.x AA: 4.5:1, 3:1 for large text) — and how fast the
page was (time to first byte, DOM ready, load, Largest Contentful Paint, layout shift).

Deterministic, in the page, no extra dependency and no AI. These are OBSERVATIONS: they
never change a case's verdict, and they are not a full accessibility audit (no screen
reader, no keyboard-trap or focus-order check) — the report says so. Never raises.

QAX_A11Y=axe runs Deque's axe-core instead (core/vendor/axe-core, MPL-2.0, unmodified — about
100 WCAG 2.0/2.1/2.2 A/AA rules). If axe cannot run on a page, the basic checks run and
the page says which engine produced its list.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

AXE_PATH = Path(__file__).resolve().parent / "vendor" / "axe-core" / "axe.min.js"
AXE_VERSION = "4.13.0"
_axe_src: "str | None" = None

# axe tags → only WCAG A/AA rules (no "best-practice" opinions, no experimental rules)
_AXE_RUN_JS = r"""async () => {
  const r = await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']},
                                      resultTypes: ['violations']});
  const sc = (tags) => { const t = (tags || []).find(x => /^wcag\d{3,5}$/.test(x)); if (!t) return ''; const d = t.slice(4); return d[0] + '.' + d[1] + '.' + d.slice(2); };
  return r.violations.slice(0, 60).map(v => ({rule: v.help, wcag: sc(v.tags), impact: v.impact || '',
    target: ((v.nodes[0] || {}).target || []).join(' ') + (v.nodes.length > 1 ? ' (+' + (v.nodes.length - 1) + ' more)' : ''),
    note: v.id}));
}"""


def _a11y_engine() -> str:
    """'axe' when asked for (QAX_A11Y=axe) and the vendored file is there, else 'basic'."""
    return "axe" if os.getenv("QAX_A11Y", "basic").strip().lower() == "axe" and AXE_PATH.is_file() else "basic"


async def _axe(page) -> "list | None":
    """axe-core violations on the page, or None when axe could not run."""
    global _axe_src
    try:
        if _axe_src is None:
            _axe_src = AXE_PATH.read_text(encoding="utf-8")
        if not await page.evaluate("() => typeof window.axe === 'object'"):
            await page.evaluate(_axe_src)
        out = await page.evaluate(_AXE_RUN_JS)
        return out if isinstance(out, list) else None
    except Exception:  # noqa: BLE001
        return None

_A11Y_JS = r"""() => {
  const out = [];
  const add = (rule, wcag, el, note) => { if (out.length < 60) out.push({rule, wcag, target: describe(el), note: note || ''}); };
  const describe = (el) => {
    if (!el || !el.tagName) return 'page';
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id; else if (el.getAttribute('name')) s += '[name="' + el.getAttribute('name') + '"]';
    const t = (el.innerText || el.value || el.getAttribute('src') || '').trim().slice(0, 40);
    return t ? s + ' "' + t + '"' : s;
  };
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden';
  const named = (el) => {
    const lb = el.getAttribute('aria-labelledby');
    if (lb && lb.split(/\s+/).some(id => (document.getElementById(id) || {}).textContent)) return true;
    return !!((el.getAttribute('aria-label') || '').trim() || (el.getAttribute('title') || '').trim());
  };
  if (!(document.documentElement.getAttribute('lang') || '').trim()) add('page has no language', '3.1.1', null);
  if (!(document.title || '').trim()) add('page has no title', '2.4.2', null);
  if (!document.querySelector('meta[name="viewport"]')) add('no viewport meta — the page does not adapt to phone screens', '1.4.10', null);
  document.querySelectorAll('img').forEach(img => { if (visible(img) && !img.hasAttribute('alt') && !named(img)) add('image without text alternative', '1.1.1', img); });
  document.querySelectorAll('input, select, textarea').forEach(el => {
    const t = (el.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(t) || !visible(el)) return;
    const hasLabel = (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]')) || el.closest('label') || named(el);
    if (!hasLabel) add('form field without a label', '1.3.1 / 4.1.2', el, el.getAttribute('placeholder') ? 'placeholder is not a label' : '');
  });
  document.querySelectorAll('button, a[href], [role=button], [role=link]').forEach(el => {
    if (!visible(el)) return;
    const text = (el.innerText || el.value || '').trim();
    const imgAlt = [...el.querySelectorAll('img[alt]')].some(i => i.alt.trim());
    if (!text && !imgAlt && !named(el)) add((el.tagName === 'A' ? 'link' : 'button') + ' without a name', '4.1.2 / 2.4.4', el);
  });
  const ids = {};
  document.querySelectorAll('[id]').forEach(el => { ids[el.id] = (ids[el.id] || 0) + 1; });
  Object.entries(ids).filter(([k, n]) => n > 1 && k).slice(0, 5).forEach(([k, n]) => add('duplicate id "' + k + '" (' + n + '×)', '4.1.1', null));
  let last = 0;
  document.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(h => { if (!visible(h)) return; const l = +h.tagName[1]; if (last && l > last + 1) add('heading level skipped (h' + last + ' → h' + l + ')', '1.3.1', h); last = l; });
  document.querySelectorAll('[tabindex]').forEach(el => { if (+el.getAttribute('tabindex') > 0) add('positive tabindex changes the focus order', '2.4.3', el); });
  // contrast (text on a solid background only; gradients/images are not judged)
  const rgb = (c) => { const m = (c || '').match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(x => parseFloat(x)); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const lum = (c) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = (el) => { for (let e = el; e && e.nodeType === 1; e = e.parentElement) { const s = getComputedStyle(e); if (s.backgroundImage && s.backgroundImage !== 'none') return null; const c = rgb(s.backgroundColor); if (c && c.a >= 0.99) return c; if (c && c.a > 0) return null; } return {r: 255, g: 255, b: 255, a: 1}; };
  let checked = 0, low = 0;
  for (const el of document.querySelectorAll('p, span, a, button, label, li, td, th, h1, h2, h3, h4, h5, h6, div')) {
    if (checked > 300 || low >= 8) break;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (!own || !visible(el)) continue;
    checked++;
    const s = getComputedStyle(el), fg = rgb(s.color), bg = bgOf(el);
    if (!fg || !bg || fg.a < 0.99) continue;
    const L1 = lum(fg), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const size = parseFloat(s.fontSize), bold = parseInt(s.fontWeight) >= 700;
    const need = (size >= 24 || (bold && size >= 18.66)) ? 3 : 4.5;
    if (ratio < need) { low++; add('text contrast ' + ratio.toFixed(2) + ':1 (needs ' + need + ':1)', '1.4.3', el); }
  }
  return out;
}"""

_PERF_JS = r"""async () => {
  const nav = performance.getEntriesByType('navigation')[0];
  const r = {};
  if (nav) { r.ttfb_ms = Math.round(nav.responseStart); r.dom_ready_ms = Math.round(nav.domContentLoadedEventEnd);
             r.load_ms = Math.round(nav.loadEventEnd) || null; r.transfer_kb = Math.round((nav.transferSize || 0) / 1024); }
  const take = (type) => new Promise(res => { try { const po = new PerformanceObserver(l => res(l.getEntries()));
      po.observe({type, buffered: true}); setTimeout(() => res([]), 150); } catch (e) { res([]); } });
  const lcp = await take('largest-contentful-paint');
  if (lcp.length) r.lcp_ms = Math.round(lcp[lcp.length - 1].startTime);
  const shifts = await take('layout-shift');
  r.cls = Math.round(shifts.filter(s => !s.hadRecentInput).reduce((a, s) => a + s.value, 0) * 1000) / 1000;
  return r;
}"""

# Google's "good" thresholds for Core Web Vitals and common budgets
_BUDGET = {"lcp_ms": 2500, "cls": 0.1, "ttfb_ms": 800, "load_ms": 4000}


async def check_page(page) -> dict:
    """
    Accessibility and performance basics of the page as it is now.

    Returns:
        dict: {"url", "accessibility": [{rule, wcag, target, note}], "performance": {...},
               "slow": [metric names over budget]}; {} when the page cannot be read.
    """
    try:
        url = page.url
        if not isinstance(url, str) or not url or url.startswith(("about:", "data:")):
            return {}
        engine, a11y = _a11y_engine(), None
        if engine == "axe":
            a11y = await _axe(page)
            if a11y is None:
                engine = "basic (axe-core could not run on this page)"
            else:
                engine = f"axe-core {AXE_VERSION}"
        if a11y is None:
            a11y = await page.evaluate(_A11Y_JS)
        perf = await page.evaluate(_PERF_JS)
        a11y = a11y if isinstance(a11y, list) else []
        perf = perf if isinstance(perf, dict) else {}
        slow = [k for k, lim in _BUDGET.items() if isinstance(perf.get(k), (int, float)) and perf[k] > lim]
        return {"url": urlparse(url).path or "/", "accessibility": a11y or [], "a11y_engine": engine,
                "performance": perf or {}, "slow": slow}
    except Exception:  # noqa: BLE001
        return {}


class QualityObserver:
    """Checks each distinct page (path) of a case once, as the case moves through the app."""

    def __init__(self) -> None:
        """Start with no page seen."""
        self.pages: list = []
        self._seen: set = set()

    async def observe(self, page) -> None:
        """Check the page if its path has not been checked in this case. Never raises."""
        try:
            path = urlparse(page.url).path or "/"
        except Exception:  # noqa: BLE001
            return
        if path in self._seen:
            return
        self._seen.add(path)
        res = await check_page(page)
        if res:
            self.pages.append(res)

    def summary(self) -> dict:
        """{"pages": [...], "issues": n accessibility observations, "slow_pages": n} or {}."""
        if not self.pages:
            return {}
        return {"pages": self.pages,
                "issues": sum(len(p["accessibility"]) for p in self.pages),
                "slow_pages": sum(1 for p in self.pages if p["slow"])}
