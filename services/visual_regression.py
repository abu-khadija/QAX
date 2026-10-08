"""
services/visual_regression.py — screenshot visual regression
============================================================
Compares the tour's per-screen screenshots against a saved baseline and flags
screens whose rendering changed. Straight from the QA craft (knowledge base sec 4):
per-pixel sensitivity, a page is CHANGED when more than a small % of pixels differ,
a dimension mismatch is its own status, and a CHANGED verdict is a LEAD (open the
diff) — not auto a bug. First run establishes the baseline.

Pure image math (Pillow), no browser/model. Each finding is a scorecard-shaped dict.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# A page is CHANGED when more than this fraction of pixels differ (KB: 0.5%).
CHANGED_PCT = 0.5
# Per-pixel colour sensitivity: channel-difference above this counts as "changed".
PIXEL_THRESHOLD = 20


def compare_images(baseline_path: str, current_path: str, diff_path: str | None = None) -> dict:
    """
    Compare two PNGs. Returns {status, pct, dims_match, diff_path}:
      status ∈ {"identical", "changed", "dimension_mismatch", "error"}.
    A diff image is written to diff_path on a real pixel difference.
    """
    try:
        from PIL import Image, ImageChops
    except Exception as exc:  # Pillow not installed in this interpreter
        return {"status": "error", "pct": 0.0, "dims_match": None, "error": str(exc)}
    try:
        a = Image.open(baseline_path).convert("RGB")
        b = Image.open(current_path).convert("RGB")
    except Exception as exc:
        return {"status": "error", "pct": 0.0, "dims_match": None, "error": str(exc)}

    if a.size != b.size:
        return {"status": "dimension_mismatch", "pct": 100.0, "dims_match": False,
                "baseline_size": a.size, "current_size": b.size}

    diff = ImageChops.difference(a, b).convert("L")
    mask = diff.point(lambda p: 255 if p > PIXEL_THRESHOLD else 0)
    changed = mask.histogram()[255]
    total = a.size[0] * a.size[1] or 1
    pct = round(100.0 * changed / total, 3)
    status = "changed" if pct > CHANGED_PCT else "identical"
    if status == "changed" and diff_path:
        try:
            Path(diff_path).parent.mkdir(parents=True, exist_ok=True)
            mask.save(diff_path)
        except Exception as exc:
            logger.warning("could not write diff image: %s", exc)
    return {"status": status, "pct": pct, "dims_match": True, "diff_path": diff_path}


def visual_findings(baseline_dir: str, current_dir: str, diff_dir: str | None = None) -> list[dict]:
    """
    Compare every screenshot in current_dir against the same-named file in
    baseline_dir. If baseline_dir has none, SEED it from current (establish the
    baseline) and return []. Otherwise return CHANGED/dimension findings (P3 leads
    — a visual change is a lead to review, not auto a defect).
    """
    cur = Path(current_dir)
    base = Path(baseline_dir)
    current_shots = sorted(cur.glob("*.png")) if cur.is_dir() else []
    if not current_shots:
        return []

    baseline_shots = sorted(base.glob("*.png")) if base.is_dir() else []
    if not baseline_shots:  # first run — establish the baseline
        base.mkdir(parents=True, exist_ok=True)
        for s in current_shots:
            try:
                shutil.copy2(s, base / s.name)
            except Exception as exc:
                logger.warning("baseline seed failed for %s: %s", s.name, exc)
        logger.info("visual baseline established (%d screen(s))", len(current_shots))
        return []

    out: list[dict] = []
    for s in current_shots:
        bpath = base / s.name
        if not bpath.exists():
            continue  # a new screen with no baseline — not a regression
        res = compare_images(str(bpath), str(s),
                             str(Path(diff_dir) / s.name) if diff_dir else None)
        if res["status"] == "changed":
            out.append({
                "title": f"Visual change on screen {s.name}",
                "severity": "P3",
                "evidence": (f"{res['pct']}% of pixels differ from the baseline "
                             f"({s.name}) — a rendering change to review"
                             + (f"; diff: {res.get('diff_path')}" if res.get("diff_path") else "") + "."),
                "source": "visual-regression",
                "target": s.name,
            })
        elif res["status"] == "dimension_mismatch":
            out.append({
                "title": f"Screen size changed on {s.name}",
                "severity": "P3",
                "evidence": (f"Rendered size changed from {res.get('baseline_size')} to "
                             f"{res.get('current_size')} — layout/viewport regression to review."),
                "source": "visual-regression",
                "target": s.name,
            })
    return out
