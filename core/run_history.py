"""
core/run_history.py — per-project memory of functional QA runs
=============================================================
Every functional run is saved per project (verdict per case, when, against what URL),
so the next run can say what changed: a case that passed last time and fails now is a
REGRESSION; one that failed and passes now is FIXED; a case whose verdict flips between
runs with no change in between is FLAKY. Every bug QAX files also goes into the
project's regression suite (output/regression/<project>.json), a cases file the UI and
`execute` load like any other — so a fixed bug is re-checked on every later run.
Files only under output/; never raises.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

def _root() -> Path:
    """output/ under the folder QAX runs from (like screenshots and reports)."""
    return Path.cwd() / "output"


def _safe(project: str) -> str:
    """A project name usable as a folder name."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", project or "default").strip("-") or "default"


def _evidence(r: dict) -> dict:
    """What a non-passing result carries for someone to check it independently."""
    steps = r.get("steps") or []
    return {"steps": bool(steps),
            "screenshot": bool(r.get("screenshot") or any(s.get("screenshot") for s in steps)),
            "network": bool(r.get("network_log")), "trace": bool(r.get("trace")),
            "reason": bool(r.get("error") or r.get("case_error") or any(s.get("error") or s.get("actual_result")
                                                                         for s in steps))}


def _quality(rs: list) -> dict:
    """Per page the run visited (first observation wins): accessibility issues and timings — for the overview."""
    pages = {}
    for r in rs:
        for p in ((r.get("quality") or {}).get("pages") or []):
            url = str(p.get("url") or "")
            if not url or url in pages:
                continue
            a11y = p.get("accessibility") or []
            pages[url] = {"engine": p.get("a11y_engine") or "basic",
                          "a11y": [{"rule": str(i.get("rule", ""))[:120], "wcag": i.get("wcag", ""),
                                    "impact": i.get("impact", ""), "target": str(i.get("target", ""))[:80]}
                                   for i in a11y[:30]],
                          "a11y_count": len(a11y),
                          "perf": {k: (p.get("performance") or {}).get(k) for k in ("lcp_ms", "load_ms", "cls", "ttfb_ms")},
                          "slow": list(p.get("slow") or [])}
    return {"pages": pages} if pages else {}


def save_run(project: str, url: str, results: list, root: Path | None = None, *,
             cost: dict | None = None, selection: dict | None = None) -> str:
    """
    Save one run's verdicts. Returns the file path ("" on failure).

    Also kept, for the overview: what evidence each non-passing case carries, the run's
    measured duration and AI usage (`cost`), and — for a change-based run — which cases the
    change selected and why (`selection`).
    """
    try:
        folder = (root or _root()) / "functional_runs" / _safe(project)
        folder.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        rs = [r for r in results or [] if isinstance(r, dict)]
        rec = {"when": ts, "url": url, "verdicts": {str(r.get("test_case_id")): r.get("status") for r in rs},
               # why a case did not pass and what it is called — for the overview's critical list
               "causes": {str(r.get("test_case_id")): r.get("cause") for r in rs if r.get("cause")},
               "titles": {str(r.get("test_case_id")): str(r.get("title") or "")[:120] for r in rs},
               "evidence": {str(r.get("test_case_id")): _evidence(r) for r in rs if r.get("status") != "PASS"}}
        quality = _quality(rs)
        if quality:
            rec["quality"] = quality
        if cost:
            rec["cost"] = cost
        if selection:
            rec["selection"] = {"run_all": bool(selection.get("run_all")), "why": str(selection.get("why", ""))[:300],
                                "selected": [x.get("id") for x in selection.get("selected") or []],
                                "skipped": [x.get("id") for x in selection.get("skipped") or []],
                                "pages": sorted(k for k in (selection.get("affected_pages") or {}) if k != "*")[:20],
                                "files": int(selection.get("files") or 0)}
        path = folder / f"{ts}.json"
        tmp = folder / f".{ts}.tmp"
        tmp.write_text(json.dumps(rec, indent=2))
        os.replace(tmp, path)
        return str(path)
    except Exception:  # noqa: BLE001
        return ""


def save_generation_cost(project: str, usage: dict, root: Path | None = None) -> None:
    """Keep the last generation's measured AI usage (calls, tokens, approximate USD). Never raises."""
    try:
        folder = (root or _root()) / "functional_runs" / _safe(project)
        folder.mkdir(parents=True, exist_ok=True)
        rec = dict(usage or {}, when=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        tmp = folder / ".generation_cost.tmp"
        tmp.write_text(json.dumps(rec, indent=2))
        os.replace(tmp, folder / "generation_cost.json")
    except Exception:  # noqa: BLE001
        pass


def history(project: str, limit: int = 10, root: Path | None = None) -> list:
    """The project's last runs, newest first: [{"when", "url", "verdicts"}]."""
    try:
        folder = (root or _root()) / "functional_runs" / _safe(project)
        # run records are named by time (20261020T…); other files in the folder are not runs
        files = sorted((f for f in folder.glob("*.json") if f.name[:1].isdigit()), reverse=True)[:limit]
        out = []
        for f in files:                         # one half-written file must not hide the others
            try:
                out.append(json.loads(f.read_text()))
            except Exception:  # noqa: BLE001
                continue
        return out
    except Exception:  # noqa: BLE001
        return []


def compare(previous: list, results: list) -> dict:
    """
    What changed against earlier runs (previous = history() BEFORE saving this run).

    Returns:
        dict: {"regressions": [ids], "fixed": [ids], "flaky": [ids], "new": [ids], "runs_before": n}
    """
    now = {str(r.get("test_case_id")): r.get("status") for r in results or [] if isinstance(r, dict)}
    last = (previous or [{}])[0].get("verdicts", {}) if previous else {}
    out = {"regressions": [], "fixed": [], "flaky": [], "new": [], "runs_before": len(previous or [])}
    for cid, st in now.items():
        before = last.get(cid)
        if before is None:
            out["new"].append(cid)
        elif before == "PASS" and st == "FAIL":
            out["regressions"].append(cid)
        elif before == "FAIL" and st == "PASS":
            out["fixed"].append(cid)
        seen = [p.get("verdicts", {}).get(cid) for p in (previous or [])[:5]]
        seen = [x for x in seen if x in ("PASS", "FAIL")] + ([st] if st in ("PASS", "FAIL") else [])
        flips = sum(1 for a, b in zip(seen, seen[1:]) if a != b)
        if flips >= 2:
            out["flaky"].append(cid)
    return out


def add_regression_cases(project: str, cases: list, bug_keys: dict, root: Path | None = None) -> str:
    """
    Add the cases behind filed bugs to the project's regression suite (merged by case id;
    each carries the bug it guards). Returns the suite path ("" on failure).
    """
    try:
        folder = (root or _root()) / "regression"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{_safe(project)}.json"
        suite = json.loads(path.read_text()) if path.exists() else {"test_cases": []}
        by_id = {str(c.get("test_case_id")): c for c in suite.get("test_cases", [])}
        for c in cases or []:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("test_case_id"))
            item = {k: v for k, v in c.items() if not str(k).startswith("_")}
            item["regression_for"] = bug_keys.get(cid) or item.get("regression_for", "")
            by_id[cid] = item
        suite["test_cases"] = list(by_id.values())
        suite["_comment"] = ("QAX regression suite: every case here once found a bug. Load it in the "
                             "web UI (Load a test-cases file) or run `main.py execute` with it.")
        path.write_text(json.dumps(suite, indent=2))
        return str(path)
    except Exception:  # noqa: BLE001
        return ""


def quarantine(project: str, root: Path | None = None) -> dict:
    """The project's quarantined cases {case_id: {"when", "reason"}} (flaky cases kept out of
    bug lists and release readiness, still run and still shown)."""
    try:
        f = (root or _root()) / "functional_runs" / _safe(project) / "quarantine.json"
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def set_quarantine(project: str, case_id: str, on: bool, reason: str = "", root: Path | None = None) -> dict:
    """Put a case in (on=True) or out of quarantine. Returns the new quarantine map."""
    q = quarantine(project, root)
    if on:
        q[str(case_id)] = {"when": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), "reason": reason or "flaky"}
    else:
        q.pop(str(case_id), None)
    try:
        folder = (root or _root()) / "functional_runs" / _safe(project)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / ".quarantine.tmp"
        tmp.write_text(json.dumps(q, indent=2))
        os.replace(tmp, folder / "quarantine.json")
    except Exception:  # noqa: BLE001
        pass
    return q
