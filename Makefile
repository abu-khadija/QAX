PY := ./.venv-exec/bin/python

.PHONY: eval eval-deep eval-mutants test gate

## Run the full test suite.
test:
	$(PY) -m pytest -q

## Merge gate (FREE, deterministic): boot the golden target, quick scan + scorecard
## vs truth, secure control, and the orchestration A/B harness. Non-zero exit blocks merge.
gate eval:
	$(PY) -m evals.gate

## Merge gate + credit-gated DEEP lane: adds a budgeted deep scan (real $/run) with an
## official deep scorecard and the deep-feature engagement checks (items 3/5/6).
eval-deep:
	$(PY) -m evals.gate --deep

## Coverage map of the gate itself: kill each probe in turn, see which ones the golden
## target would NOT notice dying (zero-LLM, ~7 min).
eval-mutants:
	$(PY) -m evals.mutants
