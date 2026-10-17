# Phase 1 corpus, apps 6–10 (2026-10-17)

PLAN.md Phase 1 item 4 asks for 10 permissively licensed real applications with reviewed findings and explicit
denominators. Apps 1–5 are in `CASE_STUDY_REALWORLD_*.md` and `REALITY_CHECK_RESULTS.md`. This file adds 6–10.

**Method.** Each app was cloned outside the repo (never committed), booted locally on loopback with SQLite, given two
ordinary users (plus an admin where one exists), then scanned by following `QUICKSTART.md` literally, as a new user
would: first `--read-only` with a token, then a full two-user scan. Every confirmed finding was re-checked by hand
against the running app, and QAX's cleanup claim was compared with the database. A finding is a **true positive (TP)**
only if reproduced by hand and it matches the claimed defect. A **false positive (FP)** is a finding that was not.

**Honest limits.** Ten apps is not a benchmark. All were run locally, in dev mode, with data we created. Numbers below
are for these apps and this method only; they say nothing about general accuracy.

## Summary

| # | App | Stack / license | Auth used | Spec | Endpoints known (before → after fixes) | Final confirmed | TP | FP (final) | Left on target (QAX said → DB truth) |
|---|---|---|---|---|---|---|---|---|---|
| 6 | Healthchecks | Django, BSD-3 | `X-Api-Key` | none | 1 → 36 | 1 (P3 headers) | 1 | 0 | n/a (read-only) |
| 7 | microblog-api | Flask + apifairy, MIT | Bearer | `/apispec.json` | 2 → 27 | 3 | 3 | 0 | 13 → 13 (users; the API has no DELETE for them). DB delta +13 users, +0 posts |
| 8 | CTFd | Flask, Apache-2.0 | `Authorization: Token` (user + admin) | `/api/v1/swagger.json` | 9 → 154 | 1 (P3 headers) | 1 | 0 | 0 |
| 9 | linkding | Django REST, MIT | `Authorization: Token` | none | 6 → 18 | 1 (P3 CSP) | 1 | 0 | 10 → 10 (1 bookmark + 9 tags the API cannot delete) |
| 10 | PocketBase | Go, MIT | raw token | none | 1 → 10 (with `--endpoints`) | 2 | 2 | 0 | 0 |

**Totals for apps 6–10: 8 confirmed findings, 8 true positives, 0 false positives in the final scans.**
Apps 1–5 stay at 0 confirmed FP as reported earlier. Corpus size is now **10**.

**Bugs in QAX that these scans exposed:** more than 30, all fixed with a regression test, golden gate 32/32 throughout
(one commit briefly broke the gate; see the commit log, restored in the next commit). Of these,
**6 were false positives that would have shipped a wrong verdict** and are the most important result of this exercise:

| App | False positive QAX produced | Why | Fix |
|---|---|---|---|
| Healthchecks | P2 "Host header reflected" | A Django `DisallowedHost` 400 page echoed the request URL | Responses >= 400 are not evidence |
| microblog-api | P2 "credentials exposed" x4 | `has_password: true` is a boolean flag, not a secret | Value must be a string/number and the name must not be a flag |
| microblog-api | P1 "IDOR" on a public post | Posts are shared by design; another user may read them | Control: if B can LIST the object it is shared; writes are never excused |
| CTFd | **P1 "OS command injection"** | A Werkzeug debug page echoed the submitted `;echo TOKEN` | Strip every echo of the payload before looking for command output |
| CTFd (with `--examples`) | P1 "SQL injection" in a page title | A re-sent unique `route` raised `IntegrityError` (a 5xx page containing "sqlalchemy"); the quote was innocent (verified: `T'` and `T\"` answer 200) | Fresh unique values per request, and the benign request must still succeed right after a payload |
| CTFd (cookie mode) | P1 "POST requires authentication" x2 | The operator's cookie was on the session that sends the "anonymous" requests, so the app's 2xx looked like a missing auth check | Steps without `use_auth` go out on a cookie-less session |

## Per-app run records

Each run: QAX 0.1.0, quick scan, supplied tokens, authorized local target. Microblog's access tokens expire after
15 minutes: a scan with an expired token reports `jwt-flaws`, `bola` and others as NOT TESTED, as it should.

### 6. Healthchecks (BSD-3-Clause), Django
- Without a spec QAX knew 1 endpoint (`/admin`). Reading the app's own docs pages raised it to 36.
- Final: 1 confirmed (P3 missing security headers), TP. 0 FP. Scan read-only, ~175 requests, 0 records created.
- Fixed here: host-header FP; `jwt-flaws` told the operator to "supply a token" when an opaque API key was supplied;
  a 1-endpoint surface read as a near-clean scan (now a NOT TESTED gap).

### 7. microblog-api (MIT), Flask + apifairy
- Spec at `/apispec.json` (not a standard path): 2 endpoints known until added. New `--spec` flag for odd locations.
- Final: 3 confirmed, all TP and hand-verified: CORS reflects any origin with credentials (P2), no login throttling
  after 12 wrong passwords (P3), missing security headers (P3). 0 FP. 0 real gaps. ~780 requests for a full
  two-user scan. Cleanup audited against the database: 18 records created, 5 posts cleaned, 13 users left
  (reported), DB delta +13 users / +0 posts, an exact match.
- Fixed here: readOnly fields were sent in request bodies (HTTP 400, four probes could not run); `has_password` FP;
  public-post IDOR FP; BOLA now handles server-generated ids; valid email/url values in create bodies;
  mass-assignment: a strict-schema 400 is now recognised as the app refusing the fields (a pass), not a gap.

### 8. CTFd (Apache-2.0), Flask, session cookie + CSRF + API tokens + admin tier
- Spec at `/api/v1/swagger.json`: 9 → 154 endpoints once versioned prefixes were added.
- Read-only ordinary user: 1 confirmed (P3 headers). Full two-user scan with an admin token as user A: ~2.9k requests,
  1 confirmed (P3 headers), 0 FP after the command-injection fix. Created 0 records.
- Remaining NOT TESTED are domain validation QAX cannot guess ("Invalid link target", "a solution for this challenge
  already exists", foreign keys): BOLA/persistence/search-robustness. Logged as an idea: `--examples FILE` of valid
  bodies. Note: CTFd's `serve.py` is a development server with the Werkzeug debugger on; that is an artefact of how it
  was run, not a finding.
- With `--examples` (one valid `POST /api/v1/pages` body) BOLA and persistence run. BOLA correctly answers "shared by
  design" (another user may list every page); an `--examples` run exposed two more FPs of QAX's own (see below) and the
  cleanup audit: the registry missed 24 pages + an award per scan because CTFd answers 200 with the id under `data`,
  and a DELETE that answered 200 without a JSON content-type removed nothing and was believed. After the fixes one scan
  moves the database by exactly what QAX reports: 11 submissions left (CTFd answers HTTP 500 to deleting them), 0 pages,
  0 awards. The remaining P1 "debugger exposed on POST /pages" is true of this instance (`serve.py` is a dev server),
  and is triggered by a route value that collides after CTFd's normalisation (an unhandled 500 where a 4xx belongs).
- Fixed here: **P1 command-injection FP**; BOLA/persistence sent `"test"` to boolean fields (now the spec type);
  BOLA now says "HTTP 403: creating needs a privileged token" instead of "rejected".

### 9. linkding (MIT), Django REST Framework, no OpenAPI
- Discovery was blind to every endpoint that answers 401 anonymously. Final: 18 endpoints, all probes exercised
  (bola and persistence included), 19 records created, 19 cleaned, 0 left.
- Final: 1 confirmed (P3 missing CSP), TP. 0 FP. The only remaining gap is the honest "no spec, endpoints guessed" notice.
- Cleanup was audited against the database several times; each audit found a real discrepancy (coverage-test writes
  were untracked; slash twins double-counted; DELETE 404 read as "left behind"; identical bodies merged into one
  registry entry so some records were never cleaned) and the final state matches the database.

### 10. PocketBase (MIT), Go, no OpenAPI
- Routes (`/api/collections/<name>/records`) cannot be guessed and need superuser access to list. QAX knew 1 endpoint
  and said so. With a 9-line `--endpoints` file: 10 endpoints, 36 records created, 36 cleaned, 0 left.
- Target configured with two collections: `notes` (owner-only rules) and `posts` (readable by anyone, **deletable by
  any signed-in user**, a deliberate misconfiguration).
- Final: 2 confirmed. **TP:** P1 "DELETE /posts/{id} does not block another user" (hand-verified: HTTP 204). **TP:** P3
  headers. Correctly **not** reported: another user *reading* the public post (shared control), and everything on
  `notes`. 0 FP.

## What the numbers do and do not show
- They show the scanner stayed quiet on 4 well-built apps and found real, hand-verified issues on the rest, and that
  its false-positive controls now cover the cases found so far.
- They do **not** show a general false-positive rate (n = 10, local, one author) or recall: the only recall measured
  is on planted bugs (golden target 32/32, go-gin-vul 7/9). Real-app recall is unmeasured: no app here had a known
  bug list except PocketBase, whose one flaw we planted.
- Open weaknesses found: spec-less apps remain far harder (the surface is guessed; `--spec` / `--endpoints` exist
  for the operator); domain-validated create bodies need operator-supplied examples; cookie+CSRF writes are
  supported only for read-only scans.
