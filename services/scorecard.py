"""
services/scorecard.py — turn a scan run into honest, trackable metrics
======================================================================
The honest critique of QAX was "there is no real number that measures quality".
A scorecard is that number. After every scan it records, per run: how many bugs
were confirmed, which LAYER caught each (deterministic probe vs generated test vs
coverage template), whether a deep scan actually finished end-to-end, duration,
tests run, coverage, the LLM cost, and — when a ground-truth file for the target
is supplied — recall against the documented bugs and the false-positive rate
(target < 15%, per strategy/REALITY_CHECK.md).

Records are written per run and appended to a ledger so behaviour can be tracked
over time and across repos. Pure logic + file IO; never raises into the scan.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _text_of(finding: dict) -> str:
    """The searchable text of a confirmed finding (title + evidence + target)."""
    return " ".join(str(finding.get(k, "")) for k in ("title", "evidence", "target")).lower()


def score_run(result, *, repo: str, url: str, mode: str, duration_sec: float,
              usage: dict | None = None, truth: dict | None = None) -> dict:
    """
    Build a scorecard record from an OrchestratorResult and run metadata.

    Args:
        result: The OrchestratorResult (uses .confirmed/.coverage/.tests_run/…).
        repo, url, mode: Run identity ("quick"/"deep").
        duration_sec: Wall-clock seconds the scan took.
        usage: usage.snapshot() dict, or None if cost wasn't instrumented.
        truth: Optional {"expected": [{"id", "match": [kw, …]}, …]} ground truth.
            A finding matches an entry when EVERY keyword is a substring of its
            text. Recall = entries matched / documented; FP = findings matching
            no entry.

    Returns:
        dict: The full scorecard record (never raises).
    """
    confirmed = list(getattr(result, "confirmed", []) or [])
    by_layer: dict = {}
    for b in confirmed:
        src = str(b.get("source", "")) or "unknown"
        by_layer[src] = by_layer.get(src, 0) + 1

    # Split confirmed CLAIMS (P1/P2) from low-confidence LEADS (P3/P4). Recall and
    # false-positive % are computed over claims only — a P3 is explicitly hedged
    # ("not confirmed"), so counting it as a false positive would distort the metric.
    claims = [b for b in confirmed if str(b.get("severity", "")).upper() in ("P1", "P2")]
    leads = [b for b in confirmed if str(b.get("severity", "")).upper() not in ("P1", "P2")]

    rec: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": repo,
        "url": url,
        "mode": mode,
        "duration_sec": round(float(duration_sec), 1),
        "tests_run": getattr(result, "tests_run", 0),
        "rounds": getattr(result, "rounds", 0),
        "suspects": getattr(result, "suspects", 0),
        "confirmed_count": len(confirmed),
        "claims_count": len(claims),
        "leads_count": len(leads),
        "by_layer": by_layer,
        "coverage": dict(getattr(result, "coverage", {}) or {}),
        "completed_end_to_end": bool(getattr(result, "success", False)),
        "confirmed": [{"title": b.get("title"), "severity": b.get("severity"),
                       "source": b.get("source")} for b in confirmed],
    }

    if usage is not None:
        rec["cost"] = {
            "estimated_usd": usage.get("estimated_cost_usd"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "llm_calls": usage.get("calls", 0),
            "tracked": usage.get("calls", 0) > 0,
        }

    if truth and truth.get("expected"):
        expected = truth["expected"]
        matched: set = set()
        tps = fps = 0
        for f in claims:  # recall + FP over confirmed claims only, not P3 leads
            text = _text_of(f)
            hit = None
            for i, ent in enumerate(expected):
                kws = [str(x).lower() for x in ent.get("match", [])]
                if kws and all(kw in text for kw in kws):
                    hit = i
                    break
            if hit is None:
                fps += 1
            else:
                tps += 1
                matched.add(hit)
        total = len(claims)
        rec["accuracy"] = {
            "documented": len(expected),
            "caught": len(matched),
            "recall_pct": round(100 * len(matched) / len(expected), 1) if expected else None,
            "true_positives": tps,
            "false_positives": fps,
            "false_positive_pct": round(100 * fps / total, 1) if total else 0.0,
            "fp_target_pct": 15,
            "leads": len(leads),
            "missed": [expected[i].get("id") for i in range(len(expected)) if i not in matched],
        }

    return rec


def write_run(rec: dict, out_dir: str = "output/scorecard") -> str | None:
    """Write the record as its own JSON and append it to the ledger. Returns the path."""
    try:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        ts = str(rec.get("timestamp", "")).replace(":", "").replace("-", "")
        name = f"{ts}_{Path(rec.get('repo', 'repo')).name}_{rec.get('mode', 'scan')}.json"
        path = d / name
        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        with (d / "ledger.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return str(path)
    except Exception as exc:
        logger.warning("scorecard write failed: %s", exc)
        return None


def render(rec: dict) -> str:
    """A compact human summary of a scorecard record for the scan output."""
    name = Path(rec.get("repo", "repo")).name
    claims = rec.get("claims_count", rec.get("confirmed_count", 0))
    leads = rec.get("leads_count", 0)
    lines = [f"  📊 Scorecard — {rec.get('mode')} scan · {name}",
             f"     confirmed (P1/P2): {claims}  ·  leads (P3): {leads}  ·  "
             f"tests: {rec.get('tests_run', 0)}  ·  {rec.get('duration_sec', 0)}s  ·  "
             f"end-to-end: {'yes' if rec.get('completed_end_to_end') else 'no'}"]
    if rec.get("by_layer"):
        lines.append("     by layer: " + ", ".join(f"{k}:{v}" for k, v in rec["by_layer"].items()))
    c = rec.get("cost")
    if c:
        if c.get("tracked"):
            lines.append(f"     cost: ~${c.get('estimated_usd')}  ({c.get('llm_calls')} LLM calls, "
                         f"{c.get('input_tokens')}+{c.get('output_tokens')} tok)")
        else:
            lines.append("     cost: not tracked (no LLM calls recorded this run)")
    a = rec.get("accuracy")
    if a:
        lead_note = f"  ·  {a.get('leads')} low-confidence lead(s)" if a.get("leads") else ""
        lines.append(f"     accuracy: caught {a.get('caught')}/{a.get('documented')} documented "
                     f"(recall {a.get('recall_pct')}%)  ·  FP {a.get('false_positive_pct')}% "
                     f"(target <{a.get('fp_target_pct')}%){lead_note}")
        missed = [m for m in (a.get("missed") or []) if m]
        if missed:
            lines.append(f"     missed: {', '.join(missed)}")
    return "\n".join(lines)
