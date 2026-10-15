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
- Scanner: **28 deterministic probes**; suite **348 passed**, 3 skipped; the gate (quick
  + deep) is green. Work lands directly on `main` (owner's call; harness blocks PR merges).
- **2026-10-15:** 34 probes; suite **449 passed**; gate green. B4 now **3 real apps** (+Gitea,
  the first large token-auth app — killed a 12× P1 false-positive cascade; 0 FP, 2 real
  findings). **B6 Bug half done:** SCRUM-5/6 filed live to the owner's Jira in their own
  QA bug format. Deep-scan generation fixes (G4/G5) **validated live** once credits returned (deep scan FAILs 19 → 2, generated tests 18/18 PASS, 0 AI false leads, ~$0.72/run).
- **2026-10-15 (KB mining, files 05 + 11):** 40 probes (+5 spec-typed: boolean-false, single-element-array, date-order, long-input, duplicate-rows); **not-tested ledger** (a probe that can't run now says so — "absence of a finding != a pass"); Jira Bug covers the owner's 13 fields + `--client-safe` + evidence attachment. Suite 510.

---

## MASTER WORKLIST — the single ordered sequence (S + B merged)

This is the source of truth for order; the per-item detail lives in the two sections
below (self-hardening S, blind-spot B). Rule unchanged: one item at a time, each green
+ gated before the next.

**DONE (2026-10-13 → 14):**
1. ✅ B1 — deterministic CONFIRMED vs AI-leads split (reproducibility gate)
2. ✅ B2 — per-stage output contract (silent-gap detection)
3. ✅ S1 — DRY_RUN request-scoped + safe default (no env race) — *unblocks B6*
4. ✅ S2 — scanner egress / token boundary
5. ✅ S3 — web API auth + export live-gate
6. ✅ S4 — authorized-target gate + mutating-probe labelling
7. ✅ S5 — packaging: declare UI deps + manifest-contract test
8. ✅ S6 — redacted replay-evidence artifact
9. ✅ S7 — legacy "assert → PASS" → "not evaluated"
10. ✅ S8 (security half) — escape UI subprocess output
11. ✅ B3·first class — BFLA (OWASP API5)

**REMAINING (in execution order):**
12. ▶ **B3 — more probe classes** (coverage breadth; biggest product gap vs DAST).
    Done this track: BFLA ✅, SSTI ✅, host-header ✅, XXE ✅, GraphQL ✅, file-upload ✅ (33 probes, gate 10/10).
    Planned batch complete (6 classes). Continues opportunistically
    as B4 reveals gaps.
13. ✅ **B5 — no-spec endpoint discovery** (DONE) — probes common paths on spec-less
    apps; widens reach for B4.
14. ✅ **B4 — real-app case studies** (DONE: 2 done — MIT DRF RealWorld,
    0 FP after a found+fixed cry-wolf; see strategy/CASE_STUDY_REALWORLD_DRF.md).
    DONE — 2 case studies (DRF + Go/Gin), 2 stacks, 0 FP on both.
15. ✅ **B7 — business-logic bug probe (DONE)** — negative quantity/amount accepted+stored (P2).
16. ✅ **S8 (refactor half, main.py)** — 2679→2288 lines: cli/ presentation modules
    + ONE generation path for CLI/scan/pipeline. ui/app.py STARTED (presenters extracted, 2281→2174).
17. **B8 — moat / GTM** — lead the message on "proven, zero false positives" (when pitching).
    ✅ First slice DONE (2026-10-14): product homepage at `/` (app moved to `/app`),
    positioned per the Qodo study below; every claim maps to a gate number or a shipped
    control. Web scans now enforce the S4 authorized-target confirmation (was CLI-only).

**NEXT — ordered (re-sequenced 2026-10-15 after the KB mining; each item green + gated before the next):**
18. ✅ **KB mining batch 1+2 (files 05, 11, 12, 14)** — not-tested ledger, 5 spec-typed probes,
    13-field Jira Bug + `--client-safe` + evidence attachment, `--reverify N` (measured X/X),
    read-back retry, grouped gaps, data-state ledger. (40 probes, suite 527.)
19. ✅ **`qax verify-fixes` (Q3, step 1 — close the loop) — DONE 2026-10-15 (24436a4)** — re-check filed findings →
    FIXED / STILL REPRODUCES / COULD NOT TEST (the not-tested ledger decides the third bucket;
    a probe that merely stopped firing is NOT clearance). Never closes as Duplicate, no
    comment on unfixed, per-batch approval, record a build fingerprint (Last-Modified/ETag/root
    hash) so "not redeployed" is distinguishable from "not fixed".
20. ✅ **Prove the new probes can FAIL on a running server — DONE 2026-10-15** (golden target `/tasks` + 3 truth entries; recall 14/14; gate checks typed probes fire / are exercised / silent on SECURE; mutation-checked: breaking `boolean_false_probe` makes the gate FAIL) — original text: — extend the golden target with
    planted bugs for the 5 typed probes + access-revocation (+ secure controls) and truth
    entries ("show every test failing once"; also Q5 benchmark credibility).
21. ✅ **Q2 — time-to-first-confirmed-finding <= 10 min — DONE 2026-10-15** (`python main.py demo`; measured ~26 s from an empty venv incl. pip install on a clean copy with no .env and no key; Docker deliberately NOT shipped — unverifiable on this machine). Original text: (one command / Docker + bundled demo target).
22. ✅ **Q4 — GitHub Action — DONE 2026-10-15** (`ci/pr_scan.py` + `ci/github_action_scan.yml` rebuilt on the honesty rules; run for real against the golden target). Original text:: scan a preview URL, comment CONFIRMED only (+ UNSTABLE/NOT TESTED honesty).
23. ⏸ **Q3 step 2 — confirmed finding -> runnable regression test -> Xray** (Xray half of B6 waits on the owner's trial app).
24. ✅ **Instrument the OLDER probes' silent bail-outs — DONE 2026-10-15** (all 40 probes now report not-applicable / missing-prerequisite / unreached / could-not-exercise; 131 ledger call-sites; 116+ honesty tests; found+fixed 3 real blind spots: command-injection, ssrf and debug-disclosure sent NO credentials; ssrf can't be tested against a remote target). Original text: — `verify-fixes` calls a bug FIXED when its
    probe "ran and reported no gap", but ~30 older probes still `return []` silently when their
    own control fails (e.g. bola's create, persistence's read-back) with no ledger entry. Until each
    reports "could not exercise" like the 5 typed probes do, FIXED is only as honest as the probe.
    (The not-tested ledger + `_skip()` already exist; this is mechanical, probe by probe, gated.)
25. **KB mining continues**: file 13 (automation deep pass), 01-03 principles/lifecycle, 06, 08-10 (see memory).
Opportunistic/gated: B6 Xray half (owner), Q5 open benchmark (gated on B9 IP), B8 GTM slices, B9 IP/legal (deferred).

**From the Qodo competitive study (2026-10-14, `strategy/QODO_COMPETITIVE_INTEL.md`):**
Qodo (≈$120M raised) is now enterprise AI *code review + governance*. It **retired test
generation** (Cover unmaintained since Jun 2025; IDE plugin retires 30 Oct 2026) and shows
**no runtime testing** in public docs. So QAX does NOT compete on code review — it is the
**runtime proof layer** next to any reviewer ("code review reads the diff; QAX proves what
the running app does"). Folded into the order above:
- **Q1 → B6 + B4 first** — live Jira filing and one published case study are the proof the
  homepage still marks as "dry run" / "in progress". Highest leverage now.
- **Q2 — packaging: time-to-first-confirmed-finding ≤ 10 min** on a clean machine
  (one command / Docker + bundled demo target). Qodo installs in one click; we don't.
- **Q3 — confirmed finding → runnable regression test → Xray** (unique loop; nobody links
  runtime proof to test management).
- **Q4 — GitHub Action: scan a preview URL, comment CONFIRMED only.**
- **Q5 — open benchmark:** publish golden targets + truth files + `make eval` (Qodo's
  credibility playbook, done honestly incl. misses). Gated on B9 (IP) before going public.
- **Later, only on demand:** MCP tool exposure (`qax.scan`), hosted mode (requires
  target-ownership verification first), pricing tests (per-app + free local tier; see the
  report §11 — hypotheses only).
- **Do NOT build:** AI PR review, rules/governance dashboards, IDE plugin, SSO/on-prem,
  standalone test-gen expansion — commoditized or owned by funded incumbents.

**Opportunistic (externally gated — do the moment the trigger arrives, not in line):**
- **B6 — real Jira/Xray filing** — Bug half DONE (SCRUM-5/6 filed from Gitea, see B6
  below); Xray Test half waits on the owner installing the Xray trial app.

**Deferred parallel:** **B9 — IP / legal / solo-founder** (owner's choice; still
existential — revisit before external distribution).

---

## Detail — blind-spot backlog (B) items

(Ordered in the MASTER WORKLIST above; full descriptions here.)
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
      the golden target + a truth entry. QAX now has **33 probes**; gate recall 10/10, FP 0.
      - [x] **BFLA — Broken Function-Level Authorization (OWASP API5)** — `bfla_probe`:
        an ordinary user reaching a privileged admin-namespaced GET that rejects a
        no-token request = P1 escalation (converts a flaky LLM lead into a deterministic
        finding). +3 tests.
      - [x] **SSTI — server-side template injection** — `ssti_probe`: a template
        expression evaluated server-side, proven by a two-product differential control
        (P1, RCE-class). +2 tests.
      - [x] **Host-header injection** — `host_header_injection_probe`: a forged Host
        used to build a URL (Location/body URL context) = reset-link / cache poisoning
        (P2). +2 tests.
      - [x] **XXE — XML external entity** — `xxe_probe`: POST an XML body whose external
        entity reads /etc/passwd; confirmed by the `root:x:0:0` signature (P1). +2 tests.
      - [x] **GraphQL introspection** — `graphql_introspection_probe`: a minimal
        introspection query that returns a real schema (data.__schema.queryType) =
        full attack-surface disclosure (P2). +2 tests.
      - [x] **Unrestricted file upload** — `file_upload_probe`: upload an HTML file,
        RETRIEVE it (same-origin URL from the upload response), and confirm it is served
        back as text/html with the marker = stored-XSS/RCE-class (P1). 0-FP via the full
        round trip; labelled mutating. +3 tests.
      - B3 planned batch COMPLETE (6 classes). The track stays open — add more classes
        opportunistically as B4 reveals real-world gaps.

- [x] **B4 — evidence on real, non-cherry-picked apps (DONE: 3 real apps, 3 stacks, 0 FP — DRF, Go/Gin, Gitea; bar was >=2).**
      First case study DONE: a real MIT Django-REST RealWorld backend, scanned
      black-box on SQLite (`strategy/CASE_STUDY_REALWORLD_DRF.md`). It drove two real
      QAX improvements: drf-yasg/drf-spectacular spec grounding, and a FALSE-POSITIVE
      fix (public GET flagged via the spec's global security — now needs per-op
      auth_explicit). Final: 0 confirmed, 0 FP, 1 legit P3 (no CSP) — QAX stays quiet
      on a well-written app. Second case study DONE: a spec-less MIT Go/Gin RealWorld backend
      (strategy/CASE_STUDY_REALWORLD_GO.md) — 0 confirmed, 0 FP, 1 P3; drove a wider
      spec-less discovery (B5 now probes /api/<resource>). **Third case study DONE
      (2026-10-15): Gitea 28.1.0** (MIT, Go, SQLite, 537 ops, token-only auth,
      privilege-tiered admin API — `strategy/CASE_STUDY_REALWORLD_GITEA.md`). First
      LARGE, token-authed, privilege-tiered real app. Final **0 FP + 2 real findings**
      (host-header injection P2, CSP P3, both hand-verified). Drove 5 fixes: Gitea
      spec-path discovery; **G1** supplied `--token`/`--token-b` now flow into the
      coverage layer (was silently self-registering → no-op on token-only APIs);
      **a 12× P1 BFLA false-positive cascade** (BFLA was using the privileged primary
      token as its "ordinary user") — now uses a provably-ordinary identity only;
      **G2** setup-failed coverage tests render `[SETUP]` not ❌FAIL; **G3** validation
      asserts only spec-`required` fields. **B4 bar exceeded: 3 real apps, 3 stacks,
      0 false positives on all three.**
- [x] **B4-orig (absorbed into B4 above — DONE).** Risk was: all proof
      is on small/known-vulnerable or seeded targets. Task: run QAX on 2–3 large,
      real OSS apps we did NOT pick for being vulnerable; publish honest scorecards
      (recall/FP/$/run) even when the result is "0 found, 0 FP". Done when:
      `strategy/` holds ≥2 real-app case studies with scorecards. (Absorbs Phase-2
      case studies.)

- [x] **B5 — no-spec endpoint discovery (DONE).** `live_surface.discover_endpoints`:
      when the app serves no OpenAPI, probe a set of common REST/GraphQL paths and add
      the ones that are actually LIVE (definitive handler response), not already in the
      model — so a spec-less target gets a real surface instead of only unverified code
      inference. Wired into the orchestrator's no-spec branch. +1 unit test. (Not
      gate-exercised — the golden target has a spec; verified by unit test.)

- [~] **B6 — Jira/Xray filing never proven live (Bug half DONE 2026-10-15).** Risk:
      "files to Jira" is DRY_RUN only. Task: with the owner's trial creds, file a real
      Bug + a real Xray Test end-to-end, then restore DRY_RUN. Done when: one real
      issue + one real test exist in a trial project, with screenshots in `strategy/`.
      (Absorbs Phase-2 Jira.)
      **Bug half DONE:** owner's free Jira Cloud (team-managed project SCRUM). First
      contact exposed 5 gaps, all fixed (commit a839a44): `priority` sent though not
      on a team-managed Bug screen (→ would 400) — now screen-aware; live mode demanded
      Xray creds for a plain Bug; fake dry run; no de-dup; one-line bug body. New
      `core/jira_bugs.py` + `qax file-findings`, bug body in the owner's compact QA
      format. **Filed live: SCRUM-5 (P2 host-header injection) and SCRUM-6 (P3 CSP)**
      from the Gitea case study; re-run detected both as open duplicates (0 re-filed).
      DRY_RUN stays true. **Remaining:** the Xray Test half needs the Xray app
      installed on the site (separate trial + client id/secret) — owner's call.

- [x] **B7 — business-logic bug probe (DONE).** `business_logic_probe`: a NEGATIVE
      quantity/amount accepted AND stored on a create endpoint — the first deterministic
      logic class, with a clean behavioural control (positive accepted + distinctive
      negative accepted & echoed; a correct app rejects the negative). P2, mutating.
      Seeded in the golden target (/orders; SECURE rejects qty<1); gate recall 11/11.
      +2 tests. Closes the "logic bugs unproven" gap with one solid class (more logic
      patterns — price-tamper, workflow-skip — can follow opportunistically).

- [ ] **B8 — thin moat (strategic, not a code task).** Risk: the deterministic
      probes are replicable by a funded incumbent. Task (GTM): lean the whole
      message on the one thing they structurally won't copy fast — "**proven, zero
      false positives**" — and build the brand/speed around it; revisit when
      pitching. Done when: positioning + deck lead with proof-not-noise and the
      case studies (B4) back it.

- Deferred (owner's choice, still existential): **B9 — IP / legal / solo-founder
      risk.** See the deferred track below and the IP status memory.

---

## Detail — self-hardening (S) items, from the 2026-10-14 implementation audit

The audit (verified against source — every checked claim was accurate) exposed a
whole category the B-backlog missed: **is QAX itself safe?** A security product that
is itself insecure is an existential credibility risk — we audit others but hadn't
dogfooded ourselves. These are real, mostly deployment/safety items (no P0). The B1
(deterministic/AI split) and B2 (stage diagnostics) work was independently confirmed
present by the audit.

**Order lives in the MASTER WORKLIST above** (S1–S7 + S8-security done; S8-refactor
is item 16). S1 came before B6 so an accidental live push is impossible.

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
- [x] **S3 — web API auth/authz + export policy (DONE).** A `before_request` gate: when
      `QAX_API_TOKEN` is set, every `/api/*` call must present a matching `X-QAX-Token`
      (else 401); unset = local-dev open mode (so exposing the server means setting the
      token). Export live-gate: a caller can NEVER force a live push — a live export
      needs the operator's server-side `QAX_ALLOW_LIVE_EXPORT=true` AND a non-dry-run
      request; otherwise it is always a dry run. +3 Flask-test-client tests.
- [x] **S4 — authorized-target gate + active-probe safety (DONE).** `main.py scan` now
      REFUSES without `--authorized` (exit 2), naming the data-mutating probes that will
      run — explicit ownership/permission confirmation before any active probing.
      `execution/probes.MUTATING_PROBES` labels the writers (bola, mass-assignment,
      stored-xss, persistence, injection, nosql, malformed-input). Cleanup is tracked +
      reported (created/cleaned records); honest caveat kept — tracking is not a
      guaranteed rollback. +2 unit tests. (Follow-up: the same consent gate for the web
      entry lands with S3.)
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
- [~] **S8 — UI escaping (DONE) + module splits (IN PROGRESS, incremental).** Security
      half done (ui/app.py html.escape of subprocess output). Module-split half started
      incrementally (audit: "incrementally, no rewrite"): extracted the scan-result
      console rendering out of main.py into `cli/scan_render.py` — pure + unit-tested,
      and aligned with B1 (deterministic CONFIRMED vs AI leads, not severity). main.py
      shrank ~25 lines and that logic now has a test net. Done so far: `cli/scan_render.py`, `cli/summaries.py` (run/push/sessions), and
      `cli/output.py` (save JSON/markdown, find saved case) — all unit-tested; main's
      helpers now delegate, plus `cli/files.py` (source-file collection). main.py
      2679 → 2507 lines; every extraction unit-tested, CLI exit codes pinned.
      Then: `_process_single_file` (the CLI's own ~220-line generation path). Done so
      far on it: (1) characterization tests pin its behavior; (2) fixed TWO real bugs
      it had — per-chunk cache keys (large files were analyzed on chunk 1 only when
      caching was on) and the size_lines/line_count key mismatch (always passed 0);
      (3) ported chunking + testability-skip + real include_deps INTO GenerationService
      (so scan/pipeline/multi-source paths gain them). ✅ SWAP DONE: parity added
      (GenerationService gained screenshot capture + a validate=False mode; git risk
      was already covered by include_git→SmartChecker), then run_analyze_command was
      rewired to generate via GenerationService(validate=False) keeping its own
      validate/report/auto-accept/push flow byte-for-byte; _process_single_file + 3
      dead imports deleted. ONE generation path now serves CLI analyze + scan +
      pipeline + multi-source. main.py 2679→2288 lines. Verified `analyze` end-to-end
      (10 valid, 0 invalid) + gate green. S8 ui/app.py: STARTED — extracted the pure HTML/format helpers (badge,
      confidence_bar, risk/score badge classes, mask_secret, count_todos,
      build_markdown_export) to a testable `ui/presenters.py` (no Streamlit import);
      ui/app.py 2281→2174. The `st.*`-heavy wizard/render views stay inline (need
      the runtime). Also extracted the data-access helpers (list_projects, load_all/latest_test_cases,
      connection_status) to `ui/data.py` (pure, takes a root path); app.py keeps the thin
      @st.cache_data wrappers. ui/app.py 2281→2149. Remaining: the st.*-heavy wizard/render
      views stay inline (need the runtime) — a later pass could move their pure sub-logic.
      ✅ FIXED: the `.cs` folder-mode default (and a hard-coded "example3" → .ts map) is
      gone. Both folder paths (CLI + pipeline FolderInputAdapter) now auto-detect the
      folder's dominant source language (`core.code_reader.detect_dominant_extension`),
      skip vendored/build/collected-asset dirs + minified files, and `"*"` means all
      files. Verified on 4 real trees (QAX→.py, DRF→.py, Go/Gin→.go, FastAPI→.py).

---

## Phase 1 — HARDEN (original framing; now subsumed by the MASTER WORKLIST above)

Goal: make quality provable and repeatable before adding surface. (Kept for history;
the live order is the MASTER WORKLIST.)

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
