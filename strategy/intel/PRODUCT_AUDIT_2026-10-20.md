# QAX — Product Audit, Strategy and Execution (2026-10-20)

*Written as the whole product company (product, QA architecture, AI, architecture, AppSec, UX,
backend, DevOps, marketing, docs, evaluation). Labels: **[V]** verified fact (test, run or file
cited) · **[O]** observation · **[H]** hypothesis · **[R]** recommendation. Nothing here is
estimated as if measured. Phases and dates stay in [PLAN.md](../PLAN.md); this is the audit and
the strategy behind the next stage. Competitor facts: [COMPETITORS_2026-10-20.md](COMPETITORS_2026-10-20.md).*

---

## 1. Executive summary

- **Where QAX stands [V]:** a self-hosted tool with two verified engines — a security scan that
  proves findings against control requests (gate: 32/32 planted bugs, 0 FP; 10 real apps) and a
  functional E2E runner in a real browser (10 real apps across Django, Flask, Svelte, React,
  Angular, Java, .NET: 68/68 correct verdicts, 20/20 negative controls failed). It never counts a
  case that did not fully run as a pass.
- **Its weakest link [V]:** the on-ramp. Writing test cases from a story/repo has **never run
  against a live model** (no credit); Jira story input, PR input, Xray and Zephyr have only ever
  been exercised with mocks.
- **The audit found and fixed a P0 [V]:** an AI-written (possibly prompt-injected) test case could
  take the browser to another site — or a cloud-metadata address — and type a stored secret into
  it, masked as •••• in the report. Fixed with an origin guard; proven by a test with a recording
  "attacker" server. Three P1s fixed with it (below).
- **The opportunity [R]:** become the **release check a team can trust** — for each change or
  story, a verdict per acceptance criterion with proof (steps, screenshots, requests), functional
  and security together, on the team's own machines, with no false bug reports. Writing tests is
  a commodity (Xray now does it); proving behaviour is not.

## 2. Current product reality

| Capability | State | Evidence |
|---|---|---|
| Security scan (deterministic probes, control requests) | ✅ verified | evals/gate.py, PLAN §9, 10 apps |
| Browser E2E execution (Chromium, Firefox, WebKit, mobile emulation, iframes, shadow DOM) | ✅ verified | tests/test_browser_engine.py (44 tests), corpus |
| Reading an app from its URL (`map`) | ✅ verified | 10-app corpus; 6 reader defects found by Java/Angular/.NET apps, fixed |
| API execution | ✅ verified | test_api_runner_egress.py, gate |
| Traceability (criteria → cases → verdicts) | ✅ deterministic part; ⚠️ the `Covers:` tag depends on the model | tests; live model not run |
| AI test generation (functional) | ⛔ **not verified** | no live run (credit); harness ready: `make eval-generation` |
| Jira bug filing | 🟡 partial | 2 live bugs (scan path); functional path dry-run only |
| Jira story input, PR input | ⛔ code only, never exercised | no test, no live run |
| Xray export/results, Zephyr | ⛔ mocked only (Zephyr Cloud path fixed today, unverified) | — |
| Excel export | ✅ now tested (was untested) | test_excel_export_writes_a_workbook… |
| AI healing | ⛔ never exercised (opt-in) | — |
| Change-based test selection | ❌ missing | risk only shapes prompts |
| Failure triage app-bug vs environment vs test | 🟡 FAIL vs ERROR (a fail after an unrunnable step is ERROR), throttling → ERROR; no environment classifier | test_executor.py |
| Flaky detection | 🟡 run history (web UI) | run_history.py |
| Evidence: screenshots, traces, page errors, a11y/perf | ✅ (HAR ❌) | tests |
| CI | 🟡 `execute --yes --junit` tested; workflow files never run on a real CI | — |
| Native mobile (Appium) | ❌ missing | emulation only |
| Packaging: pip, Docker | ✅ verified this week | pip in clean venv; image runs a browser job |
| Dead code | ⚠️ tour_service, live_tour, reporting_service, response_formatter, jira_gap_analyzer, skill_library, markdown_reporter, story/file adapters — referenced by tests or one legacy path only | capability inventory |

## 3. Critical audit findings (all P0/P1 below are FIXED, with tests)

| # | Sev | Finding | Evidence / repro | Fix | Verification |
|---|---|---|---|---|---|
| S1 | **P0** | Browser runner had no origin guard: a case could `navigate` to any site / 169.254.169.254 and `fill {{env:QAX_VAR_*}}` there; `@evil/` joined into the URL | action_mapper navigate + fill; no route guard | core/origin_guard.py: navigation and secret typing only on APP_BASE_URL's origin (+QAX_ALLOWED_ORIGINS, e.g. SSO); a context route guard blocks main-frame navigation off the app and **inspects redirects before following them** (no request leaves); steps on a foreign page are refused | test_a_test_case_cannot_take_the_browser_or_a_secret_to_another_site (recording attacker server: canary never received; no top-level visit) |
| S2 | P1 | App reader followed redirects off the app and auto-login typed the operator's password on whatever page had a password field | app_mapper landed URL unchecked; login_if_needed no origin check | off-origin pages skipped and listed; login only on the app's origin | test_the_app_reader_never_reads_or_logs_in_to_another_site |
| S3 | P1 | GET /api/scan/stream started an active scan from any localhost page (`<img src>` is "same-site", sends no Origin) | web/server.py guard | GET refused unless Sec-Fetch-Site is same-origin/none | test_a_page_on_another_localhost_port_cannot_start_a_scan_with_a_get |
| S4 | P1 | Playwright traces store typed secrets in plain text and were served | tracing.start(snapshots=True) | a case that types a `{{env:}}` secret keeps no trace; kept traces are 0600 | test_a_trace_of_a_case_that_types_a_secret_is_not_kept |
| S5 | P2 | Concurrent web jobs could swap credentials through process env | mapper set APP_* without the browser lock | the reader runs under the browser lock | web tests |
| S6 | P2 | AI provider: key sent in two headers, redirects followed, plain http allowed | ai_provider.py | one header; no redirects; https unless loopback | test_inputs_cannot_reach_secrets… |
| S7 | P2 | Repo input: hidden folders (~/.ssh) could be sent to the AI; http clones; MCP cases_file any path | functional.py, mcp_server.py | hidden folders refused, QAX_REPO_ROOTS, https only, no git redirects, workspace-only cases_file | same test |
| B1 | P1 | **The web UI's JavaScript was broken in commit 65403f7** (an apostrophe in a string) — every Python test passed | node parse | fixed + test_the_ui_javascript_parses | test |
| B2 | P1 | Zephyr Cloud users were sent to the Server v1 path | zephyr_adapter.py | Cloud v2 by default, Server via ZEPHYR_SERVER (unverified live) | payload test |
| B3 | P2 | History reader treated any JSON in the folder as a run | run_history.py | only time-named run files | overview test |
| B4 | P2 | Weak tests (`assert … or True`), stale model ids, wrong "valid commands" list | inventory | fixed | tests |
| R1 | **Risk** | Earlier corpus runs did not record app URL / start command → not reproducible | evidence doc | benchmark manifest requires both; 7 older apps marked NOT RECORDED | — |

## 4. AI intelligence assessment

**Separation that holds [V]:** reasoning (AI: understand inputs, write cases) · execution
(Playwright/HTTP, deterministic) · verification (assertions, control requests, DB/API read-back in
the corpus) · evidence (screenshots, traces, page errors, requests). No LLM ever declares a PASS,
a bug, or a vulnerability. This is the product's backbone — keep it.

**Weaknesses [V/H]:**
1. Generation quality is unmeasured (no live run) — the biggest unknown in the product.
2. Requirements understanding is regex-level for numbering criteria [V]; semantic linking relies
   on the model writing `Covers: AC-n` [V].
3. No change awareness: nothing selects tests from a diff [V].
4. No triage between app defect, environment failure and broken test beyond ERROR rules [V].
5. Healing is unexercised [V] — and must stay opt-in until it can prove it never hides a regression.

**Improvements [R], in order:** (1) run `make eval-generation` the day credit (or a local model via
QAX_AI_PROVIDER) is available and gate on it; (2) a deterministic grounding check already flags
cases using elements not on the map — make it block, not just flag; (3) change → affected pages →
cases (map records which page each case touches; a diff of the app map + the PR's routes picks the
cases); (4) triage rules from signals QAX already has (DNS/connection errors, 5xx bursts, timeouts
on navigate = environment; unknown selector = test; assertion mismatch with page loaded = app).

## 5. Missing skills, tools and integrations (the smallest set that matters)

| Add | Type | Problem solved | Cost / risk | Priority |
|---|---|---|---|---|
| Change → test selection | core engine | run what a change can break; the PR gate's whole point | medium; needs map↔route links | P2 (top) |
| Failure triage classifier (rules first) | core engine | stop wasting time on environment failures | low | P1 |
| HAR / network log per failed case | tool (Playwright record_har) | evidence for API-level root cause | low | P1 |
| Live verification of Jira story in / bugs out, Xray | integration | the promised loop | needs a Jira+Xray trial | P1 (founder) |
| axe-core | tool | full WCAG audit (basics exist) | licence review (MPL-2.0) | P2 |
| Appium | tool | native mobile | high; new runtime | P3 — not before web is sold |
| More AI agents | skill | — | adds cost and variance; no measured gap needs it | **not now** |

## 6. UI/UX assessment (walked as a first-time user, 2026-10-20)

**Found [O]:** the working app opened with a marketing hero (the tool started below the fold) and
an outdated headline ("Generate the tests"); long consent sentences were rendered in ALL CAPS; the
footer claimed "zero-false-positive" as an absolute; there was **no project view** — every session
started from scratch and functional run history was invisible; the landing page sold only the scan.
**Fixed [V]:** compact header; readable consent text; the footer states the measured result and its
sample; the landing hero and README lead with end-to-end testing + proof; a Project overview.

**Recommended journey [R]:** Project (URL + login once) → Understand (what QAX read) → Test cases
(review/export) → Run → Report (per criterion, evidence) → File bugs → next change: the overview shows
what newly fails. Open items: a first-run wizard; making the overview the landing screen of a
project; scan and functional results in one project page.

## 7. Dashboard proposal

**Built today — Project overview (every card from saved records; empty cards say how to fill them):**

| Card | Decision it supports | Data source | State |
|---|---|---|---|
| Last run | is the build OK? | run history | ✅ built |
| Since the run before | what newly broke / got fixed / flips | run history compare | ✅ |
| Pass rate, last runs | trend | run history | ✅ |
| Requirements (per criterion) | can this story ship? | last analysis + last run | ✅ |
| Regression suite | are fixed bugs staying fixed? | output/regression | ✅ |
| What QAX knows of the app | is the map current? session saved? | app_map.json | ✅ |
| Security (last scan) | any proven P1/P2? | web_runs | ✅ |

**Proposed next (with what each needs):** Release readiness (a rule over the cards above — needs
the team's own gate definition); Coverage gaps (criteria without cases — data exists, add card);
Flaky tests list (exists in history — add quarantine); Recent changes → affected cases (needs change
selection); Cost per run (needs per-run AI usage persisted — usage.py has it for scans); Evidence
completeness (cases with screenshot + steps + page state — computable). **Deliberately not built:**
"time saved" and "AI confidence" — no trustworthy measurement exists; showing them would be invented.

## 8. Market positioning

**The proposed statement** — "QAX بيختبر تطبيقك وهو شغال end to end، ويثبت اللي يلاقيه (وظيفي وأمني)،
على أجهزتك أنت، مع كل تعديل، ومربوط بالـ stories بتاعتك في Jira." — is directionally right but
**over-claims two things today**: "مع كل تعديل" (no real CI run yet) and "مربوط بالـ stories في Jira"
(story input never exercised live; functional bugs dry-run). "end to end", "يثبت", "وظيفي وأمني" and
"على أجهزتك" are verified.

| | Recommendation |
|---|---|
| First audience | product teams (5–50 engineers) shipping a web app with a staging environment and Jira, **without** a dedicated QA-automation team |
| Most painful problem | "we don't know if this release breaks a user flow or opens a hole, nobody maintains E2E tests, and scanners bury us in noise" |
| Value proposition | proof, not opinions: every result comes with the evidence that shows it, functional and security, on your machines |
| Headline (applied) | **Code review reads the diff. QAX proves what the running app does.** |
| Subheadline (applied) | Point QAX at your staging app. It runs your user flows end to end in a real browser and probes the API for security bugs — on your own machine — and reports only what it can prove: the steps, screenshots or request that show it, and a Jira bug ready to file. |
| CTAs | "Test your app end to end" · "Scan it for security bugs" · "Try the demo (no setup)" |
| Messages to test | "No false bug reports — a test that didn't run never passes" · "Your release check, with receipts" · "QA proof for teams without a QA team" |
| Claims to avoid until shown | runs on every change / every PR · writes tests from your Jira stories · Xray/Zephyr sync · zero false positives (absolute) · replaces QA engineers |
| Differentiation | vs XBOW/DAST: self-hosted, cents per run, functional too · vs Momentic/mabl: requirement-level verdicts, security, local · vs Xray AI: it runs and proves, not only writes |

## 9. Game-changer strategy

| Scenario | User problem → value | Needs | Measure |
|---|---|---|---|
| **1 Near-term** | "is this build OK?" → a trustworthy verdict per story with evidence | live generation measured; Jira story in/out live; triage rules; HAR | functional bench + gate; first real team using it weekly |
| **2 Differentiating — chosen** | "what does THIS change break?" → **the change-aware proof loop**: story/PR → affected flows + security checks → verdict per criterion with proof → bugs linked back → regression suite grows | change→test selection, CI on a real repo, PR comment, the overview as the PR's page | missed-regression rate on seeded changes; time from PR to verdict; false-bug rate 0 |
| 3 Long-term | quality as a contract: every release carries a proof bundle (verdicts, evidence, security) auditors and customers can check | signed evidence bundles, multi-tenant hosting (after the security gate) | adoption by regulated customers |

**Why scenario 2:** it uses what is already strongest and verified (deterministic execution and
proof), it attacks the real weakness of AI testing tools (noise and trust), and it is the gap
between Xray-AI (writes cases), Canary (PR-driven E2E, SaaS) and XBOW (security only, SaaS).
Alternatives rejected: "more AI test authoring" (commodity), "autonomous pentesting" (funded
incumbents), "managed QA service" (people business).

## 10. Benchmark plan

| Metric | Built? | How |
|---|---|---|
| Bug detection / missed defects / false failures / false passes | ✅ `make bench-functional` (evals/functional_bench.py) | truth = expected_outcome written before the run; never QAX's own judgement |
| Repeatability (verdict flips) | ✅ `--repeat N` | same app version, same cases |
| First-run success (tests that run) | ✅ | ERROR/SKIP share |
| Security recall / FP / reproducibility | ✅ `make eval` (gate) | golden target + secure twin |
| Generated-case quality (coverage, traceability, grounding, detection) | ✅ built, ⛔ not run | `make eval-generation` needs AI credit |
| Time per case | ✅ recorded | bench JSON |
| Cost per run | 🟡 scans only | persist AI usage per functional run |
| Investigation time, maintenance effort | ❌ | needs people: Phase-0 sessions with a stopwatch |
| Baseline comparison | ❌ | same stories, (a) a QA engineer by hand, (b) Playwright test agents / a coding agent with Playwright MCP — same app versions, same time box |

**Now in the merge gate [V]:** functional verdicts == truth on the demo app, twice, no flips.
Corpus result on record (2026-10-20): 10 apps, 68/68 correct, 20/20 negative controls failed — n=10,
hand-written cases, one machine; not a general accuracy claim.

## 11. Prioritized roadmap

- **P0 — done 2026-10-20:** S1–S4, B1 (above). Acceptance: the tests named in §3 pass in the gate run.
- **P1 — core product (next):**
  1. Measure live generation (`make eval-generation`; credit or local model) → gate on its bars. *Owner: founder (credit) + eng.*
  2. Exercise Jira story input and functional bug filing live on the trial site; Xray trial. *Founder + eng.*
  3. Failure triage rules (environment / test / app) + HAR on failure. *Eng, small.*
  4. Record app URL + start command for the 7 older corpus apps (benchmark manifest). *Eng.*
  5. First-run wizard; overview as the project's home. *Eng/UX.*
  6. Remove or wire dead modules (inventory list). *Eng.*
- **P2 — differentiation:** change → test selection; PR comment with per-criterion verdicts; flaky
  quarantine; coverage-gaps and release-readiness cards; per-run cost; axe-core after licence review.
- **P3 — later:** native mobile (Appium), exploratory charters run by an agent under the same
  verification rules, hosted multi-tenant mode (after the hosted security gate), proof bundles.

## 12. Implementation results (this session)

Code (branch feat/functional-qa-track): core/origin_guard.py; guards in action_mapper,
playwright_runner (route + redirect inspection), app_mapper, dom_analyzer; trace secrecy; GET-scan
refusal; mapper lock; AI-provider, repo and MCP hardening; Zephyr Cloud v2; core/project_overview.py
+ /api/overview + overview cards; evals/functional_bench.py + manifest + gate check; UI header,
consent text, footer, landing hero, README; fixes B1–B4. New tests: origin guard (attacker server),
reader origin, traces, GET scan, inputs hardening, Zephyr, Excel, overview, JS parse.
Full results: see the commit message and §3. **Not done:** everything marked ⛔ in §2.

## 13. Next actions

1. Founder: AI credit or a local model → `make eval-generation` (the single most important unknown).
2. Founder: Jira trial + Xray trial → live story-in / bug-out run on one corpus app.
3. Eng: triage rules + HAR (P1-3), recorded starts for the older corpus apps (P1-4).
4. Eng: change → test selection prototype on the PetClinic apps (P2-1), measured with seeded changes.
5. Founder: 3 Phase-0 sessions using the new positioning; record which message lands.
