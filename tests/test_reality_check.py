"""
test_reality_check.py — the Reality Check dashboard aggregation
===============================================================
Pure logic over scorecard records; no scan, no network.

Run with:
    pytest tests/test_reality_check.py -v
"""

from evals import reality_check as rc


def _rec(repo, mode="quick", recall=None, fp=None, cost=None, e2e=True, bugs=0):
    r = {"repo": repo, "mode": mode, "confirmed_count": bugs,
         "completed_end_to_end": e2e}
    if recall is not None or fp is not None:
        r["accuracy"] = {"recall_pct": recall, "false_positive_pct": fp}
    if cost is not None:
        r["cost"] = {"tracked": True, "estimated_usd": cost}
    return r


def test_load_ledger_skips_bad_lines(tmp_path):
    p = tmp_path / "ledger.jsonl"
    p.write_text('{"repo":"a"}\n\nnot-json\n{"repo":"b"}\n', encoding="utf-8")
    recs = rc.load_ledger(str(p))
    assert [r["repo"] for r in recs] == ["a", "b"]


def test_load_ledger_missing_file_is_empty():
    assert rc.load_ledger("/no/such/ledger.jsonl") == []


def test_latest_per_repo_keeps_newest():
    recs = [_rec("/x/A", bugs=1), _rec("/x/A", bugs=3), _rec("/x/B", bugs=2)]
    latest = rc.latest_per_repo(recs)
    by_repo = {r["repo"]: r["confirmed_count"] for r in latest}
    assert by_repo == {"/x/A": 3, "/x/B": 2}


def test_summarize_averages_only_runs_with_metrics():
    recs = [
        _rec("/x/A", recall=80.0, fp=0.0, cost=0.02),
        _rec("/x/B", recall=60.0, fp=20.0, cost=0.50),
        _rec("/x/C"),  # no truth, no cost
    ]
    s = rc.summarize(recs)
    assert s["runs"] == 3
    assert s["avg_recall_pct"] == 70.0
    assert s["avg_false_positive_pct"] == 10.0
    assert s["fp_within_target"] is False  # B is 20% > 15%
    assert s["total_cost_usd"] == 0.52


def test_summarize_deep_completion_rate():
    recs = [_rec("/x/A", mode="deep", e2e=True), _rec("/x/B", mode="deep", e2e=False),
            _rec("/x/C", mode="quick", e2e=True)]
    s = rc.summarize(recs)
    assert s["deep_runs"] == 2 and s["deep_completed"] == 1


def test_render_handles_empty_and_populated():
    assert "no scorecard records" in rc.render_table([])
    text = rc.render_table([_rec("/x/VAmPI", recall=80.0, fp=0.0, cost=0.02, bugs=4)])
    assert "VAmPI" in text and "80.0%" in text
