"""
cli/file_findings.py — `qax file-findings`: file a scan's confirmed bugs to Jira
===============================================================================
Reads a scan result (`qax scan --out result.json`) and files its CONFIRMED,
deterministic findings (probe / coverage — B1) as Jira Bugs via
core.jira_bugs.JiraBugFiler. AI leads are never filed unless asked for.

Dry run by default: prints exactly what WOULD be filed (summary, labels, fields
dropped because the Bug screen lacks them, open duplicates) using only read-only
Jira calls. `--live` files for real. Pure helpers are importable for tests.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_DETERMINISTIC = ("probe", "coverage")


def select_findings(result: dict, include_leads: bool = False) -> tuple[list[dict], int]:
    """(findings to file, number of AI leads skipped) from a scan result dict."""
    confirmed = [b for b in (result.get("confirmed") or []) if isinstance(b, dict)]
    if include_leads:
        return confirmed, 0
    keep = [b for b in confirmed if str(b.get("source", "")) in _DETERMINISTIC]
    return keep, len(confirmed) - len(keep)


def enrich(finding: dict) -> dict:
    """Attach fix guidance + QA report fields (why/fix, how/expected/data-safety)."""
    from services.remediation import qa_for, remediation_for
    b = dict(finding)
    b.setdefault("remediation", remediation_for(b))
    b.setdefault("qa", qa_for(b))
    return b


def environment_of(result: dict, override: str | None = None, client_safe: bool = False) -> str:
    """
    The Environment line for the report, from the scan result (or --env). Client-safe
    tickets never name the tool, so the "QAX quick scan" qualifier is dropped there.
    """
    if override:
        return override
    url = result.get("target_url")
    if not url:
        return "the scanned application" + ("" if client_safe else
                                             " (re-run qax scan with --out to record its URL)")
    bits = [str(url)]
    if result.get("scan_mode") and not client_safe:
        bits.append(f"QAX {result['scan_mode']} scan")
    if result.get("scanned_at"):
        bits.append(str(result["scanned_at"]))
    return " / ".join(bits)


def render_results(out: dict, *, dry_run: bool, project: str) -> str:
    """Human-readable report of a JiraBugFiler.file_many result."""
    mode = "DRY RUN — nothing filed" if dry_run else "LIVE"
    lines = [f"\n  QAX → Jira ({project}) · {mode}\n"]
    for r in out.get("results") or []:
        prev = (r.get("preview") or {}).get("fields") or {}
        summary = prev.get("summary") or r.get("summary") or r.get("test_case_id")
        if r.get("duplicate_of"):
            status = f"⏭  already open as {r['duplicate_of']} — not filed again"
        elif not r.get("success"):
            status = f"✗  failed: {r.get('error')}"
        elif dry_run:
            status = "◌  would create Bug" + (f" (+ attach {r['would_attach']})" if r.get("would_attach") else "")
        else:
            ev = {True: " · evidence attached", False: " · ⚠ evidence NOT attached"}.get(r.get("attachment"), "")
            status = f"✓  created {r.get('jira_key')}{ev}"
        lines.append(f"  {status}")
        if summary:
            lines.append(f"     {summary}")
        if prev.get("labels"):
            lines.append(f"     labels: {', '.join(prev['labels'])}")
        if r.get("dropped_fields"):
            lines.append(f"     not on this project's Bug screen (omitted): {', '.join(r['dropped_fields'])}")
    lines.append(f"\n  new: {out.get('exported_count', 0)} · duplicates: {out.get('duplicate_count', 0)}"
                 f" · failed: {out.get('failed_count', 0)}\n")
    return "\n".join(lines)


def run(argv: list[str]) -> int:
    """Entry point for `qax file-findings`. Returns a process exit code."""
    p = argparse.ArgumentParser(prog="qax file-findings",
                                description="File a scan's confirmed findings to Jira as Bugs.")
    p.add_argument("--from", dest="src", required=True, help="Scan result JSON (qax scan --out).")
    p.add_argument("--live", action="store_true",
                   help="File for real. Without it this is a dry run (read-only preview).")
    p.add_argument("--project", default=None, help="Jira project key (default: JIRA_PROJECT_KEY).")
    p.add_argument("--include-leads", action="store_true",
                   help="Also file AI leads (non-deterministic). Off by default.")
    p.add_argument("--show-payload", action="store_true", help="Print the full JSON payload(s).")
    p.add_argument("--client-safe", action="store_true",
                   help="Write the ticket so it reads like a plain manual bug report: no tool name in "
                        "the body or labels (for tickets a developer or client will read).")
    p.add_argument("--env", default=None,
                   help='Environment line for the report, e.g. "Gitea 28.1.0 @ http://127.0.0.1:3200 (local test)".')
    args = p.parse_args(argv)

    try:
        result = json.loads(Path(args.src).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"\n  Could not read scan result {args.src}: {exc}\n")
        return 1

    findings, skipped = select_findings(result, args.include_leads)
    if not findings:
        print("\n  No confirmed deterministic findings to file.\n")
        return 0

    from core.jira_bugs import JiraBugFiler, finding_to_bug
    from dotenv import load_dotenv
    load_dotenv()
    filer = JiraBugFiler(dry_run=not args.live, project_key=args.project)
    if args.live and not filer.configured():
        print("\n  Jira is not configured — set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, "
              "JIRA_PROJECT_KEY in .env.\n")
        return 1
    env = environment_of(result, args.env, client_safe=args.client_safe)
    bugs = [finding_to_bug(enrich(b), i, environment=env, base_url=result.get("target_url"),
                           client_safe=args.client_safe, build=result.get("build"),
                           reproduced=tuple(b["reproduced"]) if b.get("reproduced") else None)
            for i, b in enumerate(findings)]
    out = filer.file_many(bugs)
    print(render_results(out, dry_run=not args.live, project=filer.project_key))
    if skipped:
        print(f"  ({skipped} AI lead(s) not filed — use --include-leads to file them.)\n")
    if args.show_payload:
        for bug, r in zip(bugs, out.get("results") or []):
            print(f"\n  ── {bug['title']} ──\n")
            print(bug["description"])
        print()
    return 0 if out.get("failed_count", 0) == 0 else 2
