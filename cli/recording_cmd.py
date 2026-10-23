"""
cli/recording_cmd.py — `qax import-recording`: a browser recording becomes a QAX test case.

    python main.py import-recording flow.json                       # Chrome DevTools Recorder export
    python main.py import-recording test_flow.py --out cases.json   # Playwright codegen (Python or JS/TS)
    python main.py import-recording a.json b.spec.ts --out cases.json --append

The output is a normal QAX cases file: run it with `execute`, `release-check` or the web UI.
A typed password is replaced by {{env:QAX_VAR_PASSWORD}} — set that variable where QAX runs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def run_import_recording_command() -> None:
    """Handle `import-recording`: convert each file, write or append the cases file, print warnings."""
    from core.recording_import import convert
    ap = argparse.ArgumentParser(prog="qax import-recording",
                                 description="Turn a Chrome Recorder JSON export or a Playwright codegen script into QAX cases.")
    ap.add_argument("files", nargs="+", help="Recording files")
    ap.add_argument("--out", default="recorded_cases.json", help="Cases file to write (default recorded_cases.json)")
    ap.add_argument("--append", action="store_true", help="Add to --out instead of replacing it")
    args = ap.parse_args(sys.argv[2:])

    out = Path(args.out)
    cases = []
    if args.append and out.exists():
        try:
            cases = json.loads(out.read_text(encoding="utf-8")).get("test_cases") or []
        except Exception:  # noqa: BLE001
            cases = []
    ids = {c.get("test_case_id") for c in cases}
    failed = 0
    for f in args.files:
        try:
            text = Path(f).read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {f}: {exc}")
            failed += 1
            continue
        stem = re.sub(r"[^A-Za-z0-9]+", "-", Path(f).name.split(".")[0]).strip("-").upper()[:24] or "FLOW"
        cid, n = f"REC-{stem}", 2
        while cid in ids:
            cid, n = f"REC-{stem}-{n}", n + 1
        r = convert(text, case_id=cid)
        if not r["case"]:
            print(f"  ✗ {f}: {'; '.join(r['warnings'])}")
            failed += 1
            continue
        ids.add(cid)
        cases.append(r["case"])
        print(f"  ✓ {f} ({r['format']}) → {cid}: {len(r['case']['automation_steps'])} steps")
        for w in r["warnings"]:
            print(f"      ⚠ {w}")
        for s in r["skipped"]:
            print(f"      · not converted: {s}")
        if r["needs_env"]:
            print(f"      · set before running: {', '.join(r['needs_env'])} (the recorded value was dropped)")
    out.write_text(json.dumps({"_comment": "Imported from browser recordings (qax import-recording).",
                               "test_cases": cases}, indent=2), encoding="utf-8")
    print(f"\n  {len(cases)} case(s) in {out}")
    sys.exit(1 if failed else 0)
