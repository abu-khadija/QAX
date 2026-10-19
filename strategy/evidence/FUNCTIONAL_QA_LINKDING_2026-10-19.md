# Functional QA on a real app — linkding 1.47.0 (2026-10-19)

**Run record (PLAN.md §6)**

| Field | Value |
|---|---|
| QAX | branch `feat/functional-qa-track` (after `3fd62d4`), Playwright 1.44 + headless Chromium |
| Target | linkding **1.47.0** (MIT), `git clone --depth 1` of `sissbruecker/linkding` at `27b7303`, Django `runserver` on 127.0.0.1:9090, SQLite, one local test user |
| Authorization | local copy, run by the operator on their own machine |
| Engine | browser (`main.py execute --project linkding`), AI healing **off**, no AI calls |
| Selectors | `main.py discover` (deterministic) on `/login/`, `/bookmarks`, `/bookmarks/new` → 72 named elements, + 3 hand-added (`error_hint`, `page_main`, `search_box`) — elements that only appear after an action cannot be discovered from a static page |
| Cases | `evals/data/linkding_cases.json` — 6 cases written by hand (generation not possible: the Anthropic credit was exhausted). Expected outcomes = linkding's behaviour, checked by hand first |
| Credentials | `{{env:QAX_VAR_LD_USER}}` / `{{env:QAX_VAR_LD_PASSWORD}}`; never in a file of QAX; 0 occurrences in the HTML report |
| Left on the target | 1 bookmark (`http://127.0.0.1:9/qax-ui-test`) — browser runs have no cleanup ledger yet |

## What broke, in the order a user meets it

| # | Symptom | Cause in QAX | Fix | Regression test |
|---|---|---|---|---|
| 1 | `discover` → 0 selectors after a 30 s timeout | waited for `networkidle`; linkding keeps a request open | wait for `load`, then ≤3 s idle | `test_discovery_works_without_ai_and_on_a_page_that_never_goes_idle` (`/live` never idles) |
| 2 | `discover` → 0 selectors again | naming the elements required Claude (no credit = nothing); it also sent the element list to the AI unasked | deterministic naming by default; Claude only with `discover --ai` | same test (AI call fails the test) + `test_deterministic_labels_…` |
| 3 | the login section held the bookmarks page | discovery auto-logged-in **on the login page itself** | no auto-login when the requested page is the login page | `test_discovering_the_login_page_records_the_login_form` |
| 4 | a case file would need the password in clear text, and the report printed every typed value | no secret indirection; fill results echoed the value | `{{env:QAX_VAR_*}}` (only that prefix — a case cannot type e.g. an API key into the app); env values and password fields shown as `••••` | `test_a_secret_comes_from_the_environment_…`, `test_only_qax_var_names_…` |
| 5 | **5 of 6 FAIL — all false** (linkding was right) | assertions ran ~ms after a click; linkding (Turbo) updates the page asynchronously | settle after a click; assertions retried up to `QAX_ASSERT_TIMEOUT_MS` (5 s) — a real failure still FAILs, with the wait noted | `test_an_assertion_waits_for_an_async_update_and_a_real_failure_still_fails` |
| 6 | after #5, 5 of 6 FAIL again — all false | all cases shared one browser session: once logged in, `/login/` redirected and the next case lost its form | a fresh browser context per case | `test_each_case_gets_a_fresh_session` |

Both new browser regressions were **mutation-checked**: with the fix disabled, exactly that test fails.

## Final result

* 6 / 6 PASS — login, wrong password message, add bookmark, empty URL not saved, search, logout.
* **Negative controls** (same flows, wrong expectation): 2 / 2 FAIL — a green run here can go red.
* **Persistence checked in linkding's database**: the bookmark exists with the typed title.
* n = 1 app, 6 cases, written by the QAX author: this shows the engine can run a real app; it is not a coverage or accuracy claim.

## Follow-up the same day (fixed, re-run)

| Open item | Fix | Check |
|---|---|---|
| Discovery sees only what is on the page now | built-in `page` key: a text check anywhere on the page (errors, toasts, results that appear after an action) | the 3 hand-added selectors were removed; 6/6 PASS with discovered selectors + `page` only |
| Order dependence on data (LD-005 needed LD-003's bookmark) | LD-005 creates its own bookmark | 6/6 PASS in normal **and reversed** order |
| No "left on the target" for browser runs | every request that can change data (not GET/HEAD/OPTIONS) is recorded per case and reported ("not undone") | linkding run: 10 writes (6× POST /login/, 3× POST /bookmarks/new, 1× POST /logout/) |
| TLS errors ignored by default | verified unless `--insecure` (web: a checkbox), like the scan | `test_tls_errors_are_not_ignored_unless_opted_in` |
| `.env` overrode the operator's environment (why the healer called the AI in the first browser run) | `.env` fills missing/empty variables only | `test_env_file_fills_gaps_but_never_overrides_…` |

## Still open

* QAX does not **undo** UI-created data — it reports what it sent; cleanup is the operator's.
* Not tested: AI-generated cases against this app (no credit), Xray, a second real app.
