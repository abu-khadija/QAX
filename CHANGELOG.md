# Changelog

QAX follows [semantic versioning](https://semver.org/). Before 1.0, minor versions may change behaviour.
Every scan written with `--out` records the exact version and commit that produced it
(`qax_version`, `qax_commit`), and `python main.py --version` prints both.

## [Unreleased]

### Added
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
