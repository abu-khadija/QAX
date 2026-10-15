"""
cli/verify_fixes.py — `qax verify-fixes`: are the bugs QAX filed actually fixed?
================================================================================
Reads the tickets QAX filed (by their fingerprint label), compares them with a FRESH
scan, and reports three buckets that are never rounded together:

    ✅ FIXED              no longer reproduces, and its probe actually ran
    ❌ STILL REPRODUCES   fires again (and whether the build changed / the ticket is closed)
    ❓ COULD NOT TEST     we cannot honestly say — with the reason

Read-only by default. `--apply` (explicit) comments on FIXED tickets and moves them to a
Done status; it never touches the other two buckets. See core/jira_verify.py for the rules.

    qax verify-fixes --from scan.json                       # compare with an existing scan
    qax verify-fixes --url http://app --authorized --token T # run a fresh scan, then compare
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from core.jira_verify import FIXED, STILL, UNKNOWN, JiraTicketReader, summarize, verdict_for

_ICON = {FIXED: "✅", STILL: "❌", UNKNOWN: "❓"}


def render_report(verdicts: list, summary: dict, scan: dict, applied: list | None = None,
                  dry_run: bool = True) -> str:
    """The console report (pure). Grouped by bucket; the three counts are shown separately."""
    from core.build_fingerprint import describe
    lines = ["", f"  QAX verify-fixes — {summary['total']} ticket(s) vs a fresh scan of "
                 f"{scan.get('target_url') or '?'}  (build {describe(scan.get('build'))}, "
                 f"{scan.get('scanned_at') or 'time unknown'})", ""]
    for bucket in (STILL, FIXED, UNKNOWN):
        group = [v for v in verdicts if v.bucket == bucket]
        if not group:
            continue
        lines.append(f"  {_ICON[bucket]} {bucket} ({len(group)})")
        for v in group:
            t = v.ticket
            flag = ("  ⚠ CLOSED BUT NOT FIXED" if v.closed_but_reproduces else
                    "  ⚠ build unverified" if (v.bucket == FIXED and v.weak) else "")
            lines.append(f"    {t.key} [{t.status or '?'}] {t.summary[:70]}{flag}")
            lines.append(f"        {v.reason}")
        lines.append("")
    lines.append(f"  fixed {summary[FIXED]} · still reproduces {summary[STILL]} · could not test "
                 f"{summary[UNKNOWN]}   (of {summary['total']}; never rounded together)")
    if summary["fixed_build_unverified"]:
        lines.append(f"  ⚠ {summary['fixed_build_unverified']} of the fixed ticket(s) could not be tied to a deploy "
                     f"(build unverified) — treat as 'probably fixed', and note --apply skips them.")
    if summary["closed_but_reproduces"]:
        lines.append(f"  ⚠ {summary['closed_but_reproduces']} ticket(s) are already closed but still reproduce.")
    if applied is not None:
        mode = "DRY RUN — nothing written" if dry_run else "APPLIED"
        lines.append(f"\n  --apply · {mode}: {len(applied)} FIXED ticket(s) to comment on and move to Done"
                     + ("  (add --yes to write)" if dry_run else ""))
        for a in applied:
            if a.get("skipped"):
                lines.append(f"    {a['key']}: SKIPPED — {a['skipped']}")
                continue
            lines.append(f"    {a['key']}: " + ("would comment + move to Done" if dry_run else
                         f"commented={a['commented']} moved={a['transitioned']}" + (f" ({a['error']})" if a.get("error") else "")))
    elif summary[FIXED]:
        lines.append("\n  QAX never closes tickets on its own — review the ✅ list, then use --apply "
                     "(shows the plan) and --apply --yes (writes; only FIXED tickets are touched).")
    else:
        lines.append("\n  Nothing to close: QAX never closes tickets on its own.")
    return "\n".join(lines) + "\n"


def _fresh_scan(args, extra: list[str]) -> dict:
    """Run `main.py scan --quick` against --url and load its JSON (raises on failure)."""
    out = Path(tempfile.mkdtemp(prefix="qax_verify_")) / "scan.json"
    cmd = [sys.executable, str(Path(__file__).resolve().parent.parent / "main.py"), "scan",
           "--url", args.url, "--quick", "--authorized", "--out", str(out)] + extra
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"the fresh scan failed (exit {proc.returncode}): {(proc.stdout + proc.stderr)[-400:]}")
    return json.loads(out.read_text(encoding="utf-8"))


def run(argv: list[str]) -> int:
    """Entry point for `qax verify-fixes`. Returns a process exit code."""
    p = argparse.ArgumentParser(prog="qax verify-fixes",
                                description="Re-verify the bugs QAX filed against a fresh scan.")
    p.add_argument("--from", dest="src", default=None, help="An existing scan result JSON (qax scan --out).")
    p.add_argument("--url", default=None, help="Run a fresh quick scan of this app first.")
    p.add_argument("--authorized", action="store_true", help="Confirm you may test --url (needed with --url).")
    p.add_argument("--token", default=None); p.add_argument("--token-b", dest="token_b", default=None)
    p.add_argument("--auth-scheme", dest="auth_scheme", default=None)
    p.add_argument("--reverify", type=int, default=0, metavar="N", help="Passes to re-run the scan (see qax scan).")
    p.add_argument("--project", default=None, help="Jira project key (default: JIRA_PROJECT_KEY).")
    p.add_argument("--apply", action="store_true",
                   help="Show the plan to comment on FIXED tickets and move them to Done (writes nothing).")
    p.add_argument("--include-unverified-build", dest="include_unverified", action="store_true",
                   help="With --apply: also act on FIXED tickets whose fix could not be tied to a deploy.")
    p.add_argument("--yes", action="store_true",
                   help="With --apply: actually write. Without it --apply only shows what it WOULD do.")
    p.add_argument("--out", default=None, help="Write the full verdict report as JSON.")
    p.add_argument("--fail-on-regression", action="store_true",
                   help="Exit 2 when a CLOSED ticket still reproduces (for CI).")
    args = p.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv()
    if bool(args.src) == bool(args.url):
        print("\n  Give exactly one of --from scan.json or --url <app> (--authorized).\n")
        return 1
    try:
        if args.src:
            scan = json.loads(Path(args.src).read_text(encoding="utf-8"))
        else:
            if not args.authorized:
                print("\n  --url runs active probes: pass --authorized to confirm you may test that app.\n")
                return 1
            extra: list[str] = []
            for flag, val in (("--token", args.token), ("--token-b", args.token_b),
                              ("--auth-scheme", args.auth_scheme)):
                if val is not None:
                    extra += [flag, val]
            if args.reverify:
                extra += ["--reverify", str(args.reverify)]
            print(f"\n  Running a fresh scan of {args.url} …")
            scan = _fresh_scan(args, extra)
    except Exception as exc:
        print(f"\n  Could not get a scan result: {exc}\n")
        return 1

    reader = JiraTicketReader(project_key=args.project) if args.project else JiraTicketReader()
    if not reader.configured():
        print("\n  Jira is not configured — set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, "
              "JIRA_PROJECT_KEY in .env.\n")
        return 1
    try:
        tickets = reader.list_tickets(args.project)
    except Exception as exc:
        print(f"\n  Could not read tickets from Jira: {exc}\n")
        return 1
    if not tickets:
        print("\n  No QAX-filed tickets found (looking for the security label + a fingerprint label).\n")
        return 0

    verdicts = [verdict_for(t, scan) for t in tickets]
    summary = summarize(verdicts)
    applied = None
    if args.apply:
        applied = reader.apply_fixed(verdicts, env=str(scan.get("target_url") or ""), dry_run=not args.yes,
                                     include_unverified_build=args.include_unverified)
    print(render_report(verdicts, summary, scan, applied, dry_run=not args.yes))
    if args.out:
        Path(args.out).write_text(json.dumps({
            "scan": {k: scan.get(k) for k in ("target_url", "scanned_at", "build", "scan_mode")},
            "summary": summary,
            "tickets": [{"key": v.ticket.key, "status": v.ticket.status, "bucket": v.bucket,
                         "reason": v.reason, "closed_but_reproduces": v.closed_but_reproduces}
                        for v in verdicts]}, indent=2, ensure_ascii=False), encoding="utf-8")
    return 2 if (args.fail_on_regression and summary["closed_but_reproduces"]) else 0
