"""
evals/functional_bench.py — does functional QA tell the truth, every time?
==========================================================================
Runs each app's cases in a real browser and scores QAX's verdicts against TRUTH WRITTEN
BEFORE THE RUN (each case's expected_outcome) — never against QAX's own judgement:

  detection rate      known defects / negative controls that FAILED          (higher is better)
  false-failure rate  healthy cases that FAILED — a false bug report         (must be 0)
  missed-defect rate  known defects that PASSED — a false all-clear          (must be 0)
  false-pass rate     cases that must not pass (SKIP truth) that PASSED      (must be 0)
  first-run success   cases that RAN (no ERROR/SKIP) when they should run    (test reliability)
  repeatability       verdict flips across --repeat runs of the same case    (must be 0)
  time per case

    ./.venv-exec/bin/python -m evals.functional_bench                 # the built-in demo app
    ./.venv-exec/bin/python -m evals.functional_bench --app all --repeat 2
    ./.venv-exec/bin/python -m evals.functional_bench --app eshoponweb --base-url http://127.0.0.1:8960

Apps come from evals/data/functional_bench.json (how each was started, so a run can be
repeated); an app that is not reachable is NOT RUN and never counted. Raw results go to
output/bench/functional_<time>.json. Exit 0 when every measured bar holds, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def truth_of(case: dict) -> str:
    """PASS / FAIL / NOTPASS from a case's expected_outcome ("FAIL (planted bug)" → FAIL)."""
    t = str(case.get("expected_outcome", "PASS")).strip().upper()
    if t.startswith("FAIL"):
        return "FAIL"
    if t.startswith(("SKIP", "ERROR", "NOT")):
        return "NOTPASS"
    return "PASS"


def score(cases: list, runs: list) -> dict:
    """
    Score one app. `runs` = a list of {case_id: status} dicts (one per repeat).

    Returns:
        dict with counts, rates (None when the denominator is 0) and per-case rows.
    """
    rows, flips = [], 0
    det = det_n = ff = healthy = missed = fp = notpass_n = ran = should_run = 0
    for c in cases:
        cid = str(c.get("test_case_id"))
        truth = truth_of(c)
        seen = [r.get(cid, "NOT RUN") for r in runs]
        flips += int(len(set(seen)) > 1)
        first = seen[0] if seen else "NOT RUN"
        if truth != "NOTPASS":
            should_run += 1
            ran += int(first in ("PASS", "FAIL"))
        if truth == "FAIL":
            det_n += 1
            det += int(first == "FAIL")
            missed += int(first == "PASS")
        elif truth == "PASS":
            healthy += 1
            ff += int(first == "FAIL")
        else:
            notpass_n += 1
            fp += int(first == "PASS")
        rows.append({"case": cid, "truth": truth, "verdicts": seen,
                     "correct": (first == truth) or (truth == "NOTPASS" and first != "PASS")})
    rate = lambda a, b: round(a / b, 3) if b else None  # noqa: E731
    return {"cases": len(cases), "correct": sum(r["correct"] for r in rows),
            "detection_rate": rate(det, det_n), "false_failure_rate": rate(ff, healthy),
            "missed_defect_rate": rate(missed, det_n), "false_pass_rate": rate(fp, notpass_n),
            "first_run_success": rate(ran, should_run), "flipping_cases": flips, "rows": rows}


def bars_hold(s: dict) -> list:
    """The bars a scored app missed (empty = all hold)."""
    miss = []
    for k in ("false_failure_rate", "missed_defect_rate", "false_pass_rate"):
        if s.get(k):
            miss.append(f"{k} {s[k]}")
    if s.get("flipping_cases"):
        miss.append(f"{s['flipping_cases']} case(s) changed verdict between runs")
    if s.get("first_run_success") is not None and s["first_run_success"] < 1.0:
        miss.append(f"first_run_success {s['first_run_success']}")
    return miss


def _reachable(url: str) -> bool:
    """True when the app answers at all (any HTTP status)."""
    try:
        urllib.request.urlopen(url, timeout=5)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:  # noqa: BLE001
        return False


def _run_cases(cases: list, base_url: str, project: str) -> "tuple[dict, float]":
    """Run cases in a headless browser; returns ({case_id: status}, seconds)."""
    from services.execution_service import ExecutionService
    keep = {k: os.environ.get(k) for k in ("APP_BASE_URL", "DEFAULT_PROJECT", "QAX_AI_HEAL", "AUTO_BUG_REPORT")}
    t0 = time.monotonic()
    try:
        os.environ.update(APP_BASE_URL=base_url, DEFAULT_PROJECT=project, QAX_AI_HEAL="false", AUTO_BUG_REPORT="false")
        res = ExecutionService().execute(cases, mode="ui", headless=True)
        return {str(r.get("test_case_id")): r.get("status") for r in (res.raw or {}).get("results", [])}, \
            time.monotonic() - t0
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def bench(names: list, repeat: int = 1, base_url_override: str = "", out: str = "") -> int:
    """Run the benchmark; print the table; write the raw record. Returns the exit code."""
    manifest = json.loads((ROOT / "evals/data/functional_bench.json").read_text(encoding="utf-8"))["apps"]
    apps = [a for a in manifest if "all" in names or a["name"] in names]
    record = {"when": datetime.now(timezone.utc).isoformat(), "repeat": repeat, "apps": []}
    worst = 0
    server = None
    for app in apps:
        cases_doc = json.loads((ROOT / app["cases"]).read_text(encoding="utf-8"))
        cases = cases_doc["test_cases"] if isinstance(cases_doc, dict) else cases_doc
        url = base_url_override if (base_url_override and len(apps) == 1) else app.get("base_url", "")
        if app.get("builtin"):
            from evals.generation_quality import _serve_demo
            url, server = _serve_demo()
        missing_env = [e for e in app.get("needs_env", []) if not os.getenv(e)]
        if not url or missing_env or not _reachable(url):
            why = ("no base_url recorded" if not url else f"missing {', '.join(missing_env)}" if missing_env
                   else f"{url} not reachable")
            print(f"  {app['name']:<20} NOT RUN — {why}")
            record["apps"].append({"name": app["name"], "not_run": why})
            continue
        runs, secs = [], 0.0
        for _ in range(max(1, repeat)):
            verdicts, t = _run_cases(cases, url, app["project"])
            runs.append(verdicts)
            secs += t
        s = score(cases, runs)
        s.update(name=app["name"], base_url=url, seconds_per_case=round(secs / max(1, len(cases) * len(runs)), 2))
        miss = bars_hold(s)
        s["missed_bars"] = miss
        record["apps"].append(s)
        worst = max(worst, 1 if miss else 0)
        print(f"  {app['name']:<20} {s['correct']}/{s['cases']} correct · detection {s['detection_rate']} · "
              f"false failures {s['false_failure_rate']} · missed {s['missed_defect_rate']} · "
              f"first-run {s['first_run_success']} · flips {s['flipping_cases']} · {s['seconds_per_case']}s/case"
              + (f"  ❌ {'; '.join(miss)}" if miss else "  ✅"))
        if server is not None:
            server.shutdown()
            server = None
    path = Path(out or (Path.cwd() / "output" / "bench" / f"functional_{int(time.time())}.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\n  Raw results: {path}")
    return worst


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description="Score functional QA verdicts against truth written before the run.")
    ap.add_argument("--app", action="append", default=[], help="app name from the manifest, or 'all' (default: ui-demo)")
    ap.add_argument("--repeat", type=int, default=1, help="run each app this many times (repeatability)")
    ap.add_argument("--base-url", default="", help="override the URL (one app only)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    sys.exit(bench(a.app or ["ui-demo"], a.repeat, a.base_url, a.out))


if __name__ == "__main__":
    main()
