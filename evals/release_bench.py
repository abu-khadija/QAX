"""
evals/release_bench.py — does the release check give the right decision?
=======================================================================
The same seeded changes to Spring PetClinic as evals/change_bench.py, each built and run in a
throw-away git worktree, plus the unchanged app as the control. For each, `run_release_check`
runs only what the change can affect (the team's healthy suite — negative controls are benchmark
devices, not part of a team's suite), scans the same app read-only, and decides.

Truth, written before the run:
  unchanged app                 → GO          (nothing broke)
  every seed (it breaks a case) → NO-GO       (the regression measured by change_bench)

Scored: decisions equal to the truth (must be all). The worktree, branch and process are removed.

    ./.venv-exec/bin/python -m evals.release_bench          # needs Java and the corpus app
Exit 0 when every decision is right.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.change_bench import JAVA, _start  # noqa: E402


def _healthy(cases: list) -> list:
    """What a team would run: the cases whose truth is PASS."""
    return [c for c in cases if str(c.get("expected_outcome", "PASS")).upper().startswith("PASS")]


def _check(cases, url, project, repo, since, scan):
    from services.release_check_service import run_release_check
    d = run_release_check(cases, url, project, repo=repo, changed_since=since, scan=scan, scan_read_only=True,
                          progress=lambda m: None)
    return {"verdict": d["verdict"], "blockers": [f"{b['kind']} {b['id']}" for b in d["blockers"]],
            "not_proven": [f"{n['kind']} {n['id']} {n.get('status')}" for n in d["not_proven"]],
            "selection": d["selection"]["why"], "security": d["security"], "seconds": d["duration_s"]}


def run(out: str = "", scan: bool = True) -> int:
    """Control + each seed; prints one line each; returns 0 when every decision is right."""
    cfg = json.loads((ROOT / "evals/data/change_bench.json").read_text())
    app = cfg["app"]
    repo = Path(os.path.expanduser(app["repo"]))
    cases = _healthy(json.loads((ROOT / app["cases"]).read_text())["test_cases"])
    port, project = app["port"], app["project"]
    url = f"http://127.0.0.1:{port}"
    rows, wrong = [], 0

    def judge(name, truth, res):
        nonlocal wrong
        ok = res.get("verdict") == truth
        wrong += 0 if ok else 1
        rows.append(dict(res, name=name, truth=truth, correct=ok))
        print(f"  {name:<42} {res.get('verdict', 'ERROR'):<10} (truth {truth}) "
              f"{'; '.join(res.get('blockers') or res.get('not_proven') or [])[:90]}  {'✅' if ok else '❌'}", flush=True)

    # control: the unchanged app, no change → every healthy case runs → GO
    proc = _start(app["run"].format(java=JAVA, port=port), repo, url)
    if not proc:
        print("  NOT RUN — the unchanged app did not start")
        return 2
    try:
        judge("unchanged (control)", "GO", _check(cases, url, project, str(repo), "HEAD", scan))
    finally:
        os.killpg(proc.pid, 15)
        time.sleep(3)

    for seed in cfg["seeds"]:
        wt = repo.parent / f".release-{seed['name']}"
        branch = f"qax-release-{seed['name']}"
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "branch", "-D", branch], capture_output=True)
        try:
            subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", branch, str(wt)], check=True,
                           capture_output=True)
            f = wt / seed["file"]
            f.write_text(f.read_text().replace(seed["find"], seed["replace"], 1))
            subprocess.run(["git", "-C", str(wt), "commit", "-qam", f"seed: {seed['name']}"], check=True,
                           capture_output=True)
            b = subprocess.run(app["build"], shell=True, cwd=wt, capture_output=True, text=True,
                               env=dict(os.environ, JAVA_HOME=str(Path(JAVA).parent.parent)))
            if b.returncode != 0:
                raise RuntimeError("build failed: " + b.stderr[-300:])
            proc = _start(app["run"].format(java=JAVA, port=port), wt, url)
            if not proc:
                raise RuntimeError("the seeded app did not start")
            try:
                judge(seed["name"], "NO-GO", _check(cases, url, project, str(wt), "HEAD~1", scan))
            finally:
                os.killpg(proc.pid, 15)
                time.sleep(3)
        except Exception as exc:  # noqa: BLE001
            judge(seed["name"], "NO-GO", {"verdict": "ERROR", "not_proven": [str(exc)[:200]]})
        finally:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
            subprocess.run(["git", "-C", str(repo), "branch", "-D", branch], capture_output=True)
            shutil.rmtree(wt, ignore_errors=True)

    print(f"\n  QAX release check: {len(rows) - wrong}/{len(rows)} decisions right")
    path = Path(out or (Path.cwd() / "output" / "bench" / f"release_check_{int(time.time())}.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, default=str))
    print(f"  Raw results: {path}")
    return 0 if wrong == 0 else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else ""))
