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


def save_run(project: str, url: str, results: list, root: Path | None = None) -> str:
    """
    Save one run's verdicts. Returns the file path ("" on failure).
    """
    try:
        folder = (root or _root()) / "functional_runs" / _safe(project)
        folder.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        rs = [r for r in results or [] if isinstance(r, dict)]
        rec = {"when": ts, "url": url, "verdicts": {str(r.get("test_case_id")): r.get("status") for r in rs},
               # why a case did not pass and what it is called — for the overview's critical list
               "causes": {str(r.get("test_case_id")): r.get("cause") for r in rs if r.get("cause")},
               "titles": {str(r.get("test_case_id")): str(r.get("title") or "")[:120] for r in rs}}
        path = folder / f"{ts}.json"
        tmp = folder / f".{ts}.tmp"
        tmp.write_text(json.dumps(rec, indent=2))
        os.replace(tmp, path)
        return str(path)
    except Exception:  # noqa: BLE001
        return ""


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
