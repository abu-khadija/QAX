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


def build_overview(project: str, config_dir: Path, runs_dir: str, url: str = "") -> dict:
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
    host = urlparse(base_url or (hist[0].get("url", "") if hist else "") or url).netloc
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
    # 8 · Coverage gaps — criteria no case covers
    if req and req.get("criteria"):
        unc = (req.get("coverage") or {}).get("uncovered") or []
        txt = {c["id"]: c.get("text", "") for c in req["criteria"]}
        cards.append({"id": "coverage_gaps", "title": "Coverage gaps", "state": "warn" if unc else "ok",
                      "value": f"{len(unc)} criterion(a) without a case" if unc else "Every criterion has a case",
                      "rows": [{"id": u, "text": txt.get(u, "")[:80], "verdict": "NO CASE"} for u in unc],
                      "detail": "Write a case for each (step 3), or confirm it cannot be tested.", "action": "analyze"})

    # 9 · Flaky cases — verdict flips with no change in between; quarantine keeps them out of bugs
    from core.run_history import quarantine
    q = quarantine(p)
    flaky = []
    if len(hist) >= 2:
        flaky = compare(hist[1:], [{"test_case_id": k, "status": v} for k, v in hist[0]["verdicts"].items()])["flaky"]
    if flaky or q:
        cards.append({"id": "flaky", "title": "Flaky cases", "state": "warn" if [f for f in flaky if f not in q] else "ok",
                      "value": f"{len(flaky)} flaky · {len(q)} quarantined",
                      "rows": [{"id": c, "text": "quarantined" if c in q else "flips between runs",
                                "verdict": "QUARANTINED" if c in q else "FLAKY", "quarantined": c in q}
                               for c in sorted(set(flaky) | set(q))],
                      "detail": "A quarantined case still runs and is shown, but is not filed as a bug and does not "
                                "block release readiness. Fix it, then take it out.", "action": "run"})

    # 10 · Critical findings — the app's failures (last run) and proven P1/P2 security bugs (last scan)
    crit = []
    if hist:
        last, causes, titles = hist[0]["verdicts"], hist[0].get("causes") or {}, hist[0].get("titles") or {}
        crit += [{"id": k, "text": titles.get(k, ""), "verdict": "FAIL"} for k, v in last.items()
                 if v == "FAIL" and causes.get(k, "app") in ("app", "unclear") and k not in q]
    if scan:
        crit += [{"id": c.get("severity", ""), "text": str(c.get("title", ""))[:80], "verdict": "SECURITY"}
                 for c in (scan.get("claims") or []) if c.get("severity") in ("P1", "P2")]
    if hist or scan:
        cards.append({"id": "critical", "title": "Critical findings", "state": "bad" if crit else "ok",
                      "value": f"{len(crit)} open" if crit else "None open",
                      "rows": crit[:8], "detail": "Functional failures caused by the app (last run) and proven "
                                                  "P1/P2 security bugs (last scan).", "action": "bugs"})

    # 12 · The change → the cases it selected (a change-based run), with the reasons
    sel = (hist[0].get("selection") if hist else None) or None
    if sel:
        cards.append({"id": "change_selection", "title": "Last change → cases run",
                      "state": "warn" if sel.get("run_all") else "ok",
                      "value": ("all cases (" + str(len(sel.get("selected") or [])) + ")" if sel.get("run_all")
                                else f"{len(sel.get('selected') or [])} run · {len(sel.get('skipped') or [])} skipped"),
                      "rows": [{"id": pg, "text": "a changed file can affect this page", "verdict": "AFFECTED"}
                               for pg in (sel.get("pages") or [])[:8]],
                      "detail": f"{sel.get('files', 0)} changed file(s): {sel.get('why', '')}. A file QAX cannot place, "
                                "or a shared one, runs everything; smoke cases always run.", "action": "run"})
    elif hist:
        cards.append({"id": "change_selection", "title": "Last change → cases run", "state": "empty",
                      "value": "Every case ran",
                      "detail": "Give the app's local checkout and a base commit (Run step → only what a change can "
                                "break): QAX runs the cases a change can affect and says why.", "action": "run"})

    # 13 · Evidence — can each non-passing case be checked by someone else?
    ev = (hist[0].get("evidence") if hist else None) or {}
    if ev:
        need = ("steps", "screenshot", "reason")
        full = [k for k, e in ev.items() if all(e.get(x) for x in need)]
        rows = [{"id": k, "text": "missing: " + ", ".join(x for x in need if not e.get(x))
                 + ("" if e.get("network") else " · no network log"), "verdict": "INCOMPLETE"}
                for k, e in ev.items() if k not in full]
        cards.append({"id": "evidence", "title": "Evidence of the last run",
                      "state": "ok" if len(full) == len(ev) else "warn",
                      "value": f"{len(full)}/{len(ev)} non-passing case(s) fully evidenced",
                      "rows": rows[:8],
                      "detail": "Fully evidenced = the steps, a screenshot and the observed result or error. The "
                                "network log (HAR) is attached to browser failures; a trace when tracing is on.",
                      "action": "run"})

    # 14 · Cost — measured, for the last run and the last generation (AI cost is approximate)
    gen = _read(_root() / "functional_runs" / p / "generation_cost.json")
    run_cost = (hist[0].get("cost") if hist else None) or None
    if run_cost or gen:
        def _usd(c):
            if not c or not c.get("calls"):
                return "no AI calls"
            usd = f"≈ ${c.get('estimated_cost_usd', 0):.4f}"
            return f"{c['calls']} AI call(s) · {usd}" + (f" (+{c['unpriced_calls']} unpriced)" if c.get("unpriced_calls") else "")
        parts = []
        if run_cost:
            parts.append(f"Last run: {run_cost.get('cases', '?')} case(s) in {run_cost.get('duration_s', '?')} s · {_usd(run_cost)}")
        if gen:
            parts.append(f"Last generation: {gen.get('cases', '?')} case(s) · {_usd(gen)}")
        total = (run_cost or {}).get("estimated_cost_usd", 0) + (gen or {}).get("estimated_cost_usd", 0)
        cards.append({"id": "cost", "title": "Cost", "state": "ok",
                      "value": f"≈ ${total:.4f} AI" + (f" · {run_cost['duration_s']} s run" if run_cost else ""),
                      "detail": " · ".join(parts) + ". AI cost is an estimate from token counts and a price table; "
                                "running cases uses no AI unless healing is on.", "action": "run"})

    # 11 · Release readiness — a rule over the cards above (not a guarantee)
    if hist:
        blockers = []
        last = hist[0]["verdicts"]
        causes = hist[0].get("causes") or {}
        f = [k for k, v in last.items() if v == "FAIL" and k not in q]
        nr = [k for k, v in last.items() if v not in ("PASS", "FAIL") and k not in q]
        if f:
            blockers.append(f"{len(f)} case(s) failing: {', '.join(f[:5])}")
        if nr:
            blockers.append(f"{len(nr)} case(s) did not run to a verdict ({', '.join(sorted({causes.get(k, '?') for k in nr}))})")
        if req and req.get("criteria"):
            from core.traceability import requirement_verdicts as _rv
            bad = [v["id"] for v in _rv(req["criteria"], req.get("coverage") or {},
                                        [{"test_case_id": k, "status": v} for k, v in last.items()])
                   if v["verdict"] != "PASSED"]
            if bad:
                blockers.append(f"criteria not passed: {', '.join(bad)}")
        if scan and any(c.get("severity") == "P1" for c in scan.get("claims") or []):
            blockers.append("a proven P1 security bug in the last scan")
        cards.append({"id": "readiness", "title": "Release readiness", "state": "bad" if blockers else "ok",
                      "value": "Not ready" if blockers else "Ready by the measured checks",
                      "rows": [{"id": "•", "text": b, "verdict": ""} for b in blockers],
                      "detail": "A rule over the last run, the requirements and the last scan — what QAX did not test "
                                "is not covered by it.", "action": "run"})
    # 15 · Not tested — what nobody can rely on (cases that did not run, scan checks that could not)
    if hist or scan:
        nr_rows = []
        if hist:
            last, causes, titles = hist[0]["verdicts"], hist[0].get("causes") or {}, hist[0].get("titles") or {}
            nr_rows = [{"id": k, "text": (titles.get(k, "") or "")[:50] + (" — " + causes[k] if causes.get(k) else ""),
                        "verdict": v} for k, v in last.items() if v not in ("PASS", "FAIL")]
        gaps = len((scan or {}).get("coverage_gaps") or [])
        cards.append({"id": "not_tested", "title": "Not tested",
                      "state": "warn" if (nr_rows or gaps) else "ok",
                      "value": f"{len(nr_rows)} case(s) did not run" + (f" · {gaps} security check(s) did not run" if scan else ""),
                      "rows": nr_rows[:8],
                      "detail": "A case that did not run proves nothing either way — the release decision does not "
                                "cover it. Fix the test or the environment and run it again.", "action": "run"})
    return _organize(p, cards)


# How the overview is read: decide first, then investigate, then keep the suite healthy.
_GROUPS = [
    ("decide", "Decide", "Can this build ship?", ("readiness", "critical", "requirements", "security")),
    ("investigate", "Investigate", "What happened in the last run, and why?",
     ("last_run", "changes", "not_tested", "evidence", "change_selection")),
    ("maintain", "Keep the suite healthy", "Things that drift if nobody looks.",
     ("flaky", "regression", "app_map", "cost")),
]


def _organize(project: str, cards: list) -> dict:
    """
    Fold duplicates and group the cards: the pass-rate trend becomes part of "Last run", coverage
    gaps are already the requirements' NOT COVERED rows, and a card with no data yet moves to a
    short setup checklist instead of taking a tile. Returns {"project", "groups", "setup", "cards"}.
    """
    by_id = {c["id"]: c for c in cards}
    trend, gaps = by_id.pop("trend", None), by_id.pop("coverage_gaps", None)
    if trend and "last_run" in by_id:
        by_id["last_run"]["points"] = trend.get("points")
        by_id["last_run"]["detail"] += f" · pass rate over the last runs: {trend.get('detail', '')}"
    if gaps and gaps.get("rows") and "requirements" in by_id:
        by_id["requirements"]["detail"] += f" · {gaps['value']} — write one each (step 3)."
    setup = [{"id": c["id"], "title": c["title"], "detail": c.get("detail", ""), "action": c.get("action")}
             for c in by_id.values() if c.get("state") == "empty"]
    groups = []
    for gid, title, hint, ids in _GROUPS:
        g = [dict(by_id[i], group=gid) for i in ids if i in by_id and by_id[i].get("state") != "empty"]
        if g:
            groups.append({"id": gid, "title": title, "hint": hint, "cards": g})
    flat = [c for g in groups for c in g["cards"]] + [dict(c, group="setup") for c in by_id.values()
                                                      if c.get("state") == "empty"]
    return {"project": project, "groups": groups, "setup": setup, "cards": flat}
