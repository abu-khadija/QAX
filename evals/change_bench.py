"""
evals/change_bench.py — does change-based selection miss regressions?
=====================================================================
For each seeded change (evals/data/change_bench.json): make the edit in a throw-away git
worktree of the app, build and start it, run EVERY case (truth: the cases whose verdict
differs from the unchanged app), then ask core/change_selection which cases the change can
affect. Scored:

  missed regressions   truth cases the selection left out          (must be 0)
  cases saved          1 − selected / all                           (higher is better, never at the cost above)

The worktree, branch and process are removed afterwards; the app's own checkout is untouched.

    ./.venv-exec/bin/python -m evals.change_bench          # needs Java and the corpus app
Exit 0 when no regression was missed. Never raises.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JAVA = os.getenv("QAX_JAVA", "/opt/homebrew/opt/openjdk/bin/java")


def _verdicts(cases: list, url: str, project: str) -> dict:
    from evals.functional_bench import _run_cases
    return _run_cases(cases, url, project)[0]


def _wait(url: str, secs: int = 120) -> bool:
    end = time.time() + secs
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=3)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(2)
    return False


def _start(cmd: str, cwd: Path, url: str):
    p = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    return p if _wait(url) else (p.kill() or None)


def run(out: str = "") -> int:
    from core.change_selection import changed_files, select
    cfg = json.loads((ROOT / "evals/data/change_bench.json").read_text(encoding="utf-8"))
    app = cfg["app"]
    repo = Path(os.path.expanduser(app["repo"]))
    cases = json.loads((ROOT / app["cases"]).read_text(encoding="utf-8"))["test_cases"]
    port = app["port"]
    url = f"http://127.0.0.1:{port}"
    project_dir = ROOT / "config" / "projects" / app["project"]
    record = {"app": str(repo), "seeds": []}
    worst = 0

    # baseline: the unchanged app
    base = repo
    proc = _start(app["run"].format(java=JAVA, port=port), base, url)
    if not proc:
        print("  NOT RUN — the unchanged app did not start")
        return 2
    try:
        baseline = _verdicts(cases, url, app["project"])
    finally:
        os.killpg(proc.pid, 15)
        time.sleep(3)
    print(f"  baseline: {sum(v == 'PASS' for v in baseline.values())} PASS / {len(baseline)}")

    for seed in cfg["seeds"]:
        wt = repo.parent / f".seed-{seed['name']}"
        branch = f"qax-seed-{seed['name']}"
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "branch", "-D", branch], capture_output=True)
        try:
            subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", branch, str(wt)], check=True,
                           capture_output=True)
            f = wt / seed["file"]
            text = f.read_text(encoding="utf-8")
            assert seed["find"] in text, f"seed anchor not found in {seed['file']}"
            f.write_text(text.replace(seed["find"], seed["replace"], 1), encoding="utf-8")
            subprocess.run(["git", "-C", str(wt), "commit", "-qam", f"seed: {seed['name']}"], check=True,
                           capture_output=True)
            files, err = changed_files(str(wt), "HEAD~1", "HEAD")
            b = subprocess.run(app["build"], shell=True, cwd=wt, capture_output=True, text=True,
                               env=dict(os.environ, JAVA_HOME=str(Path(JAVA).parent.parent)))
            if b.returncode != 0:
                raise RuntimeError("build failed: " + b.stderr[-300:])
            proc = _start(app["run"].format(java=JAVA, port=port), wt, url)
            if not proc:
                raise RuntimeError("the seeded app did not start")
            try:
                now = _verdicts(cases, url, app["project"])
            finally:
                os.killpg(proc.pid, 15)
                time.sleep(3)
            truth = sorted(k for k in now if now[k] != baseline.get(k))
            sel = select(cases, files, str(wt), project_dir)
            chosen = {s["id"] for s in sel["selected"]}
            missed = [t for t in truth if t not in chosen]
            saved = round(1 - len(chosen) / len(cases), 2)
            rec = {"seed": seed["name"], "changed": files, "regressions": truth, "selected": sorted(chosen),
                   "run_all": sel["run_all"], "why": sel["why"], "missed": missed, "cases_saved": saved}
            record["seeds"].append(rec)
            worst = max(worst, 1 if missed else 0)
            print(f"  {seed['name']:<42} regressions {truth or '—'} · selected {len(chosen)}/{len(cases)}"
                  f"{' (run all)' if sel['run_all'] else ''} · missed {missed or 0} · saved {int(saved * 100)}%"
                  + ("  ❌" if missed else "  ✅"))
        except Exception as exc:  # noqa: BLE001
            print(f"  {seed['name']:<42} NOT RUN — {exc}")
            record["seeds"].append({"seed": seed["name"], "error": str(exc)[:300]})
            worst = max(worst, 2)
        finally:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
            subprocess.run(["git", "-C", str(repo), "branch", "-D", branch], capture_output=True)
            shutil.rmtree(wt, ignore_errors=True)
    path = Path(out or (Path.cwd() / "output" / "bench" / f"change_selection_{int(time.time())}.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\n  Raw results: {path}")
    return worst


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else ""))
