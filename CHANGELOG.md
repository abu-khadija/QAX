# Changelog

QAX follows [semantic versioning](https://semver.org/). Before 1.0, minor versions may change behaviour.
Every scan written with `--out` records the exact version and commit that produced it
(`qax_version`, `qax_commit`), and `python main.py --version` prints both.

## [Unreleased]

### Added
- **Functional QA track** (web: one of two tracks; CLI: `pipeline`, `execute`): generate test cases from a story or
  code — or load a cases file — review them, preview the run, run them in a real browser (Playwright) or over HTTP,
  get PASS / FAIL with step evidence and a screenshot of every failure, and send results to Xray as one Test
  Execution (Xray not yet exercised against a real instance). Plan stage (`--plan-only`), `--base-url`, an HTML/JSON
  report per run, and the data-changing requests a browser run sent ("not undone").
- `discover` names page elements without AI (`--ai` lets Claude do it); built-in `page` key for text anywhere on the
  page; `{{env:QAX_VAR_*}}` values for secrets in case files, masked in results and reports.
- `map`: read a running app from its URL (pages, SPA routes, elements, selectors checked to match one element and
  to survive a reload; risky links never followed); generation is grounded in it (web: *Read the app*).
- Assertion types `value`, `contains`, `enabled`, `disabled`, `checked`, `not_visible`; `{{unique}}` per case.
- Seeing the run: `--screenshots all`, `--trace`, `--watch` (CLI) / step screenshots, *Watch it run*, trace (web).
- `map --explore-actions`; menus/dialogs opened and recorded; sitemap.xml; the app's own login link; ARIA widgets,
  rich editors (CodeMirror/ProseMirror/Quill) and RPC/GraphQL reads understood.
- **Functional QA is one E2E flow** (web): inputs (app URL + login, repository folder or https Git URL, user
  story / acceptance criteria, code, or a cases file) → *what QAX understood* (pages, repo overview, numbered
  criteria) → test cases with requirement coverage per criterion and technique mix, downloadable as CSV/JSON or
  shared to Jira/Xray (dry run unless enabled) → run end to end → report (verdicts, criteria pass/fail, page
  problems) → the failures as ISTQB-shaped Jira bugs (dry run unless enabled), HTML report.
  `POST /api/functional/analyze`, `POST /api/functional/bugs`; `core/traceability.py`; `functional_result_to_bug()`.
- ISTQB knowledge in generation: experience-based techniques (error guessing, exploratory charters, checklists),
  white-box coverage when code is given, pairwise, test types, risk-based priority, and a required
  `Covers: AC-n | Technique: X` traceability line.
- Smarter browser runs: JavaScript exceptions, console errors, 5xx and failed requests reported per case (even on a
  PASS); a click that opens a new tab continues there; cookie banners answered with their reject option; a
  covering overlay closed (Escape) or an off-screen element scrolled into view before a click fails.

- `doctor` (machine readiness, never prints secrets), `make install` / `make web` / `make doctor` / `make docker`,
  a Dockerfile and docker-compose (published on loopback only).
- `mcp`: QAX as an MCP server for coding agents — read the app, write tests, run them, scan, preview bugs;
  same gates as the UI, never writes to Jira/Xray.
- Functional runs: elements inside iframes (`iframe#x >>> #el`); accessibility basics (WCAG) and page timings
  (TTFB, load, LCP, CLS) per page as observations; per-project run history (newly failing, fixed, flaky);
  every live-filed bug added to a regression suite.
- One story → functional **and** security cases: the security risks each criterion implies (access control,
  function-level authorization, authentication, injection, upload, business logic), with a one-click scan.
- Jira: a story key as the input (read-only); bugs linked to the story; requirement verdicts commented on it
  (dry run unless enabled); bugs and the E2E report in Arabic.
- Docs: `docs/TROUBLESHOOTING.md`, `docs/MCP.md`, QUICKSTART §4c.

- `make eval-generation`: measures the test cases an AI model writes (coverage, traceability, negatives,
  security cases, grounding) and runs them against an app with a known bug (needs AI credit).
- Web components: elements inside open shadow roots are read and used.
- `login` / 🔐 button: log in by hand once (SSO, 2FA); `map` and browser runs reuse the session.
- CI: `execute --yes --junit PATH --base-url URL`; `ci/github_action_e2e.yml`; a GitLab `qax-e2e` job.
- `--device desktop|mobile|mobile-ios|tablet`, `--browser chromium|firefox|webkit`, `--workers N`.
- `cleanup_steps` undo what a case created; `--visual` compares the end screen with a baseline (a lead).
- `QAX_AI_PROVIDER=openai`: any OpenAI-compatible API or a local model writes the test cases.
- `pyproject.toml` (`qax`, `qax-web` commands).

### Security
- The local web API refused nothing from other web pages: a page the operator visited could start scans,
  browser runs or git clones through it. Now: cross-site (Sec-Fetch-Site / Origin) requests are refused,
  writes must be JSON, only a loopback Host is served without `QAX_API_TOKEN`, security headers are set,
  `/files` serves images only, and the server will not listen beyond loopback without a token.

### Fixed
- Reading an app: a crash when a form contains `<input name="id">` (DOM clobbering, eShopOnWeb); buttons
  repeated on every table row / product card are tied to their row's title instead of their position
  (PetClinic Angular, eShopOnWeb); numeric row-index ids are not trusted; image buttons without text are read;
  a link whose text is a count is named after its address.
- Text checks include the values of visible form fields (never passwords); `{{unique_letters}}`, `{{unique_digits}}`.
- The Docker image runs Python 3.12 (the Playwright 1.44 base image shipped 3.10).
- A failure after a step that could not run (ERROR) is no longer reported as an app bug (a real failure
  after a SKIP still is).
- A FAIL that followed a step that could not run was reported as an app bug; the case is now ERROR with the reason.
- Re-reading an app no longer deletes selector names that existing test cases use.
- The login request is no longer reported as "data changed while reading the app".

### Changed
- main.py split into cli/ modules (names re-exported); the web UI's CSS and JS in their own files;
  functional-QA routes in web/functional.py.
- External writes need an explicit opt-in: `pipeline --live`; `execute` files auto-bugs only with `--upload`;
  `analyze` asks before a live push (`--yes` for CI).
- A `.env` file fills missing variables but no longer overrides one set in the environment (a `.env` with
  `DRY_RUN=false` used to override `DRY_RUN=true` from the command line or CI).
- Browser runs verify TLS certificates unless `--insecure`; AI healing / AI selector fallback only with `--ai-heal`.

### Fixed
- A case or assertion that did not run/evaluate is SKIP/ERROR, never PASS (UI executor, browser assertions).
- Browser runs on real apps (linkding): no more false FAILs from checking too early (assertions retried up to
  `QAX_ASSERT_TIMEOUT_MS`) or from cases sharing one browser session (a fresh one per case).
- Xray results are attributed to the Jira keys the export created, never to local ids.
- Browser engine on real apps (Healthchecks, PocketBase): names resolved per page and exactly (no fuzzy match onto
  another — possibly destructive — element); no page reload mid-test; app rate limiting is ERROR, not FAIL;
  clicking a disabled element fails at once with the reason.
- An API case whose steps checked nothing (no expected status/body) is SKIP, not PASS.
- Map/browser safety: never fill the operator's credentials into a non-login form with password fields (Django
  "add user"); never click a control that would submit a form while reading; framework-generated ids (React
  useId, Base UI, Radix…) never used as selectors; wait for the page to stop changing (elements + URL) before
  acting — an edit was once saved onto another row of a re-rendering list (Beszel).

- A reachability check before every scan: an address QAX cannot reach is an error with the reason (refused, DNS, timeout,
  certificate) instead of a clean-looking "0 findings" scan. `--insecure` (web: a checkbox) for a self-signed certificate.
- Web UI: extra headers, endpoint list and example bodies; the scan is streamed over a POST so tokens, cookies and CSRF
  nonces never appear in a URL (they used to be in the query string).
- `qax demo --web`: the bundled demo app and the web UI together, with the target pre-filled.
- `--examples FILE`: valid request bodies for create endpoints QAX cannot build itself (`{{unique}}` per request).
- `--header "Name: value"` (repeatable): static headers on every request, e.g. a CSRF nonce for a cookie session.
- `--spec` (OpenAPI document in an unusual place), `--endpoints FILE` (a hand-written endpoint list for apps with no
  spec), `--cookie` (session-cookie authentication, read-only scans).
- Spec-less discovery: reads the app's own docs pages, carries your credentials, understands trailing slashes, and
  learns POST schemas / item routes / DELETE from the app's own `OPTIONS` answers.
- Rate limits: HTTP 429 is waited out (`Retry-After`), retried and paced; past a budget the scan stops and says so.
  A WAF block page is reported, never evaded.
- Web UI: plan preview before a scan, run history, a read-only switch (on by default), Not-tested shown in results.
- Cleanup is tracked for every write (probes, coverage tests, family sweep) and reported against what the app really
  holds; a 404 on delete means already gone.

### Fixed
- A delete is now VERIFIED (the record is read back): a server that answers 200 without deleting (CTFd, when the
  request lacks a JSON content-type) was reported as cleaned. Creations whose id is under `data` are tracked too.
- Two more false positives found on CTFd: a duplicate-key error read as SQL injection, and an anonymous test
  authenticated by the operator's cookie read as a missing auth check.
- Four false positives found by scanning real apps: Host-header on a rejected request, a `has_password: true` flag
  reported as a credential, a public post reported as an IDOR, and **a P1 "command injection" that was a debug page
  echoing the input**.
- Probes sent read-only / wrongly typed fields in create bodies, so self-registration and BOLA/persistence silently
  could not run on strict APIs.

### Removed
- The Streamlit UI (`python main.py ui`) and the feedback dashboard (`python main.py dashboard`). `web/` is the one supported UI.
- Dead code: `core/prompt_engine.py` and two unused functions.

## [0.1.0] — 2026-10-17

First versioned release, for the self-hosted private alpha (see `strategy/PLAN.md`).
Development history since 2026-07-15 is in git; this entry describes what the release contains.

### Scanner
- 40 deterministic probes (API security, input handling, access control, data-state checks). Each one
  confirms a finding with a control request and makes no AI call.
- Endpoints come from the app's OpenAPI/Swagger document, or from common paths when there is none.
  When QAX knows fewer than 5 endpoints, it says so under NOT TESTED.
- Supplied credentials: `--token` / `--token-b`, any header (`--auth-header`) and scheme (`--auth-scheme`).
- `--read-only`: only GET/HEAD/OPTIONS leave QAX; anything that needed to write is listed under NOT TESTED.
- `--reverify N`: confirms only findings that reproduce in every pass; the rest are reported as UNSTABLE.
- A not-tested ledger: every check that could not run is reported with the reason and what would enable it.
- A left-on-the-target ledger: every test record QAX created and could not delete, grouped per collection.
- Known / by-design findings file (`.qax/accepted.json`), with a required reason and expiry date.
- `qax diff`: new / still there / gone / not re-checked / flaky / accepted, between two scans.
- A quick scan makes no call to any AI provider. A deep scan (opt-in) uses the Anthropic API.

### Workflow
- `qax demo`: a bundled vulnerable app, scanned in about 30 seconds with no key and no config.
- `file-findings`: drafts Jira bugs from a scan and files them with `--live`. Dry run is the default.
- `verify-fixes`: re-checks filed tickets (FIXED / STILL REPRODUCES / COULD NOT TEST).
- A GitHub Action PR gate (`ci/pr_scan.py`).
- Test-case generation from code, stories, Jira issues and PRs, with a deterministic quality lint
  (`lint-tests`).

### Quality gate
- `make eval`: a golden vulnerable target with 32 planted bugs and a fixed twin. It checks recall,
  0 false positives, reproducibility, a server-side check that `--read-only` sends no write, and a check
  that a quick scan makes zero AI-provider calls. Truth matching is by probe class.
- `make eval-mutants`: disables each probe in turn to prove the gate would notice it breaking.

### Known limits
- Apps without an OpenAPI/Swagger document get far less coverage.
- Login forms and cookie sessions with CSRF are not supported yet.
- Xray export is untested end to end; `verify-fixes` has not been run against real tickets yet.
- Self-hosted only: no hosted mode, accounts or multi-tenancy.
