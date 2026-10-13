"""
evals/reality_check.py — the Reality Check dashboard
====================================================
Phase 1 of the roadmap runs QAX against external repos and must be MEASURED, not
felt. Every `qax scan` already writes a scorecard record to the ledger
(output/scorecard/ledger.jsonl). This reads that ledger and renders an at-a-glance
table across runs and repos: recall, false-positive %, $/run, end-to-end rate.

It executes nothing and calls no model — pure aggregation over the records a scan
already produced. Run it any time to see where QAX stands across the Reality Check:

    python3 -m evals.reality_check                       # default ledger
    python3 -m evals.reality_check --ledger path.jsonl   # a specific ledger
    python3 -m evals.reality_check --latest-per-repo     # one row per repo (newest)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_LEDGER = "output/scorecard/ledger.jsonl"


def load_ledger(path: str = DEFAULT_LEDGER) -> list[dict]:
    """Read scorecard records from a JSONL ledger. Missing/!bad lines are skipped."""
    records: list[dict] = []
    p = Path(path)
    if not p.exists():
        return records
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records


def latest_per_repo(records: list[dict]) -> list[dict]:
    """Keep only the newest record for each (repo, mode), preserving ledger order."""
    seen: dict = {}
    for rec in records:
        key = (rec.get("repo"), rec.get("mode"))
        seen[key] = rec  # later lines overwrite — the ledger is append-only chronological
    return list(seen.values())


def summarize(records: list[dict]) -> dict:
    """
    Aggregate a set of scorecard records into headline numbers.

    Returns totals plus averages over the runs that carry each metric (recall and
    FP% only count runs that had a truth file; cost only counts tracked runs).
    """
    runs = len(records)
    recalls = [r["accuracy"]["recall_pct"] for r in records
               if r.get("accuracy") and r["accuracy"].get("recall_pct") is not None]
    fps = [r["accuracy"]["false_positive_pct"] for r in records
           if r.get("accuracy") and r["accuracy"].get("false_positive_pct") is not None]
    costs = [r["cost"]["estimated_usd"] for r in records
             if r.get("cost") and r["cost"].get("tracked") and r["cost"].get("estimated_usd") is not None]
    deep = [r for r in records if r.get("mode") == "deep"]
    deep_done = [r for r in deep if r.get("completed_end_to_end")]

    def _avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    return {
        "runs": runs,
        "repos": sorted({r.get("repo", "?") for r in records}),
        "avg_recall_pct": _avg(recalls),
        "avg_false_positive_pct": _avg(fps),
        "fp_target_pct": 15,
        "fp_within_target": (all(f < 15 for f in fps) if fps else None),
        "total_cost_usd": round(sum(costs), 4) if costs else 0.0,
        "deep_runs": len(deep),
        "deep_completed": len(deep_done),
    }


def render_table(records: list[dict]) -> str:
    """A compact per-run table of the Reality Check so far."""
    if not records:
        return "  (no scorecard records yet — run a scan with --truth to populate the ledger)"
    header = f"  {'repo':<22} {'mode':<6} {'bugs':>4} {'recall':>7} {'FP%':>5} {'$':>8} {'e2e':>4}"
    lines = [header, "  " + "-" * (len(header) - 2)]
    for r in records:
        acc = r.get("accuracy") or {}
        cost = r.get("cost") or {}
        recall = f"{acc.get('recall_pct')}%" if acc.get("recall_pct") is not None else "—"
        fp = f"{acc.get('false_positive_pct')}" if acc.get("false_positive_pct") is not None else "—"
        usd = f"${cost.get('estimated_usd')}" if cost.get("tracked") else "—"
        e2e = "yes" if r.get("completed_end_to_end") else "no"
        lines.append(f"  {Path(r.get('repo', '?')).name:<22} {str(r.get('mode', '?')):<6} "
                     f"{r.get('confirmed_count', 0):>4} {recall:>7} {fp:>5} {usd:>8} {e2e:>4}")
    return "\n".join(lines)


def render_summary(summary: dict) -> str:
    """Headline numbers for the Reality Check."""
    fp = summary.get("avg_false_positive_pct")
    fp_line = (f"{fp}% (target <{summary['fp_target_pct']}%)"
               if fp is not None else "— (no ground-truth runs yet)")
    recall = summary.get("avg_recall_pct")
    return "\n".join([
        f"  Reality Check — {summary['runs']} run(s) across {len(summary['repos'])} repo(s)",
        f"     avg recall:         {recall}%" if recall is not None else "     avg recall:         —",
        f"     avg false-positive: {fp_line}",
        f"     deep scans done:    {summary['deep_completed']}/{summary['deep_runs']}",
        f"     total LLM cost:     ${summary['total_cost_usd']}",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(prog="qax reality-check",
                                     description="Aggregate the scorecard ledger into a Reality Check dashboard.")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER, help="Path to the scorecard ledger (JSONL).")
    parser.add_argument("--latest-per-repo", action="store_true",
                        help="Show only the newest run per (repo, mode).")
    args = parser.parse_args()

    records = load_ledger(args.ledger)
    if args.latest_per_repo:
        records = latest_per_repo(records)

    print()
    print(render_summary(summarize(records)))
    print()
    print(render_table(records))
    print()


if __name__ == "__main__":
    main()
