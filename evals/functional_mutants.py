"""
evals/functional_mutants.py — break the functional engine on purpose; does the benchmark notice?
================================================================================================
The functional benchmark (evals/functional_bench.py) shows the engine gives the right verdicts.
This asks the opposite question about the BENCHMARK: if the engine's checking silently broke,
would any verdict change? Same idea as evals/mutants.py for the scan (40/40).

Each mutant is one realistic regression in the code that decides a verdict (an assertion that
always passes, a skipped step counted as a pass, a shared browser session, no wait after a
click …). For each one, a temporary copy of QAX gets that single change and runs the benchmark
apps; the mutant is

  KILLED    — at least one case's verdict differs from the truth written before the run
              (the benchmark would catch this regression);
  SURVIVED  — every verdict still matches the truth (a regression of this kind would go unseen:
              the benchmark needs a case that exercises it).

Mutants that cannot apply (the source line moved) are reported as STALE, never as killed.
Zero AI. Needs the apps reachable (the built-in demo always is; corpus apps via
~/Downloads/qax-corpus/start-all.sh and `source .bench.env`).

    ./.venv-exec/bin/python -m evals.functional_mutants                    # demo app only
    ./.venv-exec/bin/python -m evals.functional_mutants --app all --json out.json
    ./.venv-exec/bin/python -m evals.functional_mutants --only text_always_passes

Exit 0 always (a report); `--min-killed N` makes it a gate.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

AM, TE = "execution/action_mapper.py", "execution/test_executor.py"

# (name, file, exact source text, replacement, what a developer would have broken)
MUTANTS = [
    ("text_always_passes", AM,
     'if assertion_value.lower() in actual.lower():\n                    return {"status": "PASS", "actual_result": f"Text OK',
     'if True:\n                    return {"status": "PASS", "actual_result": f"Text OK',
     "a text assertion that no longer compares the text"),
    ("url_always_passes", AM,
     "if assertion_value.lower() in page.url.lower():",
     "if True:",
     "a URL assertion that no longer compares the URL"),
    ("visible_always_passes", AM,
     'if await element.is_visible():\n                    return {"status": "PASS", "actual_result": f"Element visible',
     'if True:\n                    return {"status": "PASS", "actual_result": f"Element visible',
     "a visibility assertion that no longer checks visibility"),
    ("missing_element_passes", AM,
     '''                return {
                    "status": "FAIL",
                    "actual_result": f"Element not found for assertion: {selector}",''',
     '''                return {
                    "status": "PASS",
                    "actual_result": f"Element not found for assertion: {selector}",''',
     "an assertion on an element that is not on the page counts as passed"),
    ("value_always_passes", AM,
     "ok = (assertion_value == actual) if assertion_type == \"value\" \\\n                    else (assertion_value.lower() in actual.lower())",
     "ok = True",
     "a form-field value assertion that no longer compares"),
    ("no_assertion_retry", AM,
     'if result.get("status") != "FAIL" or _time.monotonic() >= deadline:',
     "if True:",
     "assertions checked once, immediately (no wait for the page to update)"),
    ("no_settle_after_click", AM,
     '''                note = await _click_through_obstacles(page, selector)
                await _settle_after_action(page)''',
     '''                note = await _click_through_obstacles(page, selector)''',
     "no wait for the page after a click"),
    ("fill_types_nothing", AM,
     "await page.fill(selector, real_value, timeout=15_000)",
     "await page.fill(selector, '', timeout=15_000)",
     "a fill step that clears the field instead of typing"),
    ("failed_step_not_failing_case", TE,
     'if step_result["status"] == "FAIL":\n                overall_status = "FAIL"',
     'if step_result["status"] == "FAIL":\n                overall_status = overall_status',
     "a failed step no longer fails its case"),
    ("skipped_step_counts_as_pass", TE,
     'elif not step_statuses <= {"PASS", "HEALED"}:\n                overall_status = "SKIP"',
     'elif False:\n                overall_status = "SKIP"',
     "a step that did not run counts toward a pass"),
    ("error_step_counts_as_pass", TE,
     'step_statuses = {sr.get("status") for sr in step_results}',
     'step_statuses = {sr.get("status") for sr in step_results} - {"ERROR"}',
     "a step that crashed counts toward a pass"),
    ("shared_browser_session", TE,
     'if n and hasattr(runner, "new_session"):',
     "if False:",
     "cases share one browser session (cookies/login leak between cases)"),
    ("unique_not_unique", TE,
     '"unique": _u,',
     '"unique": "fixed000",',
     "{{unique}} is the same in every case and run (test data collides)"),
]


def _copy_tree(dst: Path) -> None:
    """A copy of QAX without venvs, outputs or git — what a mutant runs from."""
    skip = shutil.ignore_patterns(".git", ".venv*", "output", "__pycache__", "node_modules", "*.pyc")
    shutil.copytree(ROOT, dst, ignore=skip, symlinks=True)


def _run_app(root: Path, app: dict) -> "tuple[dict, str]":
    """Run one benchmark app's cases with the engine at `root`; ({case: status}, error)."""
    from evals.functional_bench import _reachable
    url = app.get("_url")
    if not _reachable(url):
        return {}, "not reachable"
    code = ("import json,os,sys;os.chdir(sys.argv[1]);sys.path.insert(0,sys.argv[1]);"
            "os.environ.update(APP_BASE_URL=sys.argv[3],DEFAULT_PROJECT=sys.argv[4],QAX_AI_HEAL='false',"
            "AUTO_BUG_REPORT='false',DRY_RUN='true');"
            "from services.execution_service import ExecutionService as E;"
            "c=json.load(open(sys.argv[2]))['test_cases'];r=E().execute(c,mode='ui',headless=True);"
            "print('@@'+json.dumps({str(x.get('test_case_id')):x.get('status') for x in (r.raw or {}).get('results',[])}))")
    p = subprocess.run([sys.executable, "-c", code, str(root), str(ROOT / app["cases"]), url, app["project"]],
                       capture_output=True, text=True, timeout=1800, env=os.environ.copy())
    line = next((x for x in p.stdout.splitlines() if x.startswith("@@")), None)
    return (json.loads(line[2:]), "") if line else ({}, "crashed: " + (p.stderr or "")[-200:])


def _wrong(app: dict, verdicts: dict) -> list:
    """Cases whose verdict differs from the truth written before the run."""
    from evals.functional_bench import truth_of
    out = []
    for c in json.loads((ROOT / app["cases"]).read_text())["test_cases"]:
        cid, truth = str(c.get("test_case_id")), truth_of(c)
        got = verdicts.get(cid, "NOT RUN")
        ok = got == truth or (truth == "NOTPASS" and got != "PASS")
        if not ok:
            out.append(f"{cid}: {got} (truth {truth})")
    return out


def run(apps: list, only: "set | None" = None) -> dict:
    """Baseline (unmutated copy) must be all-correct; then each mutant on its own."""
    tmp = Path(tempfile.mkdtemp(prefix="qax-fmut-"))
    root = tmp / "qax"
    _copy_tree(root)
    report = {"apps": [a["name"] for a in apps], "baseline": {}, "mutants": []}
    try:
        for a in apps:
            v, err = _run_app(root, a)
            report["baseline"][a["name"]] = {"wrong": _wrong(a, v) if not err else [], "error": err}
        for name, rel, old, new, why in MUTANTS:
            if only and name not in only:
                continue
            path = root / rel
            src = path.read_text()
            row = {"mutant": name, "breaks": why}
            if src.count(old) != 1:
                row["result"] = "STALE"
                report["mutants"].append(row)
                continue
            path.write_text(src.replace(old, new))
            t0, wrong = time.monotonic(), {}
            try:
                for a in apps:
                    if report["baseline"][a["name"]]["error"] or report["baseline"][a["name"]]["wrong"]:
                        continue          # only apps whose unmutated run is all-correct can judge a mutant
                    v, err = _run_app(root, a)
                    w = [f"run failed: {err}"] if err else _wrong(a, v)
                    if w:
                        wrong[a["name"]] = w
            finally:
                path.write_text(src)
            row.update(result="KILLED" if wrong else "SURVIVED", caught_by=wrong,
                       seconds=round(time.monotonic() - t0, 1))
            report["mutants"].append(row)
            print(f"  {row['result']:8} {name:32} {'; '.join(f'{k}: {v[0]}' for k, v in wrong.items())[:110]}",
                  flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    judged = [m for m in report["mutants"] if m["result"] in ("KILLED", "SURVIVED")]
    report["killed"] = sum(m["result"] == "KILLED" for m in judged)
    report["judged"] = len(judged)
    return report


def _apps(names: str) -> list:
    """Manifest entries with a URL; the built-in demo app is started on a free port."""
    man = json.loads((ROOT / "evals/data/functional_bench.json").read_text())["apps"]
    want = None if names == "all" else set(names.split(","))
    return [a for a in man if want is None or a["name"] in want]


def main(argv: "list | None" = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--app", default="ui-demo")
    ap.add_argument("--only", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--min-killed", type=int, default=0)
    args = ap.parse_args(argv)
    apps = _apps(args.app)
    demo = None
    if any(a.get("builtin") for a in apps):
        demo = subprocess.Popen([sys.executable, str(ROOT / "evals/targets/ui_demo_app.py"), "--port", "8931"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
    for a in apps:
        a["_url"] = "http://127.0.0.1:8931" if a.get("builtin") else a["base_url"]
    try:
        rep = run(apps, set(args.only.split(",")) - {""} or None)
    finally:
        if demo:
            demo.terminate()
    print(f"\nQAX functional mutants: {rep['killed']}/{rep['judged']} killed on {', '.join(rep['apps'])}")
    for m in rep["mutants"]:
        if m["result"] != "KILLED":
            print(f"  {m['result']}: {m['mutant']} — {m['breaks']}")
    if args.json:
        Path(args.json).write_text(json.dumps(rep, indent=2))
    return 0 if rep["killed"] >= args.min_killed else 1


if __name__ == "__main__":
    sys.exit(main())
