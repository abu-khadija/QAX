# Case study #3 — a large, mature, token-authenticated app (Gitea)

**Reality-check evidence, third app, chosen to stress what the small RealWorld
apps could not:** a production-grade service with **342 spec paths (537 grounded
operations)**, a **privilege-tiered** admin API, and **token-only auth** (no JSON
register/login flow). This is the class of app that a real buyer runs — and it
exposed three concrete QAX gaps, all found, fixed, and locked with tests, ending
at **0 false positives + 2 accurate findings**.

Date: 2026-10-15.

## Target

- **App:** Gitea 28.1.0 — self-hosted Git platform (users, orgs, repos, hooks,
  tokens, admin API). A normal production API, not a security demo.
- **License:** **MIT** (code never copied into QAX's repo — run as an external
  server; installed via Homebrew, data dir outside the repo).
- **Boot:** single Go binary on **SQLite** (`INSTALL_LOCK=true`), `127.0.0.1:3200`.
  No Docker, no DB server, no frontend build.
- **Spec:** Swagger 2.0 at `/swagger.v1.json`, `basePath: /api/v1`, global security.
- **Auth:** API tokens via `Authorization: token <sha1>` — **no JSON register/login**.
  Seeded: admin `qaxadmin`, two ordinary users `alice`/`bob`, each with a private
  repo (so the cross-user BOLA control has real objects). Verified by hand that
  Gitea is correctly secured: `alice → bob's private repo` = **404**, and every
  `/admin/*` endpoint returns **403** to an ordinary user.

## What happened

1. **Spec not discovered (first pass).** QAX found **0** endpoints and ran only the
   root header check — Gitea's spec path `/swagger.v1.json` wasn't in the discovery
   list. **Fix:** added it to `_SPEC_PATHS`. → 537 operations grounded.

2. **The authenticated coverage layer silently no-op'd (G1).** With the spec, QAX
   injected 20 coverage templates but every auth-gated one **failed at setup**: the
   templates always self-register a throwaway user, and Gitea has no JSON
   registration (`created_records: 0`). This is exactly the memory lesson — *a clean
   scan can mean auth silently failed*. The user had supplied `--token`/`--token-b`
   (the documented path for apps QAX can't register into), but the coverage layer
   ignored them. **Fix:** thread the supplied tokens through execution — pre-seed
   `token`/`token_a`/`token_b`, honor the supplied header/scheme
   (`Authorization: token …`, not `Bearer …`), and skip self-registration when
   tokens are supplied.

3. **BFLA fabricated 12 P1s off a privileged token (the serious one).** Once tokens
   flowed, the BFLA probe reported **12 P1 "ordinary user reached admin endpoint"**
   findings. Every one was a **false positive**: BFLA's `_resolve_token` returned
   the **primary** `--token` as its "ordinary user", but a primary token is
   routinely an **admin/service token** (the one you hand QAX to let it in). Admin
   hitting admin endpoints → 2xx everywhere → 12 fake P1s on a perfectly secure app.
   **Fix:** BFLA now uses a **provably-ordinary** identity only — a freshly
   self-registered user (ordinary by construction), else the documented secondary
   `token_b` (userB) — and **never** the primary supplied token. If no ordinary
   identity is available, BFLA stays silent. Verified by hand: `alice → /admin/*` is
   403, and BFLA is now correctly quiet.

4. **Inconclusive coverage tests cried wolf at the surface (G2).** Six IDOR
   templates on admin endpoints failed at their **create setup** (a trivial body
   can't create an admin hook/runner). They were correctly excluded from findings,
   but still rendered as ❌ **FAIL** — noise on a secure app. **Fix:** a coverage
   template that fails before its final assertion now renders as `[SETUP]` (◌,
   inconclusive) and does not count toward failed/pass-rate.

5. **Validation asserted on optional fields (G3).** The validation template picked
   the *first* request field as "required"; `description`/`Context` are optional →
   spurious reject-checks. **Fix:** capture `required_fields` from the spec and only
   assert on a field the spec marks required (source-only models keep the old
   first-field behaviour).

## Result (final scan — admin as primary token, alice as userB)

```
CONFIRMED (deterministic, 0-FP): 2   ·   AI leads (may vary): 0
  [P2] Host header reflected into a URL on GET /api/v1/admin/orgs   (probe)
  [P3] Response security headers missing: Content-Security-Policy    (probe)
coverage: 12 PASS · 6 SETUP · 0 FAIL        endpoints grounded: 537
```

Both findings **verified by hand against Gitea**:

- **P2 — Host-header injection:** a forged `Host: evil.invalid` makes Gitea build
  `"avatar_url":"http://evil.invalid/avatars/…"` in the response body — it reflects
  the client Host into response URLs. Real (deployment-dependent severity).
- **P3 — CSP missing:** root returns `X-Content-Type-Options` + `X-Frame-Options`
  but no `Content-Security-Policy`; QAX flagged *only* the absent header.

## Why it matters

- **Zero false positives on a large, mature, well-secured app** — after catching and
  killing a 12× P1 false-positive cascade that only a privilege-tiered, token-auth
  app would have triggered. The small RealWorld apps (single user tier, JSON
  register/login) could not have surfaced any of G1/BFLA/G2/G3.
- **The supplied-token path now actually works end-to-end** — the documented way to
  scan apps QAX can't self-register into (external IdP, token-only APIs) was silently
  broken for the coverage layer; it is now exercised and tested.
- **The CONFIRMED 0-FP discipline held throughout** — even while the probe layer was
  briefly wrong, the deterministic-template verdict never escalated a setup failure,
  and the headline findings stayed clean once the probe FP was fixed.

## Changes (all with tests)

- `services/live_surface.py` — `/swagger.v1.json` discovery; `required_fields` from
  spec (`_required_request_fields` / `_schema_required`).
- `execution/probes.py` — `bfla_probe` uses a provably-ordinary identity, never the
  primary supplied token.
- `services/coverage_goals.py` — `supplied` mode (skip self-registration, use seeded
  tokens); `_required_create_field` (assert only spec-required fields).
- `execution/api_runner.py` — supplied auth scheme/header; pre-seeded token
  variables; `[SETUP]` classification for inconclusive coverage tests.
- `services/execution_service.py` + `services/orchestrator_service.py` — thread the
  supplied `auth` into coverage generation and execution.
- Tests: `test_probes.py` (BFLA privileged-token regression), `test_coverage_goals.py`
  (supplied mode, required-field, SETUP classifier), `test_live_surface.py`
  (required_fields, Gitea spec path). Suite 417 passed; `make eval` gate PASS.
