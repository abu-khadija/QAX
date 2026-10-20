# Functional QA — seven real apps and the alpha bar (2026-10-19 / 20)

The functional QA track (generate → run in a real browser → verify → report → Jira/Xray) was run on three
real, permissively licensed apps with **different UI technology**, each cloned/downloaded into
`~/Downloads/qax-corpus/` and run locally by the operator. Selectors came **only from `main.py map`**
(the app read from its URL) — none written by hand. Cases were written by hand: generation with a live
model was not possible (the Anthropic account had no credit), so the "generated from a story" leg is
**not** covered here.

## Run records (PLAN.md §6)

| App | Version | UI | Map | Cases | Result | Negative controls | Persistence checked in the app's DB |
|---|---|---|---|---|---|---|---|
| linkding (MIT) | 1.47.0 @ 27b7303 | Django + Turbo | 19 pages · 441 elements | 6 | **6/6 PASS** (also in reversed order) | 2/2 FAIL | bookmark exists with the typed title |
| Healthchecks (BSD-3) | v5.0-dev @ de5a633 | Django + modals/dropdowns | 22 pages · 692 elements | 6 | **6/6 PASS** | 2/2 FAIL | check created; project renamed |
| PocketBase (MIT) | v0.40.5 (release binary) | Svelte SPA, hash routes | 9 pages · 305 elements | 6 | **6/6 PASS**, twice in a row | 2/2 FAIL | `meta.appName` = the run's unique value |

Common to all runs: QAX at branch `feat/functional-qa-track`; Playwright 1.44 + headless Chromium;
AI healing off and no AI calls; credentials from `QAX_VAR_*` (0 occurrences in reports); every
data-changing request listed per case ("not undone"); local test data left on each app.

Selector quality, measured: after `map`, **every** selector was checked in a *new* browser session to
match exactly one element — Healthchecks 594/594, PocketBase 214/214 (later runs dropped any that did not).

## What the second and third apps broke (all fixed, each with a regression test on the local demo app)

| Found on | Symptom | Fix |
|---|---|---|
| Healthchecks | `map` spent 17 of 20 pages on `/docs` | least-visited section first; ≤6 pages per parent path |
| Healthchecks | `…_script_run_time` treated as a risky link | action verbs only at the start of a path segment |
| Healthchecks | login page missing from the map | recorded before logging in |
| Healthchecks | selectors with a record id in the href; `name`, `name_2`, `name_3` | text selector for id links; names from the element id |
| Healthchecks | **`a[href='#']`, `button:has-text('Save')` matched several elements** (a click could hit "Remove Project") | selector chosen in the page and checked to match exactly one element; Playwright re-check, ambiguous ones dropped |
| Healthchecks | "email" on /login and on /settings: last-wins picked the wrong one | names resolved per page |
| Healthchecks | fuzzy names: `page` → a "man-pages" link; `remove` could hit a destructive button | structured steps use exact names; unknown name = ERROR |
| Healthchecks | the lookup fallback reloaded the page mid-test | reads the current page instead |
| Healthchecks | the app's login rate limit turned every case into a FAIL | HTTP 429 or "too many attempts" during a failure → ERROR (throttled) |
| Healthchecks | "Log Out" lives in a closed menu | map marks hidden-until-opened elements for generation |
| Healthchecks | the page reader broke silently ("0 elements") | it raises; `map` reports an error |
| PocketBase | auto-login failed (`<button>` with no type; SPA URL changes late) | submit in the password's form, Enter fallback, success = password field gone |
| PocketBase | only 1 page read (hash links ignored) | `#/route` links are pages; risky verbs checked in the route too |
| PocketBase | per-render ids (`#accent_xleodvxk`) — unique now, gone after a reload | page reloaded; ids that changed are avoided; never used for names |
| PocketBase | settings form read before it rendered | wait until the element count is stable |
| PocketBase | `text-is` did not match text inside a child element | unique case-insensitive `has-text` |
| PocketBase | a "Rate limits" heading was taken for throttling | unambiguous phrases only |
| PocketBase | input values are not page text; `value`/`enabled`/`checked`… asserted nothing (SKIP) | schema assertion types implemented |
| PocketBase | a saved value left Save disabled on the next run (not repeatable); 15 s click timeout | `{{unique}}` per case; a disabled click fails at once, saying so |
| SPA demo | a text field's **value** (its data) used in its selector | value used only for radio/checkbox/submit |

## Round 2 (2026-10-20): four more apps, create / edit / delete / logout in every one

| App | Version | UI | Cases | Result | Negative controls | Verified in the app's DB |
|---|---|---|---|---|---|---|
| Flaskr (BSD-3) | pallets/flask @ d086db8 | Flask, server-rendered, `confirm()` dialog | 7 | **7/7** | 2/2 FAIL | deleted post gone, edited post renamed |
| microblog (MIT) | a975ef6 | Flask + WTForms/CSRF | 7 | **7/7** | 2/2 FAIL | new user registered; follow row removed after unfollow |
| Memos (MIT) | v0.31.0 | React + Base UI, Connect RPC, CodeMirror editor | 6 | **6/6** | 2/2 FAIL | deleted memo gone; archived memo `ARCHIVED` |
| Beszel (MIT) | v0.21.0 | React + Radix, realtime table | 7 | **7/7** (twice) | 2/2 FAIL | renamed in place (no duplicate), paused, deleted |

Gotify was planned but publishes no macOS build; Memos and Beszel replaced it. Final run with the final code,
all seven apps: **45/45 PASS, 14/14 negative controls FAIL**.

What round 2 broke and fixed (each fix kept for every app):

| Found on | Symptom | Fix |
|---|---|---|
| Flaskr | `map` only knew fixed login paths; printed "logged in" without logging in; recorded a 404 page | follows the app's own "Log in" link; logged in only if it really happened; 4xx/5xx pages skipped |
| Flaskr | Delete asks `confirm()` — Playwright dismisses dialogs, so delete did nothing | dialogs accepted by default (`QAX_DIALOGS=dismiss`), recorded per case |
| Flaskr | `{{unique}}` not applied inside a selector | rendered in selectors too |
| linkding (explore) | an "opener" click was a typeless `<button>` in a form → submitted it | never click a control whose effective type submits a form |
| linkding (explore) | **"am I logged out?" filled the operator's credentials into Django's "add user" form** | auto-login only on a real login form; re-login always starts from the home page |
| Memos | 166 "writes": RPC reads are POSTs | `Get…/List…/Search…/refresh/realtime` and GraphQL `query` are reads |
| Memos | selectors on React ids (`base-ui-_r_3u_`): stable on reload, shift when content changes | generated ids never used for selectors or names |
| Memos | the editor (CodeMirror contenteditable) and menu items (`role=menuitem`) were invisible to the reader | contenteditable + ARIA widgets read; rich-editor classes; exact-text XPath before positional paths |
| Memos | a positional path broke when typing changed the page | exact text / role name preferred |
| Beszel | login and home share "/" → nothing after login was read | login page kept as "/ [login]" |
| Beszel | a dialog's submit had the same text as the button that opens it | submit anchored on its own form |
| **Beszel** | **false PASS: an "edit" was saved onto ANOTHER system** (rows re-rendered between locating and clicking); the case only checked the new name | wait until the page stops changing before every action; edit cases also assert the old name is gone |
| PocketBase | a late SPA redirect after login overrode the next navigation | "settled" = element count AND URL stable, after every click |

Also new in round 2: `map` opens menus/dialogs and records "appears after clicking X"; `--explore-actions`
(follow create/edit/delete/logout links on a disposable staging app, re-login after logout, writes listed);
`sitemap.xml`; and the browser engine **shows what it does**: a screenshot after every step (in the web
results), a Playwright trace per case (`--trace`), and `--watch` (a visible, slowed-down browser).

Not covered by an automated regression: the re-render race (timing-dependent; a test would be flaky).

## Functional QA alpha bar — engineering part

| # | Criterion | Status |
|---|---|---|
| 1 | ≥ 3 real apps, different UI technology | **met — 7 apps**: Django + Turbo, Django + modals, Svelte SPA, Flask SSR ×2, React SPA ×2 |
| 2 | Selectors from `map` only, no hand-written selectors | **met** on all three |
| 3 | Final runs: 0 false PASS, 0 false FAIL; ≥ 2 negative controls per app FAIL | **met** — 45/45 PASS, 14/14 negative controls FAIL (one false PASS found and fixed on Beszel) |
| 4 | Every data-changing case verified in the app's own database | **met** |
| 5 | Repeatable: same result run twice / in another order | **met** (linkding reversed order, PocketBase twice, Healthchecks re-runs) |
| 6 | Safety: `map` never follows logout/delete/create-style links, never submits a form; browser engine never in read-only; no AI calls without opt-in; secrets never in files or reports; writes reported | **met**, each with a test |
| 7 | **Generate from a story, grounded in the map, then run** | **not met** — needs Anthropic credit (blocked) |
| 8 | Xray results on a real instance | **not met** — needs an Xray trial (optional for the alpha: PLAN keeps Xray out until a partner needs it) |
| — | A new user completes setup + first run unaided | deferred (human gate, decision 8) |

n = 7 apps, 45 hand-written cases by the QAX author: this shows the engine and the mapper work across
three kinds of UI; it is not an accuracy or coverage claim.
