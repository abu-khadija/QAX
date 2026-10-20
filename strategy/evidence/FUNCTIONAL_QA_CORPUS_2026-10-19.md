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

## 2026-10-20 — Java and Angular, other browsers, packaging (founder approved the downloads)

| App | Version | UI | Map | Cases | Result | Negative controls | Persistence checked |
|---|---|---|---|---|---|---|---|
| Spring PetClinic (Apache-2.0) | main @ 500158f, Java 25 | Spring Boot + Thymeleaf, server-rendered | 4 pages · 26 elements (`/oups` 500 noted, not a page) | 7 | **7/7 PASS** first run | 2/2 FAIL | owners created, city edited, pet added (seen in the app's search) |
| eShopOnWeb (MIT) | 4da8212, .NET 8 app on the .NET 10 runtime, in-memory DB | ASP.NET Core Razor Pages + MVC, Identity login, anti-forgery tokens | 8 pages · 85 elements, logged in as the seeded demo user | 8 | **8/8 PASS** (incl. login → basket → checkout → My orders) | 2/2 FAIL | the order appears in My orders; the seeded password never in the report |
| Spring PetClinic Angular (Apache-2.0) + spring-petclinic-rest | 7c9f1ab + afc8fc1 | Angular 22 SPA (signals, OnPush) over a Java REST API | 9 pages · 166 elements (+36 from menus) | 8 | **8/8 PASS** | 2/2 FAIL | REST API: owners created; vets created and the deleted ones gone (8 = 6 + 2) |

Cases: `evals/data/petclinic_cases.json`, `evals/data/petclinic_angular_cases.json`, `evals/data/eshop_cases.json` (hand-written; selectors
from `map`, raw selectors only for rows a case creates). **Corpus now 10 apps: 68/68 PASS, 20/20 negative
controls FAIL.** Still n = 10, local, hand-written cases.

What the new apps broke in QAX (fixed, each with a regression test on the demo app):

| Found on | Symptom | Fix |
|---|---|---|
| PetClinic Angular | "Edit"/"Delete" on every table row selected by position (`tr:nth-of-type(3)`) — a row added or deleted above sends the click to another record | a row button is anchored on its row's own text (`//tr[*='James Carter']//button[='Edit Vet']`) and named after it (`edit_vet_james_carter_button`) |
| PetClinic Angular | `id="0"`, `id="1"` (row indexes) used as stable selectors and names | purely numeric ids are treated as generated |
| PetClinic Angular | names must be letters only; `{{unique}}` has digits | `{{unique_letters}}`, `{{unique_digits}}` |
| eShopOnWeb | the page reader crashed: `form.id` is the `<input name="id">` inside the form, not a string (DOM clobbering) | ids are read as attributes everywhere |
| eShopOnWeb | "[ ADD TO BASKET ]" on every product card selected by position; the basket link named `0_link` (its text is the item count) | the row rule generalised to any repeated card (`add_to_basket_roslyn_red_sheet_button`); a number-only link is named after its address (`basket_link`) |
| eShopOnWeb | the filter's image button (no text, no alt) was not read at all | image buttons are named by alt, else by their image (`arrow_right_button`) |
| PetClinic Angular | a page that shows its data in form fields ("radiology" in an input) failed a `page` text check | text checks include the values of visible fields (what the user sees) |

Other runs this day:
- **Firefox and WebKit** (Playwright 1.44 builds, installed with the founder's OK): the demo cases, a web
  component and an iframe give the same verdicts as Chromium (`test_the_same_cases_give_the_same_verdicts_in_firefox_and_webkit`).
- **`pip install -e .`** in a clean Python 3.12 venv: `qax --version`, `qax doctor` and `qax-web` work from any folder.
- **Docker** (colima): the image built and served the UI; the cross-site gate answered 403 — and the run exposed
  that the Playwright 1.44 base image ships **Python 3.10** (QAX needs 3.11+). The Dockerfile now uses
  python:3.12-slim with Chromium installed into it: rebuilt, `doctor` reports Python 3.12, and the demo cases run
  in a real browser INSIDE the container (mobile device) with the true verdicts. `docker compose config` validates.

## 2026-10-20 (evening) — the benchmark on every app, scored against truth written before the run

`make bench-functional` (evals/functional_bench.py; manifest evals/data/functional_bench.json; all apps start
with `~/Downloads/qax-corpus/start-all.sh`, logins from the local `~/Downloads/qax-corpus/.bench.env`, never
committed). Each app run twice (Beszel three times) on the same versions. Raw JSON: output/bench/.

| App | Cases | Correct | Known defects detected | False failures | Flips between runs | s/case |
|---|---|---|---|---|---|---|
| ui-demo (planted bug) | 5 | 5 | 1/1 | 0 | 0 | 5.5 |
| Spring PetClinic | 9 | 9 | 2/2 (negative controls) | 0 | 0 | 6.0 |
| PetClinic Angular | 10 | 10 | 2/2 | 0 | 0 | 4.1 |
| eShopOnWeb | 10 | 10 | 2/2 | 0 | 0 | 5.2 |
| linkding | 6 | 6 | — | 0 | 0 | 7.7 |
| Healthchecks | 6 | 6 | — | 0 | 0 | 5.3 |
| PocketBase | 6 | 6 | — | 0 | 0 | 4.2 |
| Flaskr | 7 | 7 | — | 0 | 0 | 7.4 |
| microblog | 7 | 7 | — | 0 | 0 | 6.8 |
| Memos | 6 | 6 | — | 0 | 0 | 5.8 |
| Beszel | 7 | 7 | — | 0 | 0 | 12.4 |
| **Total** | **79** | **79** | **7/7** | **0** | **0** | |

What the benchmark caught on the way (all fixed, with tests):
1. **A regression introduced earlier the same day**: the generalized card rule renamed Beszel's dialog submit
   (`add_system_button_2`) → 4 cases could not run. Rule limited to table rows and 3+ repeated cards.
2. **A false FAIL that flipped between runs** (Beszel): the table renders only visible rows; 36 systems left by
   earlier runs (no cleanup) pushed the renamed row out of the page — the rename WAS saved (checked in the DB).
   Cases now filter before asserting and delete what they create; 0 leftovers after 3 runs.
3. **False FAILs under a rate limit** (Healthchecks): the "Too many attempts" answer to a login was gone by the end
   of the case, so 3 of 6 cases FAILED instead of ERROR. The answer is now checked when it arrives; with the
   limit active all 6 are ERROR (rate-limited), none FAIL.
4. The manifest pointed PocketBase at `/_` (the cases navigate to `/_/` themselves) — a manifest error, not QAX.

Limits: 7 known defects (1 planted + 6 negative controls) — detection on real, unknown bugs is not measured here;
older apps have no negative controls in their case files; hand-written cases; one machine.
