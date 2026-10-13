PY := ./.venv-exec/bin/python

.PHONY: eval test gate

## Run the full test suite.
test:
	$(PY) -m pytest -q

## Merge gate: boot the golden target, deterministic quick scan + scorecard vs truth,
## secure control, and the orchestration A/B harness. Non-zero exit blocks merge.
gate eval:
	$(PY) -m evals.gate
