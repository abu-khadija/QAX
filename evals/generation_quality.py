"""
evals/generation_quality.py — how good are the test cases the AI writes?
=======================================================================
The one leg of functional QA no run has measured yet (no AI credit when it was built):
QAX writes cases from a user story for a real app, then this scores them the way a QA
lead would review them, and runs them in a real browser against an app whose bugs are
known (evals/targets/ui_demo_app.py: the empty-note check is broken on purpose).

    ./.venv-exec/bin/python -m evals.generation_quality        # or: make eval-generation

Measured per story (evals/data/generation_stories.json):
  - valid cases (schema) and requirement coverage (every acceptance criterion has a case)
  - traceability (cases that say which criterion they cover) and the technique mix
  - security cases written from the same story; negative cases (≥ 20% is the bar)
  - grounding: elements the cases use that are really on the app (from `map`)
  - in the browser: the planted bug FOUND (a case on that criterion FAILS), no false
    failure elsewhere, and how many cases could not run (ERROR) — a test problem

Exit: 0 bar met · 1 bar missed · 2 not measured (no API key / no credit / no browser).
Needs ANTHROPIC_API_KEY with credit; costs one generation per story. Never raises.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BAR = {"coverage_pct": 100, "traced_pct": 90, "grounded_pct": 90, "negative_pct": 20,
       "error_pct": 10, "security_cases": 1}


def score_cases(cases: list, criteria: list, app_keys: "set | None" = None) -> dict:
    """
    Review a set of generated cases without running them.

    Args:
        cases: generated test cases.
        criteria: from core.traceability.extract_criteria().
        app_keys: element names on the app map (None = grounding not measured).

    Returns:
        dict of measures (see module docstring).
    """
    from core.traceability import coverage
    cov = coverage(cases, criteria)
    n = len(cases) or 1
    used, grounded = 0, 0
    for c in cases:
        for st in c.get("automation_steps") or []:
            key = str(st.get("selector_key") or "").strip()
            if not key:
                continue
            used += 1
            raw_selector = key[:1] in "#.[" or "=" in key or ">>>" in key
            if app_keys is not None and (key in app_keys or raw_selector):
                grounded += 1
    neg_words = ("invalid", "reject", "empty", "wrong", "error", "not ", "cannot", "denied", "refus",
                 "unauthor", "forbidden", "fail", "missing", "too long", "negative")
    negative = [c for c in cases if str(c.get("test_type", "")).lower() in ("negative", "security")
                or any(w in (str(c.get("title", "")) + " " + str(c.get("description", ""))).lower() for w in neg_words)]
    security = [c for c in cases if str(c.get("test_type", "")).lower() == "security"
                or "access control" in str(c.get("description", "")).lower()
                or "injection" in str(c.get("description", "")).lower()]
    return {
        "cases": len(cases),
        "coverage_pct": cov["percent"],
        "uncovered": cov["uncovered"],
        "traced_pct": int(round(100 * (len(cases) - len(cov["untraced_cases"])) / n)),
        "techniques": cov["techniques"],
        "negative_pct": int(round(100 * len(negative) / n)),
        "security_cases": len(security),
        "grounded_pct": (int(round(100 * grounded / used)) if used else 0) if app_keys is not None else None,
        "automated_pct": int(round(100 * sum(1 for c in cases if c.get("automation_steps")) / n)),
        "by_criterion": cov["by_criterion"],
    }


def score_run(results: list, by_criterion: dict, planted: list) -> dict:
    """
    Judge a browser run against the known bugs: the planted bug must be found (a case on
    that criterion FAILS); a FAIL on any other criterion is a false failure to review.
    """
    status = {str(r.get("test_case_id")): r.get("status") for r in results or []}
    n = len(results) or 1
    found = [ac for ac in planted if any(status.get(i) == "FAIL" for i in by_criterion.get(ac, []))]
    planted_ids = {i for ac in planted for i in by_criterion.get(ac, [])}
    false_fail = sorted(i for i, s in status.items() if s == "FAIL" and i not in planted_ids)
    return {"planted_found": found, "planted_missed": [a for a in planted if a not in found],
            "false_failures": false_fail,
            "error_pct": int(round(100 * sum(1 for s in status.values() if s in ("ERROR", "SKIP")) / n)),
            "passed": sum(1 for s in status.values() if s == "PASS"),
            "failed": sum(1 for s in status.values() if s == "FAIL")}


def verdict(review: dict, run: "dict | None") -> list:
    """The bar items this story missed (empty = met)."""
    miss = []
    for k in ("coverage_pct", "traced_pct", "negative_pct", "security_cases"):
        if (review.get(k) or 0) < BAR[k]:
            miss.append(f"{k} {review.get(k)} < {BAR[k]}")
    if review.get("grounded_pct") is not None and review["grounded_pct"] < BAR["grounded_pct"]:
        miss.append(f"grounded_pct {review['grounded_pct']} < {BAR['grounded_pct']}")
    if run is not None:
        if run["planted_missed"]:
            miss.append(f"planted bug not found: {', '.join(run['planted_missed'])}")
        if run["false_failures"]:
            miss.append(f"false failures: {', '.join(run['false_failures'])}")
        if run["error_pct"] > BAR["error_pct"]:
            miss.append(f"error_pct {run['error_pct']} > {BAR['error_pct']}")
    return miss


def _serve_demo() -> "tuple[str, object]":
    """Start the demo app on a free port; returns (url, server)."""
    import importlib.util

    from werkzeug.serving import make_server
    spec = importlib.util.spec_from_file_location("ui_demo_app", ROOT / "evals/targets/ui_demo_app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = make_server("127.0.0.1", port, mod.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", server


def run(stories_file: str, out: str, generate=None, execute: bool = True) -> int:
    """
    Measure every story; print a table; write the JSON record. Returns the exit code.
    `generate(story_text, app_map, context) -> (cases, error)` is injectable (tests).
    """
    from core.traceability import criteria_block, extract_criteria, security_block, security_risks
    stories = json.loads(Path(stories_file).read_text(encoding="utf-8"))["stories"]
    if generate is None:
        if not os.getenv("ANTHROPIC_API_KEY", "").strip():
            print("QAX generation quality: NOT MEASURED — ANTHROPIC_API_KEY is not set.")
            return 2
        generate = _live_generate
    url, server = _serve_demo()
    project = "qax-generation-eval"
    record = {"url": url, "stories": []}
    worst = 0
    try:
        from core.app_mapper import map_app, save_map
        app_map = asyncio.run(map_app(url, username="demo", password="demo123"))
        if app_map.error:
            print(f"QAX generation quality: NOT MEASURED — could not read the demo app: {app_map.error}")
            return 2
        save_map(app_map, project)
        keys = set(app_map.keys)
        for st in stories:
            criteria = extract_criteria(st["story"])
            ctx = "\n\n".join(x for x in (criteria_block(criteria), security_block(security_risks(criteria))) if x)
            cases, err = generate(st["story"], app_map, ctx)
            if err:
                msg = str(err)
                print(f"QAX generation quality: NOT MEASURED — generation failed: {msg[:200]}")
                record["stories"].append({"id": st["id"], "error": msg[:300]})
                worst = max(worst, 2)
                continue
            review = score_cases(cases, criteria, keys)
            run_res = None
            if execute and cases:
                run_res = score_run(_execute(cases, url, project), review["by_criterion"],
                                    st.get("planted_bug_criteria", []))
            missed = verdict(review, run_res)
            record["stories"].append({"id": st["id"], "review": review, "run": run_res, "missed": missed})
            _print(st["id"], review, run_res, missed)
            worst = max(worst, 1 if missed else 0)
    finally:
        server.shutdown()
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    print(f"\n  Record: {out}")
    print("  BAR: " + ("MET ✅" if worst == 0 else "MISSED ❌" if worst == 1 else "NOT MEASURED"))
    return worst


def _live_generate(story: str, app_map, ctx: str):
    """One real generation call (the same path as the web UI)."""
    try:
        from services.context import QAXContext
        from services.generation_service import GenerationOptions, GenerationService
        from services.progress import NullProgressReporter
        opts = GenerationOptions(user_story=story, content=story, app_map=app_map, repo_context=ctx,
                                 use_cache=False)
        res = GenerationService(QAXContext.from_env()).generate("story://generation-eval", options=opts,
                                                                progress=NullProgressReporter())
        return (res.test_cases or []), res.error
    except Exception as exc:  # noqa: BLE001
        return [], exc


def _execute(cases: list, url: str, project: str) -> list:
    """Run the cases in a headless browser against the demo app (AI healing off)."""
    from services.execution_service import ExecutionService
    keep = {k: os.environ.get(k) for k in ("APP_BASE_URL", "DEFAULT_PROJECT", "QAX_AI_HEAL", "AUTO_BUG_REPORT")}
    try:
        os.environ.update(APP_BASE_URL=url, DEFAULT_PROJECT=project, QAX_AI_HEAL="false", AUTO_BUG_REPORT="false")
        res = ExecutionService().execute(cases, mode="ui", headless=True)
        return (res.raw or {}).get("results", [])
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _print(sid: str, review: dict, run_res, missed: list) -> None:
    """One story's measures as a readable block."""
    print(f"\n  Story: {sid}")
    print(f"    cases {review['cases']} · coverage {review['coverage_pct']}% (uncovered: {review['uncovered'] or '–'})"
          f" · traced {review['traced_pct']}% · negative {review['negative_pct']}% · security {review['security_cases']}"
          f" · grounded {review['grounded_pct']}% · automated {review['automated_pct']}%")
    print(f"    techniques: {review['techniques'] or '–'}")
    if run_res:
        print(f"    run: {run_res['passed']} PASS · {run_res['failed']} FAIL · errors {run_res['error_pct']}%"
              f" · planted bug found: {run_res['planted_found'] or 'NO'} · false failures: {run_res['false_failures'] or '–'}")
    print("    " + ("bar met" if not missed else "missed: " + "; ".join(missed)))


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description="Measure the quality of AI-written test cases.")
    ap.add_argument("--stories", default=str(ROOT / "evals/data/generation_stories.json"))
    ap.add_argument("--out", default="output/generation_quality.json")
    ap.add_argument("--no-run", action="store_true", help="review the cases only, do not run them")
    a = ap.parse_args()
    sys.exit(run(a.stories, a.out, execute=not a.no_run))


if __name__ == "__main__":
    main()
