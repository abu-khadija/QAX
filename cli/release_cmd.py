"""
cli/release_cmd.py — `qax release-check`: one decision per change (GO / NO-GO / NOT PROVEN) with the proof.

    python main.py release-check --file cases.json --project shop --base-url http://staging:8000 --authorized
    python main.py release-check ... --changed-since origin/main --repo .     # only what the change can break
    python main.py release-check ... --pr-comment qax-comment.md              # Markdown for the PR

Exit code: 0 GO · 1 NO-GO · 2 NOT PROVEN · 3 could not start (bad input). CI can gate on it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def run_release_check_command() -> None:
    """Handle `release-check`: parse flags, run services.release_check_service, print and exit."""
    ap = argparse.ArgumentParser(prog="qax release-check",
                                 description="Run what a change can break, scan the same app, and decide: "
                                             "GO, NO-GO or NOT PROVEN — with the proof.")
    ap.add_argument("--file", required=True, help="Test cases JSON ({\"test_cases\": [...]}) — browser cases.")
    ap.add_argument("--base-url", required=True, help="The running app (staging).")
    ap.add_argument("--project", default="", help="QAX project: selectors, app map, run history, quarantine.")
    ap.add_argument("--changed-since", default="", metavar="REF", help="Run only the cases this change can affect.")
    ap.add_argument("--repo", default="", metavar="PATH", help="The app's git checkout (for --changed-since).")
    ap.add_argument("--no-scan", action="store_true", help="Skip the security scan (the report then says so).")
    ap.add_argument("--scan-writes", action="store_true",
                    help="Let the scan send data-changing requests (staging only). Default: read-only.")
    ap.add_argument("--fail-on", default="P1", choices=["P1", "P2", "P3"],
                    help="Lowest confirmed security severity that blocks (default P1).")
    ap.add_argument("--a11y", default="basic", choices=["basic", "axe", "off"], help="Page checks while cases run.")
    ap.add_argument("--a11y-fail-on", default="", choices=["", "critical", "serious", "moderate"],
                    help="Make accessibility issues of this impact (axe) block. Default: observations only.")
    ap.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"])
    ap.add_argument("--device", default="desktop", choices=["desktop", "mobile", "mobile-ios", "tablet"])
    ap.add_argument("--pr-comment", default="", metavar="PATH", help="Also write the Markdown report here.")
    ap.add_argument("--json", default="", metavar="PATH", help="Also write the decision as JSON here.")
    ap.add_argument("--authorized", action="store_true",
                    help="Confirm you own or are authorized to test this app (the scan probes it).")
    args = ap.parse_args(sys.argv[2:])

    if not args.authorized:
        print("\n  QAX: a release check runs your cases and probes the app for security bugs.\n"
              "  Re-run with --authorized to confirm you own or are authorized to test it.\n")
        sys.exit(3)
    try:
        cases = json.loads(Path(args.file).read_text(encoding="utf-8")).get("test_cases") or []
    except Exception as exc:  # noqa: BLE001
        print(f"\n  QAX: could not read {args.file}: {exc}\n")
        sys.exit(3)
    if not args.base_url.startswith(("http://", "https://")):
        print("\n  QAX: --base-url must start with http:// or https://\n")
        sys.exit(3)
    if args.changed_since and not args.repo:
        print("\n  QAX: --changed-since needs --repo (the app's git checkout)\n")
        sys.exit(3)

    from services.release_check_service import run_release_check
    d = run_release_check(cases, args.base_url, args.project, repo=args.repo, changed_since=args.changed_since,
                          scan=not args.no_scan, scan_read_only=not args.scan_writes, fail_on=args.fail_on,
                          a11y=args.a11y, a11y_fail_on=args.a11y_fail_on, browser=args.browser, device=args.device,
                          progress=lambda m: print(f"  · {m}", flush=True))
    print("\n" + d["report_md"] + "\n")
    if args.pr_comment:
        Path(args.pr_comment).write_text(d["report_md"], encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps({k: v for k, v in d.items() if k != "report_md"}, indent=2, default=str), encoding="utf-8")
    if d.get("files"):
        print(f"  Report: {d['files'].get('markdown')}")
    sys.exit(d["exit_code"])
