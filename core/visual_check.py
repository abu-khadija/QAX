"""
core/visual_check.py — visual regression for functional runs (no extra dependency)
=================================================================================
With `--visual` (QAX_VISUAL=true), the screen each case ends on is compared with the
baseline saved the first time that case ran (per project, device and browser):
output/visual/<project>/<device>-<browser>/<case>.png. A difference above 0.5 % of the
pixels is CHANGED — a lead to look at (with a diff image), never a verdict: dynamic data
(dates, counters, {{unique}} values) changes pixels too. QAX_VISUAL=update replaces the
baselines. The pixel comparison runs in the browser's own canvas, so no image library is
needed. Never raises.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

CHANGED_PCT = 0.5
PIXEL_THRESHOLD = 20

_COMPARE_JS = r"""async ([a, b, thr]) => {
  const load = (src) => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = src; });
  const [ia, ib] = await Promise.all([load(a), load(b)]);
  if (ia.width !== ib.width || ia.height !== ib.height)
    return {status: 'dimension_mismatch', pct: 100, size: [ia.width, ia.height, ib.width, ib.height]};
  const c = document.createElement('canvas'); c.width = ia.width; c.height = ia.height;
  const g = c.getContext('2d');
  g.drawImage(ia, 0, 0); const da = g.getImageData(0, 0, c.width, c.height).data;
  g.clearRect(0, 0, c.width, c.height); g.drawImage(ib, 0, 0); const img = g.getImageData(0, 0, c.width, c.height); const db = img.data;
  let changed = 0;
  for (let i = 0; i < da.length; i += 4) {
    const d = Math.max(Math.abs(da[i] - db[i]), Math.abs(da[i + 1] - db[i + 1]), Math.abs(da[i + 2] - db[i + 2]));
    if (d > thr) { changed++; db[i] = 255; db[i + 1] = 0; db[i + 2] = 64; db[i + 3] = 255; }
    else { db[i + 3] = 40; }
  }
  g.putImageData(img, 0, 0);
  return {status: 'ok', pct: Math.round(10000 * changed / (c.width * c.height || 1)) / 100,
          diff: changed ? c.toDataURL('image/png') : ''};
}"""


def _safe(x: str) -> str:
    """A string usable as a file name."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", x or "x").strip("_") or "x"


async def check_case_screen(page, case_id: str) -> dict:
    """
    Compare the current screen with the case's baseline (or save the first baseline).

    Returns:
        dict: {"status": "baseline_saved" | "identical" | "changed" | "dimension_mismatch" | "error",
               "pct", "baseline", "current", "diff"} — {} when visual checks are off.
    """
    mode = os.getenv("QAX_VISUAL", "false").strip().lower()
    if mode not in ("true", "1", "update"):
        return {}
    try:
        project = _safe(os.getenv("DEFAULT_PROJECT", "") or "default")
        variant = _safe(f"{os.getenv('QAX_DEVICE', 'desktop') or 'desktop'}-{os.getenv('QAX_BROWSER', 'chromium') or 'chromium'}")
        folder = Path.cwd() / "output" / "visual" / project / variant
        folder.mkdir(parents=True, exist_ok=True)
        base = folder / f"{_safe(case_id)}.png"
        cur = folder / f"{_safe(case_id)}.current.png"
        shot = await page.screenshot(animations="disabled", caret="hide")
        if mode == "update" or not base.exists():
            base.write_bytes(shot)
            return {"status": "baseline_saved", "pct": 0.0, "baseline": str(base)}
        cur.write_bytes(shot)
        uri = lambda b: "data:image/png;base64," + base64.b64encode(b).decode()  # noqa: E731
        probe = await page.context.new_page()
        try:
            res = await probe.evaluate(_COMPARE_JS, [uri(base.read_bytes()), uri(shot), PIXEL_THRESHOLD])
        finally:
            await probe.close()
        if res.get("status") == "dimension_mismatch":
            return {"status": "dimension_mismatch", "pct": 100.0, "baseline": str(base), "current": str(cur)}
        pct = float(res.get("pct") or 0)
        out = {"status": "changed" if pct > CHANGED_PCT else "identical", "pct": pct,
               "baseline": str(base), "current": str(cur)}
        if out["status"] == "changed" and res.get("diff"):
            diff = folder / f"{_safe(case_id)}.diff.png"
            diff.write_bytes(base64.b64decode(res["diff"].split(",", 1)[1]))
            out["diff"] = str(diff)
        return out
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "pct": 0.0, "error": str(exc)[:200]}
