# QAX — Project Context

## What this project is
QAX is the runtime proof layer: "Generate the tests. Prove the bugs. Ship to Jira."
The core (the wedge) scans a RUNNING app with deterministic probes and reports only what it can
prove; generating test cases from code/stories (→ Jira/Xray) is the on-ramp.
Multi-project: works on any codebase.
Formerly: example-qa-agent → QAFlow Agent → QAX

## Current Status (2026-10-16)
The plan is `strategy/PLAN_TO_V1.md` (decisions D1–D11, phases A–E, dates); the task board is
`strategy/ROADMAP.md` (current phase, order, what waits on the owner). `strategy/README.md` indexes the rest.
History of the earlier prompts/phases: `strategy/archive/ROADMAP_HISTORY_2026-10.md`.
Now: Phase A (the truth on real apps), target 2026-11-01.

## File Structure
core/
  claude_client.py        ✅
  code_reader.py          ✅
  prompt_engine.py        ✅
  validator.py            ✅
  xray_client.py          ✅
  screenshot_capture.py   ✅
  feedback_store.py       ✅
  git_analyzer.py         ✅
  bug_fetcher.py          ✅
  session_manager.py      ✅
  prompt_optimizer.py     ✅
  markdown_reporter.py    ✅
  cache_manager.py        ✅ (Phase A)
  response_formatter.py   ✅ (Phase A)
  jira_gap_analyzer.py    ✅ (Phase B)
  risk_assessor.py        ✅ (Phase B)
  skill_library.py        ✅ (Phase B)
  smart_checker.py        ✅ (Phase B)
  pipeline.py             ✅ (Pipeline Orchestrator)
  adapters/
    __init__.py           ✅ (registry + get_adapter())
    base.py               ✅ (BaseExportAdapter)
    xray_adapter.py       ✅ (wraps XrayClient)
    zephyr_adapter.py     ✅ (Zephyr Scale REST API v2)
    excel_adapter.py      ✅ (openpyxl .xlsx export)
    memory_adapter.py     ✅ (no-op, memory only)
  input_adapters/
    __init__.py           ✅ (registry + get_input_adapter())
    base.py               ✅ (BaseInputAdapter)
    file_adapter.py       ✅ (single file from disk)
    folder_adapter.py     ✅ (walk directory, filter by ext)
    jira_adapter.py       ✅ (Jira ticket → story input)
    pr_adapter.py         ✅ (GitHub PR changed files)
    story_adapter.py      ✅ (plain text / user story)
config/
  system_prompt.md        ✅
  xray_schema.json        ✅
  projects/
    example/
      context.md          ✅
      selectors.json       ✅ (needs real selectors)
    example2/
      context.md          ✅ (empty)
    example3/
      context.md          ✅ (empty)
configs/
  smoke.json              ✅
  regression.json         ✅
  release-template.json   ✅
  bugs-template.json      ✅
execution/
  playwright_runner.py    ✅
  action_mapper.py        ✅
  test_executor.py        ✅
  claude_healer.py        ✅
  results_reporter.py     ✅
  api_runner.py           ✅ (HTTP execution mode)
dashboard/
  app.py                  ✅
  templates/index.html    ✅
ci/
  github_action.yml       ✅
  gitlab_ci.yml           ✅
main.py                   ✅
requirements.txt          ✅
.env                      ✅

## Key Rules
1. Never rebuild existing files unless asked
2. Add to existing code, not replace
3. DRY_RUN=true always until told otherwise
4. Every function needs docstrings
5. Never crash — handle all errors
6. --project flag default = read from .env
7. All logs say "QAX" only
8. QAX is GENERIC — no hardcoded project names
9. --auto-accept only for confidence >= 8.5
10. Cache enabled by default, --no-cache to skip
11. Pipeline is additive only — never break existing commands
12. Each adapter handles its own credential validation
13. Execution is never blocked by export failure
14. Input adapters never raise — always return []
15. API runner maps automation_steps playwright_action to HTTP methods
16. --exec-mode none skips execution entirely

## Config
cloudId: YOUR-JIRA-CLOUD-ID

## Tech Stack
Python 3.11+, anthropic SDK,
requests, playwright, sqlite3, pytest, flask
