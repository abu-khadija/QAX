"""
test_usage_other_provider.py — a non-Claude provider is counted, never priced
=============================================================================
The first deep scan on a free provider printed "cost: ~$0.1322": its 14502 + 5911 tokens priced at Sonnet's rates.
`note()` already treated "other:" models as unpriced, but the estimate behind the scorecard did not.
"""
from services import scorecard, usage


def _fresh():
    usage.reset()


def test_other_provider_tokens_are_counted_but_cost_nothing_in_the_estimate():
    _fresh()
    usage.note(14502, 5911, "other:gemini-3-flash-preview")
    s = usage.snapshot()
    assert s["calls"] == 1 and s["input_tokens"] == 14502 and s["output_tokens"] == 5911
    assert s["estimated_cost_usd"] == 0.0 and s["unpriced_calls"] == 1


def test_claude_calls_are_still_priced_and_a_mix_prices_only_the_claude_part():
    _fresh()
    usage.note(1_000_000, 0, "claude-sonnet-4-5")
    usage.note(500_000, 500_000, "other:some-model")
    s = usage.snapshot()
    assert s["estimated_cost_usd"] == 3.0 and s["unpriced_calls"] == 1 and s["calls"] == 2


def _rec(**cost):
    return {"mode": "deep", "repo": "r", "tests_run": 1, "duration_sec": 1, "completed_end_to_end": True,
            "cost": dict({"tracked": True, "input_tokens": 100, "output_tokens": 50}, **cost), "by_layer": {}}


def test_the_scorecard_says_not_priced_instead_of_inventing_a_figure():
    out = scorecard.render(_rec(estimated_usd=0.0, llm_calls=4, unpriced_calls=4))
    assert "cost: not priced" in out and "4 LLM calls to a non-Claude provider" in out and "$" not in out.split("cost:")[1].splitlines()[0]


def test_a_mixed_run_shows_the_claude_figure_and_the_unpriced_calls():
    out = scorecard.render(_rec(estimated_usd=0.5, llm_calls=5, unpriced_calls=2))
    assert "~$0.5 + 2 call(s) to a non-Claude provider, not priced" in out


def test_an_all_claude_run_reads_as_before():
    out = scorecard.render(_rec(estimated_usd=0.5, llm_calls=3, unpriced_calls=0))
    assert "cost: ~$0.5  (3 LLM calls, 100+50 tok)" in out
