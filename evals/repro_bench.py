"""
evals/repro_bench.py — does QAX tell whether a ticket's bug still happens?
=========================================================================
Each run = a ticket (tests/data/tickets) on an app version, with the truth written before the run
(evals/data/repro_bench.json): REPRODUCED where the bug is present, NOT REPRODUCED where it was fixed or never
existed. No AI. An app that is not reachable, or a missing login variable, is NOT RUN — never counted.

    ./.venv-exec/bin/python -m evals.repro_bench            # demo always; linkding when the corpus runs
Exit 0 when every run that ran is right.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run() -> int:
    """Run every ticket; print one line each; 0 when all that ran are right."""
    from core.ticket_repro import reproduce
    from evals.functional_bench import _reachable
    from evals.generation_quality import _serve_demo
    cfg = json.loads((ROOT / "evals/data/repro_bench.json").read_text())
    demo_url, server = _serve_demo()
    right = wrong = skipped = 0
    rows = []
    try:
        for r in cfg["runs"]:
            url = demo_url if r["app"] == "builtin" else r["app"]
            missing = [e for e in r.get("needs_env", []) if not os.getenv(e)]
            name = f"{Path(r['ticket']).stem} @ {r.get('version', 'demo')}"
            if missing or not _reachable(url):
                skipped += 1
                print(f"  {name:<52} NOT RUN — {'missing ' + ', '.join(missing) if missing else url + ' not reachable'}")
                continue
            t0 = time.monotonic()
            got = reproduce((ROOT / r["ticket"]).read_text(), url, r["project"])
            ok = got["verdict"] == r["truth"]
            right, wrong = right + ok, wrong + (not ok)
            rows.append({"run": name, "truth": r["truth"], "got": got["verdict"], "why": got["why"],
                         "seconds": round(time.monotonic() - t0, 1)})
            print(f"  {name:<52} {got['verdict']:<15} (truth {r['truth']})  {'✅' if ok else '❌ ' + got['why'][:80]}")
    finally:
        if server:
            server.shutdown()
    print(f"\n  QAX bug reproduction: {right}/{right + wrong} right ({skipped} not run)")
    out = Path.cwd() / "output" / "bench" / f"repro_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2))
    return 0 if wrong == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
