# QAX — Pitch State (honest, numbers-backed)

*Working material for events / investors / the IP lawyer. Figures are the latest
VERIFIED per-target results, not the raw scorecard aggregate (the ledger also holds
development iterations, so its averages are not representative).*

## One line
**QAX finds and PROVES the bugs other tools miss — across any stack, API and UI —
and it doesn't cry wolf on clean code.**

## What it is
A scanner that models an app, grounds itself in the RUNNING app (OpenAPI spec or a
browser tour), then runs **12 deterministic (zero-LLM) exploit probes** + a
**browser UI tour** (console/network errors + screenshots) + **visual regression**,
and scores every run honestly (recall, false-positive %, $/run, end-to-end, and
which layer caught each). An LLM layer (red-team/critic/investigator) adds coverage
on top but the core findings are deterministic and cheap.

Probes: forge-jwt · jwt-flaws (alg=none / unverified-signature) · sqli (path) ·
injection (query+body, boolean+error) · nosql · BOLA/IDOR · excessive-data ·
malformed-input (unhandled-500 / debug exposure) · error-handling · cookie-flags ·
debug-disclosure · crash-fuzz.

## Evidence (verified)
- **Beats a generic coding agent (the go/no-go).** On the OWASP VAmPI benchmark, a
  normal "write tests" agent caught **0 of 5** security vulns as security issues
  (its functional suite passes *on* the vulnerable behaviour); QAX confirmed
  **4 of 5 deterministically, 0 false positives, ~$0.02/run.**
- **Finds real bugs in real third-party apps.** Two real open-source apps, two
  hand-verified **P1s** of a widespread class — unguarded input parsing + debug
  mode → **HTTP 500 exposing the interactive Werkzeug debugger** (source
  disclosure + console/RCE risk): a bad `service_time` in a POST body (asset
  tracker) and `?page=abc` on a menu list (restaurant API). *(Local-only testing;
  responsible disclosure is the path upstream.)*
- **Doesn't cry wolf.** On three well-written real apps (a FastAPI prompt library,
  a JWT job-tracker, a Node/TS API) QAX reported **0 confirmed bugs, 0 false
  positives** — and separates low-confidence "leads" (P3) from confirmed (P1/P2).
- **Generalises across stacks.** Grounding + probes proven on Python/Flask,
  FastAPI, and Node/TypeScript; auth via JSON, OAuth2 form, or a supplied token.
- 230 automated tests green.

## Why it's defensible (vs coding agents / commodity scanners)
Writing test cases is now commoditised (any coding agent does it). QAX's wedge is
the thing they don't do: **model the whole system → prove the exploit on the live
app → report only what's real.** That reads as security/QA assurance, not
test-authoring.

## Honest risks / what's NOT done
1. **IP is existential and must be cleared first.** Owner's account: QAX was built on
   personal time and a personal account, with no the vendor company account or
   project assets used. The repository has been swept clean of all ExampleApp/EXAMPLE
   product-specific content and real infrastructure identifiers (see
   `IP_CLEANUP_INVENTORY.md`); the engine was always generic. Still open for counsel:
   a formal ownership determination, and whether to rewrite git history (the removed
   content remains in prior commits). No incorporation/funding/sponsor until cleared.
2. **It's a strong prototype, not a product yet.** Needs a real onboarding UI (not a
   two-venv CLI), multi-tenant/reliability, and packaging.
3. **Deep-scan economics unproven end-to-end.** The deterministic + UI layers are
   proven and near-free; the full LLM deep scan + its $/run is the next thing to
   validate (in progress).
4. **Coverage gaps remain** (apps with no served spec; some auth shapes; business-
   logic/race-condition bugs).

## The ask / use of funds (once IP is clear)
Small, staged: (a) resolve IP; (b) collect more case studies on widely-used apps
(bigger headlines, via responsible disclosure); (c) prove deep-scan economics; (d)
build the product surface (point-at-your-app → report); then events / design
partners / SaaS.
