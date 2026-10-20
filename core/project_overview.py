"""
core/project_overview.py — one project's quality, from what QAX actually measured
==================================================================================
The overview a QA lead opens first. Every card is computed from saved records — functional
run history, the requirement criteria of the last analysis, the regression suite, the app
map, the last security scan of the same app — and a card with no data says how to get it.
Nothing is estimated, extrapolated or invented ("time saved", "AI confidence" and similar
numbers are deliberately absent until QAX can measure them). Never raises.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse


def _read(path: Path):
    """JSON from a file, or None."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _when(stamp: str) -> str:
    """20261020T101533123456Z → 2026-10-20 10:15 UTC."""
    s = str(stamp or "")
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[9:11]}:{s[11:13]} UTC" if len(s) >= 13 and s[8:9] == "T" else s


def save_requirements(project: str, criteria: list, coverage: dict, story_key: str = "") -> None:
    """Keep the last analysis' criteria and their case links, so verdicts can be traced later."""
    from core.run_history import _root, _safe
    try:
        folder = _root() / "functional_runs" / _safe(project)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "requirements.json").write_text(json.dumps(
            {"criteria": criteria, "coverage": coverage, "story_key": story_key,
             "when": time.strftime("%Y-%m-%d %H:%M")}, indent=2))
    except Exception:  # noqa: BLE001
        pass


def build_overview(project: str, config_dir: Path, runs_dir: str) -> dict:
    """
    The project's cards: {"project", "cards": [{id, title, state: ok|warn|bad|empty, value,
    detail, rows?, action}]}. `action` names the place in the UI that produces or deepens it.
    """
    from core.run_history import _root, _safe, compare, history
    p = _safe(project)
    cards = []
    hist = history(p, limit=20)

    # 1 · Last run — what happened, honestly counted
    if hist:
        last = hist[0]["verdicts"]
        n = {s: sum(1 for v in last.values() if v == s) for s in ("PASS", "FAIL", "ERROR", "SKIP")}
        other = len(last) - n["PASS"] - n["FAIL"]
        cards.append({"id": "last_run", "title": "Last run", "state": "bad" if n["FAIL"] else ("warn" if other else "ok"),
                      "value": f"{n['PASS']} passed · {n['FAIL']} failed · {other} not run",
                      "detail": f"{_when(hist[0]['when'])} on {hist[0].get('url', '')} — a case that did not fully run is never counted as passed",
                      "action": "run"})
    else:
        cards.append({"id": "last_run", "title": "Last run", "state": "empty", "value": "No run yet",
                      "detail": "Run test cases against the app (step 4) and the results appear here.", "action": "run"})

    # 2 · What changed since the run before — regressions first
    if len(hist) >= 2:
        ch = compare(hist[1:], [{"test_case_id": k, "status": v} for k, v in hist[0]["verdicts"].items()])
        cards.append({"id": "changes", "title": "Since the run before",
                      "state": "bad" if ch["regressions"] else ("warn" if ch["flaky"] else "ok"),
                      "value": f"{len(ch['regressions'])} newly failing · {len(ch['fixed'])} fixed",
                      "detail": ("Newly failing: " + ", ".join(ch["regressions"]) if ch["regressions"] else "Nothing that passed before fails now.")
                      + (" · Flaky (verdict flips with no change): " + ", ".join(ch["flaky"]) if ch["flaky"] else ""),
                      "action": "run"})
    else:
        cards.append({"id": "changes", "title": "Since the run before", "state": "empty", "value": "Needs two runs",
                      "detail": "After a second run QAX shows what newly fails, what was fixed and which cases flip.",
                      "action": "run"})

    # 3 · Pass rate over the recorded runs (a trend only from real runs)
    if len(hist) >= 2:
        pts = []
        for h in reversed(hist[:10]):
            v = list(h["verdicts"].values())
            pts.append(round(100 * sum(1 for x in v if x == "PASS") / len(v)) if v else 0)
        cards.append({"id": "trend", "title": "Pass rate, last runs", "state": "ok", "value": f"{pts[-1]}% now",
                      "detail": " → ".join(f"{x}%" for x in pts) + f" ({len(pts)} runs)", "points": pts, "action": "run"})

    # 4 · Requirements — each acceptance criterion's verdict from the last run
    req = _read(_root() / "functional_runs" / p / "requirements.json")
    if req and req.get("criteria"):
        from core.traceability import requirement_verdicts
        last = hist[0]["verdicts"] if hist else {}
        verdicts = requirement_verdicts(req["criteria"], req.get("coverage") or {},
                                        [{"test_case_id": k, "status": v} for k, v in last.items()])
        bad = [v for v in verdicts if v["verdict"] in ("FAILED", "NOT COVERED")]
        cards.append({"id": "requirements", "title": "Requirements" + (f" · {req['story_key']}" if req.get("story_key") else ""),
                      "state": "bad" if any(v["verdict"] == "FAILED" for v in verdicts) else ("warn" if bad else "ok"),
                      "value": f"{sum(v['verdict'] == 'PASSED' for v in verdicts)}/{len(verdicts)} criteria passed",
                      "rows": [{"id": v["id"], "text": v["text"][:80], "verdict": v["verdict"]} for v in verdicts],
                      "detail": f"From the analysis of {req.get('when', '')} and the last run.", "action": "analyze"})
    else:
        cards.append({"id": "requirements", "title": "Requirements", "state": "empty", "value": "No story analysed",
                      "detail": "Give QAX a user story (step 1): every acceptance criterion gets a verdict here.",
                      "action": "analyze"})

    # 5 · Regression suite — bugs that are re-checked on every run
    suite = _read(_root() / "regression" / f"{p}.json")
    cases = (suite or {}).get("test_cases") or []
    cards.append({"id": "regression", "title": "Regression suite", "state": "ok" if cases else "empty",
                  "value": f"{len(cases)} case(s)" if cases else "Empty",
                  "detail": ("Each found a bug that was filed: " + ", ".join(c.get("regression_for") or c.get("test_case_id", "") for c in cases[:6]))
                  if cases else "Every bug filed to Jira (live) adds its case here, so a fixed bug stays fixed.",
                  "action": "bugs"})

    # 6 · App knowledge — what QAX has read of the app
    amap = _read(config_dir / p / "app_map.json")
    base_url = ""
    if amap:
        base_url = amap.get("base_url", "")
        try:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime((config_dir / p / "app_map.json").stat().st_mtime))
        except Exception:  # noqa: BLE001
            when = ""
        pages = amap.get("pages") or []
        from core.session_state import has_state
        cards.append({"id": "app_map", "title": "What QAX knows of the app", "state": "ok",
                      "value": f"{len(pages)} pages · {sum(len(x.get('elements') or {}) for x in pages)} elements",
                      "detail": f"Read {when} from {base_url}" + (" · logged in" if amap.get("login_ok") else "")
                      + (" · a recorded SSO/2FA session is saved" if has_state(p) else ""), "action": "analyze"})
    else:
        cards.append({"id": "app_map", "title": "What QAX knows of the app", "state": "empty", "value": "Not read yet",
                      "detail": "Enter the app's URL and press Understand: QAX reads its pages and elements.",
                      "action": "analyze"})

    # 7 · Security — the last scan of the same app (host), proven findings only
    host = urlparse(base_url or (hist[0].get("url", "") if hist else "")).netloc
    scan = None
    if host:
        try:
            for name in sorted(os.listdir(runs_dir), reverse=True):
                rec = _read(Path(runs_dir) / name) if name.endswith(".json") else None
                if rec and urlparse(rec.get("url", "")).netloc == host:
                    scan = rec
                    break
        except FileNotFoundError:
            pass
    if scan:
        claims = scan.get("claims") or []
        sev = {s: sum(1 for c in claims if c.get("severity") == s) for s in ("P1", "P2", "P3")}
        cards.append({"id": "security", "title": "Security (last scan)",
                      "state": "bad" if sev["P1"] else ("warn" if claims else "ok"),
                      "value": f"{len(claims)} confirmed · {sev['P1']} P1 · {sev['P2']} P2",
                      "detail": f"{scan.get('scanned_at', '')}{' · read-only' if scan.get('read_only') else ''} · "
                                f"{len(scan.get('coverage_gaps') or [])} check(s) could not run",
                      "action": "scan"})
    else:
        cards.append({"id": "security", "title": "Security", "state": "empty", "value": "Not scanned",
                      "detail": "Scan the same app (the other track): proven security bugs appear here.", "action": "scan"})
    return {"project": p, "cards": cards}
