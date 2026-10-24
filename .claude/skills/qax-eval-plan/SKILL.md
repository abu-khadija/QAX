---
name: qax-eval-plan
description: Write down an eval's bars, stop rules and spend cap BEFORE running anything that costs money, then run a tiny pilot to learn the cost. Use before `make eval-generation` or any run with a paid AI provider.
---

# QAX eval plan (before spending)

An eval run on a paid provider spends real money and its result belongs to the model that produced it. Plan first.

## 1. Preregister (write this into the reply or a file, before running)
- **Question:** the single claim being tested (e.g. "generation meets the bar on a broken app").
- **Provider and model:** exact names. A result belongs to its model; never mix.
- **Bars:** the pass numbers written now (coverage, planted bug found, false failures, errors), taken from `evals/generation_quality.py`.
- **n:** stories x runs. Say plainly when n is too small to claim anything.
- **Stop rules:** stop on the first provider error that repeats, on a 429 daily cap, or when the spend cap is reached.
- **Spend cap:** set `QAX_AI_MAX_CALLS` to a number computed from the pilot below. Never run uncapped.

## 2. Dry run at zero cost
Check the provider is ready without a paid call: the key exists (names and lengths only, never print it), the model name is set, `.env` is
read, and the harness reports its `ai` label. Run the offline tests for the harness.

## 3. Pilot of three items
Run ONE story once with `QAX_AI_MAX_CALLS` set small. Read `services.usage.snapshot()`: calls, tokens and the estimate (a non-Claude provider is
counted but not priced, so say "not priced"). Multiply by the full plan. If the projected spend or call count is more than the person approved, stop and ask.

## 4. Run sequentially
One AI job at a time (a free tier has a small daily cap per model and parallel jobs cause 429s). Record every result with its model.

## 5. Report
State the bars, the numbers, n, the model and what is NOT claimed. Update `docs/HANDOVER.md` and the measure page only with what was measured.
