---
name: qax-security
description: Audit the QAX repository itself for security problems (secrets, dependencies, CI permissions, what AI output can reach). Use before a release, a push of CI changes, or when a new integration is added.
---

# QAX security audit (of QAX, not of a target app)

Read-only. Never print a secret: report names, files and counts. Never call a live API to test a key. Findings need evidence (`file:line` or a command and its output); a guess is a lead, not a finding.

## 1. Secrets
- `git ls-files` for `.env`, keys, `.bench.env`. `.env` must be ignored (`git check-ignore -v .env`).
- Key-shaped strings in tracked files and in history (`git grep -lE` and `git log --all -G`), counts only. Prefixes such as `AKIA`, `ghp_`, `sk-ant-`, `AIza`, and a private-key header.
- A removed secret is still exposed: recommend rotation, never history rewriting from this audit.

## 2. Dependencies
- `requirements.txt` and `pyproject.toml` bounded below AND above (next major); Playwright pinned exactly. Note any unpinned transitive risk as a lead.
- Never run an install to "audit". Use `pip install --dry-run` or read the files.

## 3. CI/CD (ci/*.yml and .github/workflows/*.yml, which must stay identical where a test says so)
- `permissions:` is minimal; no `pull_request_target` that checks out PR code.
- No `${{ github.event.* }}` or other untrusted value interpolated into a `run:` shell; pass it through `env:`. Repository `vars` and `secrets` are admin-controlled (lower risk).
- Secrets only in `env:` of the step that needs them; a fork's PR gets a read-only token, so nothing depends on a secret there.
- Actions on Node 24 majors (`tests/test_ci_workflows.py` guards this).

## 4. What AI output can reach
- AI-written cases use a closed action set; navigation passes `core/origin_guard`; a healed selector is saved by value only after it worked.
- AI calls respect `QAX_AI_MAX_CALLS`; keys are never logged (`core.public_error.clean` on error text); the AI endpoint never follows a redirect.

## 5. The scanner's own boundary
- Probes stay on the target's origin; read-only by default; the preflight authorization check stands; Jira/Xray stay behind `DRY_RUN`.
- Hosted-grade protections are NOT present (SSRF/DNS rebinding, private ranges, tenancy): say so, do not imply they exist.

## Report
Per finding: severity, `file:line`, evidence, fix. End with what was NOT checked. Fix what is cheap in the same session (with a test), commit locally, never push without being asked.
