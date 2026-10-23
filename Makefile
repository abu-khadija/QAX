PY := ./.venv-exec/bin/python

.PHONY: eval eval-deep eval-mutants eval-functional-mutants eval-release eval-repro audit-deps eval-generation bench-functional test gate install web doctor docker

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

## Break the functional engine on purpose (13 mutants): does the functional benchmark notice? (13/13 on 2026-10-21)
eval-functional-mutants:
	$(PY) -m evals.functional_mutants --app ui-demo,ui-demo-depth --min-killed 13

## One-command install: a Python 3.11/3.12 venv in .venv-exec, the packages, Chromium.
install:
	@command -v python3.12 >/dev/null 2>&1 && PYBIN=python3.12 || PYBIN=python3; \
	  $$PYBIN -m venv .venv-exec && $(PY) -m pip install -q --upgrade pip && \
	  $(PY) -m pip install -q -r requirements.txt && $(PY) -m playwright install chromium && \
	  ([ -f .env ] || cp .env.example .env) && $(PY) main.py doctor

## The web UI on http://localhost:8900
web:
	$(PY) -m web.server

## Is this machine ready? (Python, packages, browser, git, integrations — never prints secrets)
doctor:
	$(PY) main.py doctor

## The web UI in a container (Chromium included); published on this machine's loopback only.
docker:
	docker compose up --build

## Quality of the test cases the AI writes (an AI provider: ANTHROPIC_API_KEY with credit, or QAX_AI_PROVIDER=openai
## + QAX_AI_MODEL for a free/local one; one generation per story; the result names the model)
eval-generation:
	$(PY) -m evals.generation_quality

## Functional QA benchmark: verdicts scored against truth written before the run (repeat 2 = repeatability)
bench-functional:
	$(PY) -m evals.functional_bench --app all --repeat 2

## Known vulnerabilities in the installed Python packages and requirements.txt (needs network; pip-audit from the dev extra)
audit-deps:
	$(PY) -m pip_audit --progress-spinner off
	$(PY) -m pip_audit --progress-spinner off -r requirements.txt

## Release check decisions on seeded PetClinic changes: control → GO, each regression → NO-GO (4/4 on 2026-10-21; needs Java + corpus)
eval-release:
	$(PY) -m evals.release_bench

## Bug reproduction from tickets: real upstream linkding bugs on old/fixed versions + demo (6/6 on 2026-10-21)
eval-repro:
	$(PY) -m evals.repro_bench
