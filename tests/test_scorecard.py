"""
test_scorecard.py — the honest run metrics (scorecard + usage)
==============================================================
No network: scorecard is pure logic over an OrchestratorResult, and usage is
tested via its public note/reset API (the SDK wrapper is exercised separately
only for idempotence).

Run with:
    pytest tests/test_scorecard.py -v
"""

from services import scorecard, usage
from services.results import OrchestratorResult


def _result() -> OrchestratorResult:
    return OrchestratorResult(
        confirmed=[
            {"title": "JWT forgery → full account takeover", "severity": "P1",
             "source": "probe", "evidence": "forged a token", "target": "/me"},
            {"title": "SQL injection in GET /users/v1/{username}", "severity": "P1",
             "source": "probe", "evidence": "sql error", "target": "/users/v1/{username}"},
            {"title": "Verify POST /users/v1/register ignores admin field", "severity": "P2",
             "source": "test", "evidence": "admin=True persisted on register", "target": "/register"},
        ],
        coverage={"auth-bypass": "template", "broken-authz": "generation"},
        rounds=1, tests_run=48, suspects=13,
    )


# ── scorecard: layers, end-to-end, no truth ──────────────────────────────────

def test_score_run_attributes_layers_and_completion():
    rec = scorecard.score_run(_result(), repo="/x/VAmPI", url="http://x",
                              mode="quick", duration_sec=12.3)
    assert rec["confirmed_count"] == 3
    assert rec["by_layer"] == {"probe": 2, "test": 1}
    assert rec["completed_end_to_end"] is True
    assert rec["tests_run"] == 48
    assert "accuracy" not in rec  # no truth supplied


def test_score_run_marks_failed_run_not_end_to_end():
    rec = scorecard.score_run(OrchestratorResult(error="boom"), repo="r", url="u",
                              mode="deep", duration_sec=1.0)
    assert rec["completed_end_to_end"] is False
    assert rec["confirmed_count"] == 0


# ── scorecard: recall + false-positive math against ground truth ─────────────

def test_score_run_computes_recall_and_false_positives():
    truth = {"expected": [
        {"id": "jwt-forgery", "match": ["jwt", "forgery"]},
        {"id": "sqli", "match": ["sql injection"]},
        {"id": "bola", "match": ["object-level authorization"]},  # not caught
    ]}
    rec = scorecard.score_run(_result(), repo="r", url="u", mode="quick",
                              duration_sec=5.0, truth=truth)
    a = rec["accuracy"]
    assert a["documented"] == 3 and a["caught"] == 2
    assert a["recall_pct"] == 66.7
    assert a["missed"] == ["bola"]
    # the register/admin finding matches no expected entry → one false positive
    assert a["false_positives"] == 1 and a["true_positives"] == 2
    assert a["false_positive_pct"] == 33.3


def test_cost_block_marks_untracked_when_no_calls():
    rec = scorecard.score_run(_result(), repo="r", url="u", mode="quick",
                              duration_sec=1.0, usage={"calls": 0, "estimated_cost_usd": 0.0,
                                                       "input_tokens": 0, "output_tokens": 0})
    assert rec["cost"]["tracked"] is False


def test_render_and_write(tmp_path):
    rec = scorecard.score_run(_result(), repo="/x/VAmPI", url="u", mode="quick",
                              duration_sec=1.0)
    text = scorecard.render(rec)
    assert "Scorecard" in text and "by layer" in text
    path = scorecard.write_run(rec, out_dir=str(tmp_path))
    assert path and (tmp_path / "ledger.jsonl").exists()


# ── usage accounting ─────────────────────────────────────────────────────────

def test_usage_accumulates_and_prices_per_model():
    usage.reset()
    usage.note(1_000_000, 1_000_000, "claude-haiku-4-5")  # 0.80 + 4.00
    usage.note(1_000_000, 0, "claude-sonnet-4-5")          # 3.00
    snap = usage.snapshot()
    assert snap["calls"] == 2
    assert snap["input_tokens"] == 2_000_000
    assert snap["estimated_cost_usd"] == 7.8
    usage.reset()
    assert usage.snapshot()["calls"] == 0


def test_usage_track_is_idempotent():
    assert usage.track() in (True, False)  # True normally; False only if SDK absent
    assert usage.track() in (True, False)  # second call must not raise
