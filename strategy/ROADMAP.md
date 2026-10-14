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
- **PR #1 merged to main** (2026-10-13, merge commit 95322b2).

---

## ACTIVE WORKLIST — blind-spot backlog, ranked by risk

The owner's call (2026-10-13): QAX already beats the competition on some axes
(prove-not-flag, 0-FP, deterministic, generate+file in one tool) — now close every
axis where it is behind. These are the honest blind spots from the standing
assessment, ordered highest-risk-first. **March through them one at a time, top to
bottom; a task is done only when its "done when" holds and the gate stays green.**
Overlapping Phase-2/3 items are absorbed here.

- [x] **B1 — LLM non-determinism visible to the buyer (DONE).** The output now
      splits by TRUST, not severity: `OrchestratorResult.confirmed_deterministic`
      (probes + self-proving templates, canonical-ordered, byte-stable) vs
      `.leads_ai` (Investigator/tour, may vary). Labelled everywhere — scorecard
      (`deterministic_count`/`ai_leads_count` + render line), web UI (CONFIRMED ·
      deterministic / Leads · AI-hygiene, footer reworded), and `summary()`. Gate
      hard-check: two quick scans of the golden target yield an identical CONFIRMED
      block (passing: 5 confirmed, identical=True). +2 unit tests. Done.

- [x] **B2 — silent pipeline gaps (DONE).** The scan now records a per-stage output
      count (`OrchestratorResult.legs`: endpoints_grounded, suspects, probes_confirmed,
      functional_generated, templates_injected, tests_executed, deterministic_confirmed)
      and `silent_legs()` fires a contract when a stage RAN but produced nothing
      (functional generation attempted → 0 tests; tests built → 0 executed) — exactly
      the shape of the 8-month API-validation bug. The orchestrator emits loud
      `SILENT-GAP:` warnings, and the gate hard-checks "no silent legs" on both the
      quick (templates built → executed) and deep (functional_generated=13) lanes.
      +3 unit tests. Done.

- [~] **B3 — coverage breadth vs DAST (IN PROGRESS, a continuing track).** Add the
      missing high-value classes deterministically, 0-FP, one per step, each seeded in
      the golden target + a truth entry. QAX now has 28 probes.
      - [x] **BFLA — Broken Function-Level Authorization (OWASP API5).** New
        `bfla_probe`: an ordinary freshly-registered user reaching a privileged,
        admin-namespaced GET that rejects a no-token request = privilege escalation
        (P1). Converts what was a flaky LLM lead ("non-admin reaches /admin") into a
        deterministic finding. Seeded in the golden target (SECURE mode enforces the
        role → quiet); truth entry added; gate recall now 4/4, secure control watches
        bfla. +3 unit tests.
      - [ ] NEXT candidates (ranked): SSTI, unrestricted file upload, host-header /
        cache poisoning, XXE, GraphQL introspection/batching. One per step.

- [ ] **B4 — no evidence on messy, real, non-cherry-picked apps.** Risk: all proof
      is on small/known-vulnerable or seeded targets. Task: run QAX on 2–3 large,
      real OSS apps we did NOT pick for being vulnerable; publish honest scorecards
      (recall/FP/$/run) even when the result is "0 found, 0 FP". Done when:
      `strategy/` holds ≥2 real-app case studies with scorecards. (Absorbs Phase-2
      case studies.)

- [ ] **B5 — targets without an OpenAPI spec degrade.** Risk: many real apps have no
      spec; grounding falls back to partial code-inference. Task: a no-spec
      endpoint-discovery fallback (crawl served links / probe common REST patterns /
      verify each code-derived path is live before probing). Done when: a spec-less
      target on the gate is still enumerated and probed.

- [ ] **B6 — Jira/Xray filing never proven live.** Risk: "files to Jira" is DRY_RUN
      only. Task: with the owner's trial creds, file a real Bug + a real Xray Test
      end-to-end, then restore DRY_RUN. Done when: one real issue + one real test
      exist in a trial project, with screenshots in `strategy/`. (Absorbs Phase-2
      Jira.)

- [ ] **B7 — business-logic bugs unproven.** Risk: the hardest, highest-value class;
      QAX only proves OWASP/injection/auth today. Task: pick one concrete logic-bug
      pattern (e.g. negative-quantity / price-tamper / workflow-skip), build a
      deterministic probe + a seeded fixture, and prove it. Done when: QAX confirms a
      seeded business-logic bug, 0 FP, and it is in the gate.

- [ ] **B8 — thin moat (strategic, not a code task).** Risk: the deterministic
      probes are replicable by a funded incumbent. Task (GTM): lean the whole
      message on the one thing they structurally won't copy fast — "**proven, zero
      false positives**" — and build the brand/speed around it; revisit when
      pitching. Done when: positioning + deck lead with proof-not-noise and the
      case studies (B4) back it.

- Deferred (owner's choice, still existential): **B9 — IP / legal / solo-founder
      risk.** See the deferred track below and the IP status memory.

---

## SELF-HARDENING track (S) — from the 2026-10-14 implementation audit

The audit (verified against source — every checked claim was accurate) exposed a
whole category the B-backlog missed: **is QAX itself safe?** A security product that
is itself insecure is an existential credibility risk — we audit others but hadn't
dogfooded ourselves. These are real, mostly deployment/safety items (no P0). The B1
(deterministic/AI split) and B2 (stage diagnostics) work was independently confirmed
present by the audit.

**Merged execution order (what to march through):** S1 → S2 → S7 → S5 → S6 → S4 →
S3 → S8, then resume the B-track (more B3 probe classes, then B4…). S1 comes before
B6 (real Jira filing) so an accidental live push is impossible.

- [x] **S1 — DRY_RUN safety (DONE; gated B6).** `XrayClient(dry_run=...)` is now
      request-scoped — the explicit flag wins, else env with a SAFE default of **true**
      (was false). Both `core/adapters/xray_adapter.py` and `web/server.py`'s findings
      export now pass `dry_run` straight to the client; the process-global
      `os.environ["DRY_RUN"]` mutation (a race under threaded Flask) is deleted
      everywhere. No path can push live unless dry_run is explicitly false. +2 unit
      tests (explicit flag wins; safe default when unset).
- [x] **S2 — scanner egress / token boundary (DONE).** `APIRunner._is_approved_origin`
      enforces that every step hits the same host+port as the target `base_url`; a step
      carrying an absolute OFF-origin URL is refused BEFORE any request is made, so the
      saved bearer token can never leak off-origin. No base_url → absolute URLs refused,
      relative allowed. +3 unit tests (origin rules; off-origin blocked with no request
      and no token sent). Same-origin scans unaffected (gate recall 4/4). Follow-up
      (deferred): request count / payload-size caps.
- [ ] **S3 — web API auth/authz + export policy.** `/api/scan|generate|export` have no
      auth; export honours caller-supplied `dry_run:false` with server creds. Gates
      any non-loopback exposure (private beta). Done when: routes require auth and
      export cannot be forced live by the caller.
- [ ] **S4 — authorized-target gate + active-probe safety.** No ownership/consent gate
      before active (mutating) probing. Done when: a scan requires explicit
      target-authorization, destructive probes are labelled, and cleanup is proven.
- [x] **S5 — packaging/install reproducible (DONE).** Declared streamlit/plotly/pandas
      in requirements.txt (labelled Web-UI section) + `tests/test_packaging.py`: a
      manifest-contract test that fails if the UI imports a third-party module not
      declared, so the gap can't recur.
- [x] **S6 — replayable evidence artifact (DONE).** `OrchestratorResult.evidence`
      now carries a REDACTED replay trail — the executed test definitions, the
      per-step execution results (method/path/status/response/error), and the proven
      probe findings — serialized in to_dict. `redact_secrets()` masks secret-keyed
      values and any Bearer token / JWT so the saved artifact never persists live
      credentials. +3 redaction unit tests. (Feeds B4 — case studies now have a
      replayable trail.)
- [x] **S7 — legacy "assert noted → PASS" (DONE).** `api_runner.execute_step`'s legacy
      UI-shaped `assert`/`verify`/`check`/`expect` now returns SKIP ("Assertion NOT
      evaluated in API mode — use api_steps"), never PASS, so an un-checked step can't
      read as verified coverage. Real assertions remain in api_steps. +1 unit test.
- [ ] **S8 — UI output escaping + module splits.** `ui/app.py` renders subprocess
      output via `unsafe_allow_html`; `main.py`/`ui/app.py` are oversized. Done when:
      UI output is escaped and the biggest files are split behind stable interfaces.

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
- [~] **Generation polish — slowness** (investigated; deferred, not worth the
      quality risk now): measured ~80s for one functional generation (13 cases).
      Root cause is OUTPUT SIZE, not the model — each case carries three step arrays
      (manual_steps + automation_steps + api_steps), ~tripling tokens for API tests
      that only need api_steps. Switching to the light model (Haiku) cut time to 57s
      but produced 0 valid cases (too weak for the schema) — reverted. The right fix
      is to make API-mode generation emit api_steps ONLY (skip the UI dual-steps),
      which needs a prompt change with a quality check — scoped follow-up. Added
      harmless plumbing: `GenerationOptions.model` → `analyze_code(model_override=)`.

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
