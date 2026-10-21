"""
cli/reproduce_cmd.py — `qax reproduce`: does the bug in this ticket still happen?

    python main.py reproduce --ticket ticket.md --base-url http://staging:8000 --project shop --authorized
    python main.py reproduce --jira SHOP-123 --base-url ... --authorized        # reads the issue (read-only)

Exit code: 0 NOT REPRODUCED · 1 REPRODUCED · 2 COULD NOT RUN / NOT DECIDABLE · 3 bad input.
On REPRODUCED a fix brief for a coding agent is written next to the result (--out).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXIT = {"NOT REPRODUCED": 0, "REPRODUCED": 1, "COULD NOT RUN": 2, "NOT DECIDABLE": 2}


def run_reproduce_command() -> None:
    """Handle `reproduce`: read the ticket, run it, print the verdict and why, exit with its code."""
    ap = argparse.ArgumentParser(prog="qax reproduce", description="Run a bug ticket's steps on the app and say whether "
                                                                   "the bug still happens — with the proof.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--ticket", help="A file with the ticket text (title, steps to reproduce, expected, actual).")
    src.add_argument("--jira", help="A Jira issue key — read with JIRA_* from .env (read-only).")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--project", default="")
    ap.add_argument("--repo", default="", help="The app's checkout — the fix brief then lists files to look at.")
    ap.add_argument("--out", default="", help="Folder for result.json and fix-brief.md")
    ap.add_argument("--authorized", action="store_true", help="Confirm you own or are authorized to test this app.")
    args = ap.parse_args(sys.argv[2:])
    if not args.authorized:
        print("\n  QAX: reproducing runs the ticket's steps in a browser against the app.\n"
              "  Re-run with --authorized to confirm you own or are authorized to test it.\n")
        sys.exit(3)
    text = ""
    if args.ticket:
        try:
            text = Path(args.ticket).read_text()
        except Exception as exc:  # noqa: BLE001
            print(f"\n  QAX: could not read {args.ticket}: {exc}\n")
            sys.exit(3)
    else:
        text = _jira_text(args.jira)
        if not text:
            print(f"\n  QAX: could not read {args.jira} from Jira (check JIRA_* in .env).\n")
            sys.exit(3)
    from core.ticket_repro import reproduce
    r = reproduce(text, args.base_url, args.project, repo=args.repo)
    print(f"\n  {r['verdict']} — {r['why']}")
    for n in r.get("notes") or []:
        print(f"  · {n}")
    for x in r.get("results") or []:
        print(f"  {x.get('test_case_id')}: {x.get('status')}")
        for s in x.get("steps") or []:
            mark = {"PASS": "✓", "FAIL": "✗"}.get(s.get("status"), "–")
            print(f"     {mark} {s.get('step_number')}. {str(s.get('actual_result') or '')[:110]}")
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(json.dumps({k: v for k, v in r.items() if k != "fix_brief"}, indent=2, default=str))
        if r.get("fix_brief"):
            (out / "fix-brief.md").write_text(r["fix_brief"])
            print(f"\n  Fix brief for a coding agent: {out / 'fix-brief.md'}")
    sys.exit(EXIT.get(r["verdict"], 2))


def _jira_text(key: str) -> str:
    """The issue's text through the Jira input adapter (read-only); "" when it cannot be read."""
    try:
        from core.input_adapters.jira_adapter import JiraInputAdapter
        items = JiraInputAdapter().fetch(key)
        return str((items[0] or {}).get("content", "")) if items else ""
    except Exception:  # noqa: BLE001
        return ""
