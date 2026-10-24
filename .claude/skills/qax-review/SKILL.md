---
name: qax-review
description: Review the current QAX changes before they are committed, against QAX's own rules. Use before any commit, or when asked to review the diff.
---

# QAX pre-commit review

Review `git diff` (staged, unstaged and new files) plus `git diff <base>..HEAD` when asked. Cite `file:line`. Report only real
problems, one line for the problem and one for the fix. No praise, no summary. If nothing is wrong, say "No issues found."

Method adapted from the open checklist idea of reading the diff in two passes; the checks below are QAX's own.

## Pass 1: critical (read first)
1. **Writes to Jira/Xray or any outside service.** Anything that creates, edits or deletes there must sit behind `DRY_RUN` and an explicit
   flag. `DRY_RUN` must stay `true` by default.
2. **Shell injection.** `shell=True`, `os.system`, `eval`, `exec`, or a command built from a string that includes AI output, a ticket, a
   URL or a file name. (Today only `evals/*bench.py` use `shell=True`, on commands from local manifest files.)
3. **AI output trusted without a check.** Anything an AI wrote (a case, a selector, a URL, a healed selector) must be validated against a
   closed set before it runs or is saved. Navigation stays behind `core/origin_guard`.
4. **Uncapped spend.** A new AI call in a loop, a retry, or a new caller of `ai_provider`/`claude_client` must go through the usage
   meter and respect `QAX_AI_MAX_CALLS`.
5. **Secrets.** No key, token or `.env` content in code, tests, logs, docs or error text. Never commit `.bench.env`.
6. **A guard weakened.** A test removed, skipped, loosened (a bound raised, `except` widened, an assertion deleted) without a measurement or
   decision visible in the diff is a finding, not a cleanup.

## Pass 2: QAX rules (CLAUDE.md "Key Rules")
- Every function has a docstring. Nothing crashes: errors are handled and say why. Logs say "QAX" only. No hardcoded project names.
- Input adapters never raise (return `[]`). Execution is never blocked by an export failure. `--auto-accept` only at confidence >= 8.5.
- Additive only: an existing command's behaviour does not change.
- Claims: any number in code, docs or a page names its n, its model/provider and how to re-run it. "Did not run" is never a pass.
- A new variable or flag appears in `.env.example` or the CLI help, and in the matching doc.
- A test that depends on wall-clock time asserts the outcome instead (or uses an injected clock).
- Tests set the environment with `monkeypatch` (never a bare `os.environ[...] =`), and helper names are unique per file.

## Before you finish
Run the suite with `./.venv-exec/bin/python -m pytest -q` and read the exit code (never decide on `| tail`). It takes about 10 minutes;
run it in the background. Commit locally only; never push without being asked.
