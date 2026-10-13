# QAX Roadmap — the plan we walk on

Re-sequenced 2026-10-13 with the owner. Near-term goal chosen: **product/technical
hardening first** (lock quality before expanding). IP/legal track: **deferred**.

One mental model: **one platform, two capabilities** — *Scan & prove bugs* (the
wedge) and *Generate test cases* (the on-ramp), both filing to the team's tracker.
Buyers in order: owners → QA → dev. The deterministic probe layer is the trust
guarantee (0 false positives); the LLM layer adds recall but is non-deterministic,
so the Orchestrator stays deterministic around it.

**Discipline (non-negotiable):** every change is measured before it merges — the
golden target (below) + `evals/orchestrator_ab.py` must show it improved something
without breaking anything. No number, no merge.

---

## Where we are (2026-10-13)

- Scanner: ~22 deterministic probes, 0-FP, validated on 3 external stacks + a seeded
  target. All **7 orchestrator-weakness fixes done + measured** (3→5 live, evidence in
  `strategy/ORCHESTRATOR_FIXES_EVIDENCE.md`). Suite 321 passed, 3 skipped.
- Test generation: works (code/story/jira/pr → cases → Xray/Zephyr/Excel). On-ramp.
- Filing to Jira/Xray: built, DRY_RUN default, waiting on real credentials.
- UI: one-platform restructure done. CI PR-scan gate built. Pitch deck exists.
- **PR #1 open** (abu-khadija/QAX#1) — needs review/merge.

---

## Phase 1 — HARDEN (now, the chosen focus)

Goal: make quality provable and repeatable before adding surface.

- [ ] **Merge PR #1** — land the service layer + the 7 fixes.
- [x] **Golden regression target** (done) — `evals/targets/vuln_jwt_app.py` + truth
      `evals/truth/golden-jwt.json`. A permanent, deliberately-vulnerable fixture
      (JWT signature not verified) the gate scans on every change.
- [x] **One-command eval** (done) — `make eval` (`python -m evals.gate`): boots the
      golden target, runs a deterministic quick scan, scores recall/FP vs truth,
      runs the SECURE control (flaw fixed → probes silent, 0 FP), and the A/B
      harness. Non-zero exit blocks merge. Effectively zero-LLM (empty repo → live
      grounding). Current: GATE PASS (recall 100%, FP 0).
- [x] **Deep lane via scorecard** (done) — `make eval-deep` (`python -m evals.gate
      --deep`, credit-gated): budgeted deep scan on the golden target with an official
      deep scorecard (last run: recall 100% 3/3, FP 0%, ~$0.13, 7 LLM calls,
      end-to-end yes) and deep-feature engagement checks. Item 6 (forged-token-replay
      in deep) is hard-gated; items 3 (setup repair) + 5 (fallback) engage
      deterministically and are hard-gated on ENGAGEMENT, with their LLM-dependent
      yield reported (item 5 produced 0 functional tests from the bare toy spec this
      run — see tuning note below). Also fixed: generation no longer crashes on a
      missing project context (empty project → empty context, QAX is GENERIC).
      KNOWN TUNING ITEM: the no-router fallback engages but the per-endpoint
      functional generation yields few/zero valid tests from a bare endpoint spec —
      improve `_endpoints_as_spec` (examples/structure) so the LLM produces concrete
      api_steps. Folded into "generation polish" below.
- [x] **Verify the original pipeline's `execute` leg end-to-end** (done) — the gate
      now runs `QAXPipeline(exec_mode="api").run(test_cases=...)` against the golden
      target and asserts it distinguishes pass from fail (total=2, passed=1,
      failed=1), deterministically, every run. Note: the pipeline targets the host
      via `APP_BASE_URL` (no explicit base_url param) — a small ergonomics gap, not a
      correctness one.
- [x] **Generation polish — API-mode validation bug fixed** (done, high-impact):
      the validator required Playwright UI steps (`manual_steps`+`automation_steps`,
      ≥2 each) and a non-empty `preconditions`, so EVERY API-mode test case
      (`api_steps`) was silently rejected — the whole functional-generation leg
      (router + the no-router fallback) yielded ~0 valid API tests. Fixes: an
      API-mode schema (`validator.api_schema`) that requires `api_steps` and drops
      the UI-step mandate; `fix_common_issues` defaults empty/short `preconditions`.
      After: generation yields 13–14 valid cases reliably (was 0); the deep-gate
      fallback now produces tests (`groups=['auth']`). +4 validator tests.
- [ ] **Generation polish — slowness** (remaining, lower priority, only if it hurts
      demo quality): fewer cases / streaming / raise max_tokens.

## Phase 2 — EVIDENCE + JIRA (after hardening)

- [ ] **Real Jira filing E2E** when the owner's trial creds arrive — bug→Jira (Bug),
      test→Xray; set `.env` JIRA_EMAIL/JIRA_API_TOKEN/JIRA_BASE_URL/JIRA_CLOUD_ID,
      lift DRY_RUN on an opt-in flag only.
- [ ] **1–2 new OSS case studies** — vet first (boots cheaply + serves a spec, per
      the vet checklist), quick scan broadly, one budgeted deep scan.
- [ ] **Simplify "File to Jira/Xray" naming + add a tool picker** (owner-deferred UX).
- [ ] **KB → probes** — pull the top-ranked probe candidates from the knowledge base.

## Phase 3 — PACKAGING / PITCH (later, when expanding)

- [ ] **SaaS tiering** — the coverage budget (`max_endpoints`/`max_router_files`) as
      the free-vs-paid lever.
- [ ] **Competition slide** (only when actively pitching).
- [ ] Agent-team vision (Product-Owner/Acceptance agent) — Tier 3, far off.

## Deferred track — IP / legal

Time-sensitive because the owner is leaving the company, but **deferred by choice
now**. Revisit before any external distribution or fundraising. See the IP status
memory.

---

## New this session, folded into the plan
- The seeded vulnerable Flask target + `scan_dump.py` harness → becomes the Phase-1
  golden target.
- The A/B harness + scorecard → become the automated merge gate.
- Before/after evidence captured in `strategy/ORCHESTRATOR_FIXES_EVIDENCE.md`.
