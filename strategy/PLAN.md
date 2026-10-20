# QAX Roadmap: Self-Hosted Validation to Public SaaS

**Status:** Revised draft for founder review  
**Updated:** 2026-10-17  
**Verified baseline reported by founder:** commit 436d039  
**Purpose:** Single execution plan and status/decision log for reaching a public SaaS launch.

> Evidence note: the run results in this plan were supplied by the founder as verified on commit 436d039. They have not been rerun by this reviewer in this session. Keep the evidence attached to the commit and update this plan at every phase gate.

## 1. Goal and product boundary

The long-term goal remains a public SaaS launch and announcement. The revised sequence delays hosted infrastructure until self-hosted use establishes demand and the product’s workflow is useful.

QAX today is a self-hosted CLI with a local web UI. It is single-tenant and reads secrets from .env. There is no hosted mode, customer tenancy, or billing.

The initial product hypothesis is a QA or development team that needs to find actionable web-application security defects in an authorized staging environment, with evidence they can review and a practical path to Jira. Treat this as a hypothesis to validate, not established market demand.

For now, keep the product claim narrow: a self-hosted scanner that runs deterministic probes, reports evidence-backed findings, and can support a human-reviewed workflow. Publish only capabilities supported by measured results.

**Functional QA track (founder decision 9, 2026-10-19):** end-to-end functional QA — input (code, story, Jira, PR) → plan → AI-generated test cases → execution against the running app → verification → report → Jira/Xray — is a core part of the product story, not an adjacent path. The same honesty rules apply: generated cases are AI proposals until executed; a case that did not run is never PASS; no external write without the operator's explicit opt-in; Xray is not claimed until tested end to end on a real instance.

## 2. Current evidence baseline

The following is the founder’s report of runs against commit 436d039 on 2026-10-17:

- Test suite: 768 passed, 3 skipped. The merge gate, make eval, passed.
- Golden target: 32 documented planted bugs; recall 32/32 and zero false positives. Its SECURE twin stayed silent for every probe.
- Mutation sweep: the last full sweep detected loss of a CONFIRMED finding for 39 of 40 individually disabled probes. The forge-jwt probe was verified separately through a new source lane. Together, the founder reports every probe has a planted bug the gate catches. Preserve the distinction between the full sweep and the separate source-lane check.
- Read-only: the golden server counted requests by HTTP method; a --read-only scan added zero write requests. The check caught 74 leaked POST requests before the fix.
- Quick scans: zero AI-provider calls even when an API key was set. A local listener observed three calls before the fix and zero after it.
- Five real applications were scanned from external clones with permissive licenses: DRF RealWorld, Node/Express RealWorld, Go/Gin RealWorld, Java/Spring RealWorld, and Gitea 28.1. The reported outcome after fixes was zero confirmed false positives across those five.
- Healthchecks was the sixth application, scanned on 2026-10-17. It exposed and led to a fix for a host-header false positive. Without an OpenAPI specification, QAX discovered only one endpoint. Spec-less endpoint discovery is the current blocker.
- Jira: two bugs were filed live to the founder’s trial site.
- Xray has never been tested.
- verify-fixes has unit-test coverage but has not been run against real tickets.
- The GitHub PR gate was built and run against the golden target, not against pull requests in a real repository.
- Scorecard truth matching uses keyword-substring matching. A NoSQL injection finding was matched to a SQL injection truth entry; that case was fixed, but the matching method still needs stricter semantic rules.
- The less-than-15% false-positive figure in scorecard.py is an internal target, not a measured result.
- The scanner has an egress boundary: it does not send tokens or requests off the target origin. Hosted-grade SSRF protections are absent, including DNS rebinding defenses, private-range and metadata blocking, and redirect validation at connection time. These are required before scans run from QAX infrastructure, not a launch prerequisite for the current self-hosted product.
- Product surface: QAX is self-hosted, single-tenant CLI plus local web UI; secrets come from .env. No hosted mode, tenant isolation, or billing exists.

## 3. Execution rules

1. Work through phase gates. Dates are planning windows, not permission to advance without meeting the exit criteria.
2. Run the first external demos after product-completion engineering (decision 8; the original 2026-10-17 to 2026-10-23 window is deferred). Engineering does not wait on them.
3. Use only applications and environments the operator is authorized to test. For external alpha users, default to read-only against their own staging systems. Self-hosting reduces QAX’s data custody and infrastructure egress exposure; it does not remove target-side risk, local secret risk, or data sent by an explicitly invoked deep AI workflow.
4. Explain what data leaves the user’s machine, what is sent to an AI provider, and what Jira actions may write before a user enables those workflows.
5. Record metrics with definitions and denominators. Do not use a dashboard target as if it were a measured result.
6. Keep one run record for every evidence run: version/commit, target scope, discovered endpoints, checks executed and skipped, finding evidence, read-only/write activity, AI calls and cost, and Jira mode/results.
7. Do not use employer code, credentials, or data without the right and permission. Company beta waits for the founder’s IP/legal review expected in early 2027.
8. Treat a benchmark as specific to its corpus and method. Never claim general recall or false-positive performance from the golden target or five clean applications.
9. This file is the single reference for phases, dates, decisions, and status. Update the decision log at each gate; do not maintain a competing roadmap.
10. No public announcement or hosted scanning before the relevant release gates pass.

## 4. Phases, dates, and exit criteria

### Phase 0 — External demo and problem discovery

**Window:** 2026-10-17 to 2026-10-23  
**Status:** Ready to start  
**Intent:** Show the current demo to 2–3 QA or development practitioners and learn whether the workflow addresses a recurring, valuable problem. This is a demo and discovery step, not a promise of production readiness.

**Actions**

- Show qax demo without requiring installation.
- Ask about their current testing and triage process, where evidence gets lost, what they trust, and what they currently pay for or maintain.
- Observe which result they consider useful and what they need before running on an authorized staging target.
- Capture objections, requested workflow changes, and whether they would try a self-hosted alpha.
- Do not count compliments, feature requests, or hypothetical interest as purchase evidence.

**Exit criteria**

- Complete 2–3 demos, or document specific outreach/access blockers and a dated next attempt.
- Save concise notes with the role, workflow, pain, current alternative, trust concerns, requested next step, and any willingness-to-try or willingness-to-pay signal.
- Identify the next product question from evidence. If the problem or target user remains unclear, repeat discovery before expanding product scope.

### Phase 1 — Self-hosted evidence and alpha readiness

**Window:** 2026-10-19 to 2026-11-15  
**Status:** In progress  
**Current work order:** spec-less endpoint discovery, then cookie-session and CSRF authentication — both done (see §9). Remaining work is product completion: close evidence-backed engineering defects first; the human-dependent exit criteria below are deferred until that is done (decision 8).

**Actions, in sequence**

1. **Improve spec-less endpoint discovery.** Use Healthchecks as a regression case. Report when coverage is incomplete instead of implying the application was meaningfully scanned. Measure discovery against a known endpoint inventory where available.
2. **Add and validate cookie-session plus CSRF coverage.** Then assess login forms and API-key headers using authorized applications. Do not label an auth mode supported until its request flow, failure cases, and findings are verified.
3. **Strengthen scorecard truth matching.** Stop relying on keyword substrings as the adjudicator. Match by normalized vulnerability class and relevant attributes; mark ambiguous matches for human review. Add regressions for SQL versus NoSQL injection and other near-neighbor classes.
4. **Expand real-application evidence from five to at least ten permissively licensed applications.** Include spec-less applications where feasible. Inspect candidate findings and record denominators, app/version, scope, discovered endpoints, confirmed findings, false positives, not-tested checks, runtime, and request behavior. Preserve the current result as “zero confirmed false positives reported on five applications,” not a general rate.
5. **Measure rate-limit and WAF behavior.** Use bounded retries and clear stopped/limited states. Do not attempt to bypass WAF controls.
6. **Exercise verify-fixes against real trial tickets.** Start with a dry run that shows the proposed verification and expected writes. Only perform live ticket changes with explicit operator authorization.
7. **Make the local UI usable for an alpha.** Show a plan preview before a scan: target, scope, endpoints, probes, read-only mode, expected writes, and missing prerequisites. Add a run history with finding evidence and status. Keep a clear distinction between confirmed, accepted, not tested, and unexecuted.
8. **Consolidate the operator path and quickstart.** Make one documented command path from setup to preview, read-only scan, result review, and optional Jira filing. Record any unsupported or confusing path; avoid presenting multiple local UIs as one coherent product.
9. **Review release hygiene.** Prepare versioning, changelog, security contact/process, and license decision in coordination with the founder’s IP/legal review. A license file by itself does not establish ownership or resolve third-party rights.

**Exit criteria**

- The current test suite and make eval pass on the release candidate; preserve commit-level output.
- Golden and SECURE controls pass; mutation evidence records the last full sweep (40/40 on 4c2fabd, source lane included — see §9).
- Read-only regression still measures zero write requests; per-run ledger agrees with server-side method counts.
- Spec-less discovery reports its known coverage limits and passes a documented regression corpus.
- SQL/NoSQL and other ambiguous truth cases cannot be silently accepted by substring overlap.
- At least ten real applications have run records with reviewed findings and explicit denominators; no claim of broad accuracy is made from this set.
- Supported auth modes have reproducible evidence, including failures and CSRF behavior where relevant.
- verify-fixes has at least one real-ticket dry-run reviewed; any live write is separately authorized and recorded.
- A new alpha user can configure a target and understand the scan plan before execution, using the quickstart without founder-led screen sharing. Record setup time and unresolved blockers.

### Phase 2 — Self-hosted private alpha

**Window:** 2026-11-16 to 2026-12-31  
**Status:** Planned; gated on Phase 1  
**Participants:** Start with 3–5 trusted QA or development practitioners.

**Actions**

- Give each participant the self-hosted package and quickstart. They run it on their own authorized staging applications; default to --read-only.
- Explain the local data flow, optional AI data flow, Jira write behavior, limitations, and how to stop a run.
- Capture setup success, first useful result, not-tested checks, reviewed false positives, runtime, support burden, and repeat usage.
- Ask users to show their real workflow and current alternative; do not interpret praise as product-market evidence.
- Triage every reported false positive and add a regression when reproducible.

**Exit criteria**

- At least three participants complete a run against an authorized application.
- At least two complete setup and first-run review without step-by-step founder assistance; target first useful result within ten minutes, or document the measured cause when it takes longer.
- Every reported finding is reviewed or explicitly marked unresolved; there are no untriaged high-impact false positives.
- The run records are complete enough to reproduce the scope and explain what ran and what did not.
- Evidence shows whether users return for another cycle and whether a company design-partner beta is justified. If alpha use is weak, fix the concrete blocker or stop and reassess before building hosted infrastructure.

### Phase 3 — IP/legal review and self-hosted design-partner beta

**Window:** 2027-01-01 to 2027-02-15, subject to counsel availability  
**Status:** Blocked on IP/legal review; companies wait for this review.

**Entry conditions**

- A qualified legal review addresses ownership and provenance of code, prior-employer boundaries, third-party dependencies and licenses, rights to benchmark data, distribution terms, and any applicable privacy or security commitments.
- The founder confirms in writing what code and data may be used. Do not include employer code or data without explicit rights.
- Define the beta agreement, authorized-scope requirements, support expectations, and data-flow disclosures.

**Actions**

- Work with a small number of design-partner companies on their authorized staging systems.
- Start with self-hosted execution and read-only defaults. Keep any Jira writes behind preview and explicit confirmation.
- Track weekly use, repeat cycles, reviewed findings, false-positive resolution time, installation friction, and support hours.
- Validate Jira in their workflow. Keep Xray out of scope until a partner has a concrete need and the integration can be tested end to end.
- Treat the PR gate as unvalidated on real repository pull requests. Add it to beta only if partners identify it as necessary to their core workflow.

**Exit criteria**

- At least one design partner returns in a later testing cycle and completes a useful run.
- At least one buyer gives concrete willingness-to-pay evidence, such as a budget discussion or a conditional purchase path, rather than praise alone.
- Findings and false positives are adjudicated with recorded denominators; no unresolved high-severity product or security issue remains for the agreed beta scope.
- If the PR gate is included, it has been run on a real repository’s pull requests for at least four weeks without the team disabling it because of noise.
- The founder makes an explicit decision to proceed to hosted-mode design, continue self-hosted, or stop/reposition.

### Phase 4 — Hosted-mode architecture and security gate

**Window:** 2027-02-16 to 2027-03-31, only after Phase 3 evidence supports hosted investment  
**Status:** Deferred; not started  
**Intent:** Decide whether QAX should host scans, provide a hosted control plane with a customer-run runner, or remain self-hosted. Do not assume the first option.

**Required design and verification**

- Threat model and data-flow map for tenants, repositories, target URLs, credentials, code, AI providers, artifacts, logs, and integrations.
- SSRF defenses before any scan originates from QAX infrastructure: resolve and validate public address ranges, block loopback/private/link-local and cloud metadata destinations for IPv4 and IPv6, revalidate redirects and the actual connection destination, and address DNS rebinding. Enforce egress policy at the runner/network boundary.
- Per-job runner isolation, resource/time limits, cleanup, and a documented failure/kill path.
- Strict authorized-target scope and least-privilege Git/Jira credentials. Prevent secrets from appearing in logs, URLs, screenshots, or reports.
- Tenant isolation across data, queues, storage, jobs, logs, artifacts, and backups. Validate with negative cross-tenant tests.
- Retention, deletion, backup/restore, incident response, AI-provider disclosures, prompt-injection boundaries, structured-output validation, and cost limits.
- Decide scan placement based on partner evidence and security review. Self-hosted is the default if hosted execution cannot meet the gate.

**Exit criteria**

- Founder approves a written architecture, threat model, and data-flow design.
- Security tests demonstrate SSRF and redirect protections at connection time, isolation between tenants, safe secret handling, and bounded runner behavior.
- Delete, backup, restore, incident, and operational procedures have been exercised.
- Unit economics and an operating-cost model are credible for the beta use case.
- No hosted scan is enabled before every required security finding is fixed and the gate is documented as passed.

### Phase 5 — Minimum hosted SaaS build

**Window:** 2027-04-01 to 2027-06-30; planning estimate of roughly 8–12 weeks after approved architecture, not a delivery promise  
**Status:** Not started; depends on Phase 4  
**Intent:** Build only the hosted workflow needed for the validated customer job.

**Likely minimum scope**

- Account/workspace and tenant identity, authorization, and isolation that match the approved design.
- A clear workflow to configure a project/target, preview a run, execute or connect the approved runner, review evidence, and see history and diffs.
- Human review and confirmation before Jira or other consequential writes.
- Usage metering, quotas, alerts, and spend caps for scans, AI, and storage.
- Operational monitoring, job failure handling, support access controls, backups, restore, deletion, and rollback.
- Onboarding, quickstart, privacy/data-flow explanation, and support path.

**Explicitly defer unless evidence changes**

- Broad multi-provider AI support, native mobile testing, extensive PR governance, Xray, or features requested only by analogy to competitors.
- Do not charge for or announce an integration before it is exercised end to end.

**Exit criteria**

- A new customer can complete onboarding and the primary workflow without founder intervention.
- Tenant-isolation, authorization, deletion, failure, recovery, metering, and hosted security-gate checks pass.
- Costs and support time are measured on representative beta use, with limits that prevent uncontrolled spend.
- The hosted workflow does not overstate endpoint coverage, test execution, or finding certainty.

### Phase 6 — Commercial readiness and public launch

**Window:** 2027-05-01 to 2027-07-31 as a provisional target, gated on Phase 5; may move  
**Status:** Not started; target only, not a commitment  
**Intent:** Launch and announce only when the product can be operated and supported responsibly.

**Actions**

- Set pricing from observed customer value, support burden, and measured infrastructure/AI cost. Test willingness to pay with beta participants before fixing tiers.
- Complete counsel-led review of IP, distribution, privacy terms, customer agreements, security representations, and launch claims.
- Prepare incident/support ownership, status and escalation path, usage limits, deletion workflow, backup recovery, and customer onboarding.
- Publish benchmark methodology and results with corpus, version, denominators, matching rules, and limitations. Keep planted-target results separate from real-app results.
- Prepare launch documentation and announcement only after the release gate is signed off.

**Exit criteria**

- At least one design partner returns for a later cycle and gives concrete willingness-to-pay evidence; ideally convert at least one into a paid customer before a broad announcement.
- Public claims have traceable evidence and match the shipped feature set.
- No unresolved critical/high security issue remains in the public launch scope.
- Pricing covers measured variable costs and an explicit support model; quotas and alerts are active.
- Legal, privacy, operational, support, rollback, and incident procedures are ready.
- Founder signs the release decision. If any gate is not met, move the date rather than waive the criterion.

## 5. Metric definitions

Publish these definitions with every scorecard or benchmark. Record numerator, denominator, corpus version, application/version, and adjudication method.

- **Confirmed finding:** A finding manually or deterministically adjudicated as a real defect, with reproducible evidence and a defined vulnerability class.
- **False positive (FP):** A reported finding adjudicated not to represent the claimed defect. Record the reason and whether it was caused by matching, discovery, probe behavior, or interpretation.
- **False negative (FN):** A documented in-scope planted or independently confirmed defect that the relevant run did not report. Do not infer all real-world FNs from an unknown corpus.
- **Recall:** Confirmed in-scope truth items detected divided by all confirmed in-scope truth items in the declared corpus.
- **FP rate:** State the denominator explicitly. Report both FP count and the chosen rate, such as FP among adjudicated reported findings. Never mix this with a target or an unreviewed finding set.
- **Not tested:** A check that did not execute, including missing authentication, undiscovered surface, unsupported input, or a safety stop. It is not a pass.
- **Coverage:** Name the denominator (known endpoints, configured probes, requirements, or another defined universe). Probe count alone is not application coverage.
- **Accepted finding:** A human workflow state, not proof that a finding is correct.
- **Matching:** Match by vulnerability class and material attributes, not keyword overlap alone. Ambiguous truth matches require review; preserve the source item and adjudication.

The less-than-15% FP number remains an internal target until a measured rate with a declared denominator supports a claim.

## 6. Required per-run record

For every benchmark, alpha, beta, or hosted run, record:

- QAX version and commit; configuration and enabled mode.
- Authorized target and scope, operator, time window, and target application version where known.
- Discovered endpoints and discovery source; known or expected endpoint inventory where available.
- Each check as executed, skipped, unsupported, blocked, or not tested, with reason.
- Findings with evidence, vulnerability class, confidence/state, adjudication, and related truth item if applicable.
- Request counts by method and destination; read-only/write mode; rate limits and stop conditions.
- AI provider/model calls, data sent, token/cost estimate, and any local instrumentation used.
- Jira/Xray or other integration mode, proposed writes, approvals, and resulting identifiers.
- Runtime, errors, cleanup result, and whether the run was reproducible.

## 7. Main risks and responses

| Risk | Current evidence or uncertainty | Response |
|---|---|---|
| Demand is unproven | No external-user evidence is recorded here yet | Demo this week; alpha before hosted investment |
| Spec-less apps have poor discovery | Healthchecks exposed one discovered endpoint without OpenAPI | Fix discovery and make incomplete coverage visible |
| Scorecard truth matching is too loose | SQL/NoSQL substring collision occurred | Semantic matching rules, ambiguity review, regression corpus |
| Real-world accuracy is uncertain | Five applications reported clean; sixth exposed a FP and discovery gap | Expand reviewed corpus and publish denominators |
| Self-hosted runs can still affect targets | Read-only check is strong evidence, but auth and application state vary | Authorized staging only, read-only default, preview and audit |
| Hosted execution adds SSRF and isolation risks | Hosted-grade defenses are not present | Separate mandatory hosted security gate before hosted scans |
| AI/privacy expectations may be misunderstood | Quick scans made zero provider calls; deep mode data flow needs clear user understanding | Show mode and data flow before use; meter and disclose calls |
| Commercial/IP rights may be unclear | Legal review is expected early 2027 | Delay company beta and distribution decisions until review |
| Integrations may be overstated | Jira used live; Xray untested; verify-fixes not run on real tickets | Validate actual workflows before marketing them |

## 8. Reference links

These references support the security and operating principles; review current guidance again when implementing hosted mode.

- [OWASP Server Side Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
- [AWS SaaS Tenant Isolation Strategies](https://docs.aws.amazon.com/whitepapers/latest/saas-architecture-fundamentals/tenant-isolation.html)
- [GitHub App permission guidance](https://docs.github.com/en/apps/creating-github-apps/setting-up-a-github-app/choosing-permissions-for-a-github-app)
- [Anthropic data usage and retention](https://privacy.claude.com/en/articles/7996868-how-long-do-you-store-my-organization-s-data)
- [OWASP Cross-Site Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)

## 9. Status and decision log

**As of 2026-10-17, based on founder-reported runs at 436d039:**

| Area | Status | Evidence / decision |
|---|---|---|
| Test suite | Done | 768 passed, 3 skipped |
| Merge gate | Done | make eval passed |
| Golden target | Done | 32 documented planted bugs; recall 32/32; zero false positives |
| SECURE twin | Done | All probes stayed silent |
| Mutation coverage | Done, refreshed 2026-10-19 (40/40 on branch feat/functional-qa-track, after the functional-QA work) — earlier: refreshed 2026-10-17 | A full sweep at commit ~f8c31f8 (all lanes incl. the source lane) detected a lost CONFIRMED finding for **40/40** disabled probes (`make eval-mutants`). Later commits (CLI fix, catch-all discovery, web UI) did not touch probe logic; re-run before the next release |
| Read-only | Done | Zero added write requests on golden target; prior check caught 74 leaked POSTs |
| Quick-scan AI boundary | Done | Local listener saw zero provider calls after fix, including with API key set |
| Real applications | **10 of 10 (2026-10-17)** | Apps 1–5 as before; 6–10 (Healthchecks, microblog-api, CTFd, linkding, PocketBase) in `strategy/evidence/PHASE1_CORPUS_2026-10-17.md`: 8 confirmed findings in the final scans, 8 true positives (each re-checked by hand), 0 FP. 4 false positives were produced on the way (one a P1) and fixed with regression tests. n = 10, local, one author: not a general rate |
| Spec-less discovery | Done 2026-10-17 | Reads the app's docs pages; carries the operator's credentials; trailing-slash aware; learns POST schemas, item routes, DELETE and auth-required from the app's own OPTIONS/list answers; `--spec` and `--endpoints FILE` for what cannot be found; spec-less "not applicable" now carries a blind-spot gap. Healthchecks 1→36, linkding 6→18, microblog 2→27, CTFd 9→154 |
| Jira | Partial / exercised | Two bugs filed live to founder’s trial site |
| Xray | Not started | Never tested |
| verify-fixes | Exercised (read-only) | 2026-10-17: run against the 2 real trial tickets (SCRUM-5/6) as a read-only dry run — 1 STILL REPRODUCES, 1 no-longer-reproduces flagged "build unverified" and NOT auto-closed. No live write (needs owner's OK) |
| GitHub PR gate | Partial | Built and run on golden target; not run on real repository pull requests |
| Scorecard FP target | Target only | Less-than-15% is internal and unmeasured; matching still needs stricter semantics |
| SSRF boundary | Partial by deployment context | Current egress boundary stays on target origin; hosted-grade DNS, private-range, metadata, and connect-time redirect controls absent |
| Product delivery model | Confirmed current state | Self-hosted CLI and local web UI; single-tenant; .env secrets; no hosted mode, tenants, or billing |
| Web UI (one surface) | Done 2026-10-17 | `web/` is the only UI (Streamlit and the feedback dashboard were removed with the owner's OK). Plan preview, Past scans, read-only (on by default), spec location, second user, session cookie; Not tested / left-on-target shown in results. Verified in a real browser |
| Rate limits / WAF | Done 2026-10-17 | `core/throttle.py`: 429 → wait `Retry-After` (capped), retry, pace; budget (120 s wait / 40 throttled answers) → the scan stops and lists the gap; WAF block pages reported, never evaded. Gate lane: a 60 req/2 s limiter — with the layer 35 of 36 P1/P2 survive, without it 13 (23 silent misses) |
| Cleanup honesty | Done 2026-10-17 | Every write is tracked (probes, coverage tests, family sweep) and QAX's "left on the target" was checked against the app's database on microblog-api and linkding: equal. DELETE 404 = already gone; DELETE route learned from the server's OPTIONS when no spec lists it |
| Privacy: AI calls | Done 2026-10-17 | A quick scan makes zero calls to an AI provider even with a key set (a Critic leak was found and fixed; the gate counts requests to a local listener) |
| Release hygiene | Partly done | Version 0.1.0 + `--version`, run record in every scan output, CHANGELOG, SECURITY.md, QUICKSTART verified in a clean venv (27 s to 44 findings), `demo --web`. The gate now also drives the REAL command line (`demo`, credential-less `scan --out`) after a regression that broke `qax demo` went unseen by 800 tests. LICENSE intentionally not added (D12/B9: with counsel) |
| Current engineering sequence | Phase 1 engineering complete | Discovery, cookie+CSRF (`--cookie`, `--header`), 429/WAF throttle layer, class-aware truth matching, run records + versioning, plan preview/run history UI, Quickstart, 10-app corpus. Left in Phase 1: the exit-criteria items that need people (a new user completes the Quickstart unaided) and a refreshed mutation sweep |
| Phase 0 demos | Deferred (2026-10-19) | Founder decision 8: after product implementation. Requirement unchanged (2–3 QA/dev practitioners); not done |
| Functional QA track | Engineering done 2026-10-19 (uncommitted at time of writing); live Xray unverified | Decision 9. Closed gaps: PR input used local disk; pipeline `success` ignored failures; no report/results phase; results "uploaded" as Jira comments to local TC ids; `execute --suite` ran tests with no steps; `run --mode smoke/regression` was a placeholder; a UI case with skipped steps counted PASS; auto-filed Jira bugs ignored `--dry-run`; `analyze` pushed live without asking; web UI could not execute, skipped validation, never sent the API token. Added: plan stage (`--plan-only`), `--base-url`, `--live`, HTML/JSON report, Xray results import (`/import/execution`) and step fetch (GraphQL) — **Xray never exercised against a real instance**. Evidence (local, this machine): 47 focused tests pass; full suite **860 passed, 4 skipped, 0 failed**; `make eval` **GATE: PASS** (recall 32/32, 0 FP, read-only 0 writes, secure control silent, pipeline execute leg E2E). The tests caught two of my own bugs before the gate (a local id like TC-1 accepted as a Jira key; a crash with no Xray credentials). Browser engine (same day, after installing Playwright 1.44 + Chromium): verified in a REAL headless Chromium against `evals/targets/ui_demo_app.py` (one planted bug) — CLI `execute`, `tests/test_browser_engine.py`, and the web UI (Browser engine) all give the truth exactly: 3 PASS, planted bug FAIL with screenshot, unrunnable step SKIP. That run exposed and fixed: unevaluable assertions returned PASS; AI healing sent page HTML/screenshots to the AI provider by default (now opt-in `--ai-heal`, with a positive-control test). First real app (same day): linkding 1.47.0 in a real browser — `strategy/evidence/FUNCTIONAL_QA_LINKDING_2026-10-19.md`. Six QAX defects found and fixed in the order a user meets them (discovery timed out; discovery required AI; login page mislabelled; passwords in case files and reports; 5/6 false FAILs from checking too early; 5/6 false FAILs from a shared browser session). Final 6/6 PASS, 2/2 negative controls FAIL, persistence checked in the app's DB. Follow-up fixes (built-in `page` key, self-contained cases — 6/6 in reversed order too, a per-case ledger of data-changing requests, TLS verified unless `--insecure`, `.env` no longer overrides the environment); web UI reduced to two tracks (Scan · Functional QA, generation = its step 1). n = 1 app, hand-written cases. Not tested: any real Jira/Xray call; generation with a live Claude call (the founder's API credit was exhausted on 2026-10-19); a second real app |
| Functional QA alpha bar (engineering) | 6 of 8 met (2026-10-19; widened to 7 apps 2026-10-20: 45/45 PASS, 14/14 negative controls FAIL, create/edit/delete/logout in each) | `strategy/evidence/FUNCTIONAL_QA_CORPUS_2026-10-19.md`: 3 real apps (linkding, Healthchecks, PocketBase SPA), selectors from `map` only, 18/18 PASS, 6/6 negative controls FAIL, persistence checked in each app's DB, repeatable, safety rules tested. **Not met:** generation from a story with a live model (no Anthropic credit), Xray on a real instance (optional for the alpha). n = 3, hand-written cases |
| Functional QA as one E2E flow | Engineering done 2026-10-20 (branch feat/functional-qa-track) | Web: URL + repo + story → understanding → traced cases (coverage per criterion; CSV/JSON; share to Jira/Xray, dry run) → browser run → report → failures as Jira bugs (dry run unless `QAX_ALLOW_LIVE_EXPORT`). Runner reports page errors/5xx, follows new tabs, handles cookie banners and covering overlays. Verified locally: full suite 902 passed, 4 skipped; a real-browser run on `ui_demo_app` through the web UI gave 2 PASS, the planted bug + 1 negative control FAIL, 2 bugs previewed (dry run). **Not verified:** generation with a live model (no credit), any live Jira/Xray write, iframes, accessibility scanning |
| Company-review action register | Engineering items closed 2026-10-20 | `intel/COMPANY_REVIEW_2026-10-20.md` §8: 13 ✅ and 4 🟡 (bounded: Docker not built here, Java/.NET/Angular runtimes absent, one AI provider, terms are a draft). Open (people/money/external): IP review, LICENSE, AI credit, Xray instance, external pentest, unaided Quickstart, demo video, Phase-0 demos, PR gate on a real repo. Closing it found and fixed a CSRF/DNS-rebinding hole in the local web API and a false-bug path (FAIL after an unrunnable step) |
| Company-review second round | Engineering done 2026-10-20 | Register B1–B12: AI-case quality harness (waits for credit), shadow DOM, hand-recorded sessions for SSO/2FA, CI with JUnit, devices/browsers/parallel, cleanup steps, visual regression, a second AI provider (OpenAI-compatible / local), packaging, code split, competitors checked against sources (`intel/COMPETITORS_2026-10-20.md`: XBOW proves exploits at a $1B valuation and Xray now generates cases — positioning proposal for the founder). Not run here: a real CI, Firefox/WebKit, `pip install` |
| Human-dependent Phase 1 exit criteria | Deferred (2026-10-19), pending | Unaided Quickstart user, live verify-fixes write, LICENSE/IP review, Xray against a real instance, PR gate on a real repo. Requirements unchanged; not met; do not report as passed |
| Self-hosted private alpha | Planned | 2026-11-16 to 2026-12-31, gated on Phase 1 |
| Company design-partner beta | Blocked | Wait for founder’s IP/legal review, expected early 2027 |
| Hosted-mode gate | Deferred | Start only after beta evidence justifies investment |
| Minimum hosted SaaS | Not started | Build only after hosted security gate passes |
| Public launch and announcement | Target only | Provisional May–July 2027; move if any exit criterion is unmet |

### Phase 1 log (2026-10-17)
- **Dogfooding is the test that finds the real bugs.** Following the Quickstart literally on five apps surfaced more than 25 defects in QAX that 800 unit tests and the golden gate had not (four false positives among them, one a P1). Unit tests passed throughout; the fixes were verified against the apps' own databases.
- **A deliberate rule, learned the hard way:** read the gate result BEFORE committing. One commit this day was pushed with a failing gate (BOLA/persistence missed on the golden target) because the commands were chained; it was repaired in the next commit.
- **Two regressions I introduced and the gate did not see, both found by running the product as a user would:** (1) the cookie/CSRF commit broke `--out` for every scan without credentials and therefore `qax demo` (an import inside an `if`); (2) putting the operator's cookie on the test runner's session turned "anonymous request is rejected" tests into false P1s. Both fixed with regression tests; the gate now runs the real CLI end to end.
- **Real-app audits against the app's own database** corrected QAX's "left on the target" in both directions (over-reporting twins and under-reporting untracked writes and unverified deletes) until report and database matched exactly on microblog-api, linkding, CTFd, Healthchecks and PocketBase.
- **Exit criteria still open in Phase 1:** (a) a new user finishes the Quickstart unaided (needs people: Phase 0 demos and the alpha); (b) login-form authentication is not supported (documented); (c) cookie mode has one identity only, so BFLA/BOLA are NOT TESTED there (documented); (d) the mutation sweep was re-run on commit 4c2fabd: 40/40 probes DETECTED (disabling any probe loses a CONFIRMED finding on the gate), 0 ledger-only, 0 noisy.
- **Odd-origin checks (2026-10-17):** a dead port exits 1 (not a clean scan); a self-signed https target exits 1 with a hint, and works with `--insecure`; the golden app mounted under a `/myapp` prefix (behind a dispatcher with a 404 root) gives the same 34 endpoints and the same 15 confirmed findings as the root mount. Not tested: a reverse proxy that rewrites paths or redirects between hosts.
- **Idea logged, not built:** `--examples FILE` of valid request bodies, to unblock apps whose creates need domain data QAX cannot guess (CTFd).

### Decisions recorded

1. Preserve the public SaaS goal; change the order to validate self-hosted demand before hosted platform work.
2. Demonstrate QAX to 2–3 practitioners this week while continuing endpoint discovery.
3. Continue with a self-hosted alpha, using authorized staging targets and read-only as the default.
4. Require IP/legal review before company design-partner beta.
5. Make a separate hosted-mode security gate mandatory before any scan runs from QAX infrastructure.
6. Treat the current benchmark results as bounded evidence, not a universal accuracy claim.
7. Keep this document as the single roadmap and update its status/decision log at each phase gate.
8. (2026-10-19) Product completion first: human-dependent and external activities (demos, interviews, user/participant testing, outreach, lawyer/IP review, external approvals) are deferred until the engineering work is done. They keep their requirements and stay pending; no phase gate that depends on them is passed by code tests.

9. (2026-10-19) The functional QA track (generate → execute → verify → report → Jira/Xray) is part of the product and is completed in this phase: close its evidence-backed gaps (see §9 "Functional QA track").

10. (2026-10-20) Whole-company review recorded in `intel/COMPANY_REVIEW_2026-10-20.md`: GO with conditions (prove demand, clear IP). Its action register is the punch list; engineering items are closed in the product-completion phase (decision 8), people/money/external items stay pending with their requirements.

11. (2026-10-20) Full product audit (`intel/PRODUCT_AUDIT_2026-10-20.md`): a P0 (an AI-written test case could take the browser — and a stored secret — to another site) and four P1s fixed with tests; functional verdicts are now benchmarked against truth written before the run and gated. Strategic direction chosen: the change-aware proof loop (story/PR → affected flows + security checks → verdict per criterion with proof). Positioning updated to verified claims only; "every change" and "from your Jira stories" are held back until shown.

**Next action:** Finish product-completion engineering (evidence-backed defects); then run the deferred Phase 0 demos and the human-dependent Phase 1 exit criteria. Do not start hosted SaaS implementation until beta evidence justifies it and the hosted-mode security gate is approved.
