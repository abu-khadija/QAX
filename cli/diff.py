"""
cli/diff.py — `qax diff OLD.json NEW.json`: what changed between two scans
==========================================================================
ROADMAP A2. Compares two `qax scan --out` results, finding by finding, keyed on the same fingerprint
that labels Jira tickets (title + target). The rule is the one verify-fixes follows:
**a finding that is gone is not "fixed" until we know the check really ran on a changed build.**

Buckets:
  NEW            — confirmed now, not before;
  STILL THERE    — confirmed in both;
  GONE           — confirmed before, not now, its probe ran cleanly AND the build changed (or is unknown);
  NOT RE-CHECKED — confirmed before, not now, but its probe is in the new scan's not-tested ledger
                   (could not run / read-only / crashed / unstable) — absence of a finding != a pass;
  FLAKY?         — confirmed before, not now, on the SAME build: a bug that vanishes without a deploy;
  ACCEPTED       — in the new scan's ACCEPTED block (core.accepted);
  AI LEAD        — AI (non-deterministic) findings are reported separately and never called gone/fixed.

Exit: 0, or 1 with --fail-on-new when a NEW finding at a listed severity exists; 2 = unreadable input.
Pure functions, never raise.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_DETERMINISTIC = ("probe", "coverage")

NEW, STILL, GONE, NOT_RECHECKED, FLAKY, ACCEPTED = (
    "NEW", "STILL THERE", "GONE", "NOT RE-CHECKED", "FLAKY?", "ACCEPTED")
ORDER = (NEW, STILL, NOT_RECHECKED, FLAKY, GONE, ACCEPTED)


def _key(b: dict) -> str:
    from core.jira_bugs import fingerprint
    return fingerprint(str(b.get("title", "")), str(b.get("target", "")))


def _det(scan: dict) -> dict:
    return {_key(b): b for b in (scan.get("confirmed") or [])
            if isinstance(b, dict) and str(b.get("source", "")) in _DETERMINISTIC}


def _ai(scan: dict) -> list:
    return [b for b in (scan.get("confirmed") or [])
            if isinstance(b, dict) and str(b.get("source", "")) not in _DETERMINISTIC]


def _ledger_for(scan: dict, b: dict) -> dict | None:
    """The new scan's not-tested entry that explains why this finding could not reappear, if any."""
    probe = str(b.get("probe") or "")
    for n in scan.get("not_tested") or []:
        if not isinstance(n, dict):
            continue
        if n.get("kind") == "not-applicable" and n.get("probe") == probe:
            return n
        if n.get("kind") in ("missing-prerequisite", "error", "read-only") and n.get("probe") == probe:
            return n
        if n.get("kind") == "unstable" and _key(n) == _key(b):
            return n
    return None


def compare(old: dict, new: dict) -> dict:
    """{"rows": [{"bucket", "finding", "why"}], "ai": {"old": n, "new": n}, "warnings": [...]}"""
    from core.build_fingerprint import describe, same_build
    warnings = []
    o_url, n_url = old.get("target_url"), new.get("target_url")
    if o_url and n_url and o_url.rstrip("/") != n_url.rstrip("/"):
        warnings.append(f"the scans are of different targets ({o_url} vs {n_url}) — the comparison proves little")
    if old.get("scan_mode") and new.get("scan_mode") and old["scan_mode"] != new["scan_mode"]:
        warnings.append(f"scan modes differ ({old['scan_mode']} vs {new['scan_mode']}) — deep finds things quick does not")
    same = same_build(old.get("build"), new.get("build"))
    a, b = _det(old), _det(new)
    accepted = {_key(x): x for x in (new.get("accepted") or []) if isinstance(x, dict)}
    rows = []
    for k, f in b.items():
        if k in a:
            sev_was = a[k].get("severity")
            why = "" if sev_was == f.get("severity") else f"severity {sev_was} → {f.get('severity')}"
            rows.append({"bucket": STILL, "finding": f, "why": why})
        else:
            rows.append({"bucket": NEW, "finding": f, "why": ""})
    for k, f in a.items():
        if k in b:
            continue
        if k in accepted:
            e = accepted[k].get("accepted") or {}
            rows.append({"bucket": ACCEPTED, "finding": f,
                         "why": f"{e.get('category', 'accepted')}: {e.get('reason', '')}".strip(": ")})
            continue
        led = _ledger_for(new, f)
        if led:
            rows.append({"bucket": NOT_RECHECKED, "finding": f,
                         "why": f"{led.get('kind')}: {led.get('reason', '')}"})
        elif same is True:
            rows.append({"bucket": FLAKY, "finding": f,
                         "why": f"gone on the SAME build ({describe(new.get('build'))}) — flaky, not fixed; "
                                f"re-run with --reverify 2"})
        else:
            rows.append({"bucket": GONE, "finding": f,
                         "why": ("the build changed" if same is False else "build unknown") +
                                " and its probe ran cleanly — likely fixed (confirm with verify-fixes)"})
    rows.sort(key=lambda r: (ORDER.index(r["bucket"]), str(r["finding"].get("severity", "P9"))))
    return {"rows": rows, "ai": {"old": len(_ai(old)), "new": len(_ai(new))}, "warnings": warnings,
            "builds": {"old": describe(old.get("build")), "new": describe(new.get("build"))}}


def render(rep: dict) -> str:
    """Console text."""
    counts = {bk: sum(1 for r in rep["rows"] if r["bucket"] == bk) for bk in ORDER}
    head = " · ".join(f"{bk} {counts[bk]}" for bk in ORDER if counts[bk])
    lines = [f"QAX diff — {head or 'no deterministic findings in either scan'}",
             f"  builds: {rep['builds']['old']} → {rep['builds']['new']}"]
    lines += [f"  ⚠ {w}" for w in rep["warnings"]]
    for bk in ORDER:
        rows = [r for r in rep["rows"] if r["bucket"] == bk]
        if not rows:
            continue
        whys = {r["why"] for r in rows}
        shared = whys.pop() if len(whys) == 1 and len(rows) > 1 else None
        lines.append(f"\n  {bk}" + (f"  — {shared}" if shared else ""))
        for r in rows:
            f = r["finding"]
            own = "" if shared is not None or not r["why"] else f"  — {r['why']}"
            lines.append(f"    [{f.get('severity', '?')}] {f.get('title', '')}{own}")
    ai = rep["ai"]
    if ai["old"] or ai["new"]:
        lines.append(f"\n  AI leads (not compared — they may vary between runs): {ai['old']} → {ai['new']}")
    return "\n".join(lines)


def run(argv: list[str]) -> int:
    """Entry point for `qax diff`."""
    p = argparse.ArgumentParser(prog="qax diff", description="Compare two `qax scan --out` results.")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--fail-on-new", default="", metavar="SEVERITIES",
                   help='exit 1 when a NEW finding has one of these severities (e.g. "P1,P2")')
    a = p.parse_args(argv)
    try:
        old = json.loads(Path(a.old).read_text(encoding="utf-8"))
        new = json.loads(Path(a.new).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"QAX: cannot read the scan files: {exc}")
        return 2
    rep = compare(old, new)
    print(json.dumps(rep, indent=2, default=str) if a.json else render(rep))
    fail = {s.strip().upper() for s in a.fail_on_new.split(",") if s.strip()}
    if fail and any(r["bucket"] == NEW and str(r["finding"].get("severity", "")).upper() in fail for r in rep["rows"]):
        return 1
    return 0
