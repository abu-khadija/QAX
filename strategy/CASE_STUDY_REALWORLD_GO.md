# Case study #2 — a spec-less app on a different stack (Go / Gin RealWorld)

**B4 evidence, second app, deliberately a different stack AND a different situation:**
a Go service with **no OpenAPI spec** (the DRF app in case study #1 served one). The
point is to show how QAX behaves black-box when it is under-informed — and that it
still refuses to cry wolf.

Date: 2026-10-14.

## Target

- **App:** `gothinkster/golang-gin-realworld-example-app` — the official RealWorld
  "Conduit" backend in **Go + Gin + GORM** (users, JWT auth, articles, comments,
  tags, profiles). A normal business API, not a security demo.
- **License:** **MIT** (code never copied into QAX's repo — run as an external server).
- **Boot:** `go build` (cgo SQLite via GORM) → a single binary, `PORT=8085 ./server`.
  No Docker, no DB server (SQLite file, auto-migrated on start).
- **Key trait:** it serves **no OpenAPI/Swagger spec** and mounts its routes under
  `/api` (`/api/articles`, `/api/users`, `/api/profiles`, …).

## What happened

1. **No spec → thin reach (first pass).** With no spec to ground, QAX's spec-less
   discovery (B5) probed its common-path list and found **0** endpoints — the list
   didn't cover `/api/<resource>` paths. QAX enumerated nothing and ran only its
   root-level header check. Honest, but it couldn't exercise the app.

2. **General improvement.** Scanning this real app exposed that B5's common-path
   discovery was too narrow for the dominant REST convention (`/api/<noun>`). Widened
   it to probe bare and `/api/`-prefixed common resource nouns (articles, posts,
   comments, profiles, tags, …). Re-scan: QAX now **discovers 2 live endpoints**.

3. **Result (honest).**
   - **0 confirmed P1/P2**, **0 AI leads**, **0 false positives**.
   - **1 P3 lead** — missing security headers (`X-Content-Type-Options`,
     `X-Frame-Options`, CSP). Real, low-severity hygiene.
   - QAX still could not run its auth / BOLA / injection templates here, because
     **without a spec it cannot reconstruct the write + auth flow** (methods, request
     bodies, which routes are protected). That is an inherent limit of black-box,
     spec-less scanning — not a bug.

## The honest read

- QAX is **far more powerful WITH a spec** (case study #1: 22 endpoints grounded and
  a real false-positive found + fixed) than without. The product ask is therefore
  explicit: point QAX at an OpenAPI/Swagger spec, or give it the source — then it
  maps the surface and tests it properly.
- Crucially, even **under-informed on an unfamiliar stack, QAX stayed quiet and
  0-FP** — it did not manufacture findings to look busy. A security tool that invents
  bugs on an app it can barely see is worse than useless; QAX didn't.
- Follow-up on the roadmap: deepen spec-less discovery (crawl served links / infer
  methods) so black-box reach improves — but a spec/source will always win.

## Result vs case study #1

| | #1 DRF (spec) | #2 Go/Gin (spec-less) |
|---|---|---|
| Endpoints QAX saw | 22 (grounded) | 2 (discovered) |
| Confirmed P1/P2 | 0 | 0 |
| False positives | 0 (after a found+fixed cry-wolf) | 0 |
| P3 leads | 1 (no CSP) | 1 (missing headers) |

Two real, non-cherry-picked apps on two stacks: **zero false positives on both**, and
each scan drove a concrete QAX improvement (drf-yasg grounding + an auth-bypass FP
fix; wider spec-less discovery).
