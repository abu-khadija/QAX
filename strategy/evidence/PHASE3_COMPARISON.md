# Phase 3 — QAX vs a plain "write tests" coding agent

**Question (the go/no-go for the pivot):** if you point a normal coding agent at a
repo and say *"write a test suite for this API,"* does it find what QAX finds? If
yes, QAX is a feature. If no, QAX has a real wedge.

**Target:** VAmPI (OWASP vulnerable API, `vuln=1`), the richest ground truth we have.
Live at `http://127.0.0.1:5001`.

## Method (stated for fairness)

- **Generic agent:** I wrote a functional pytest suite the way a generalist would
  when asked only to "write tests" — happy paths + basic validation, grounded in
  the API's apparent contract. I did **not** encode knowledge of the planted vulns
  (no JWT-forgery / IDOR / injection tests), because a generalist asked to "write
  tests" wouldn't. The one security-ish test (wrong password → 401) is a standard
  functional check any dev writes. Suite + raw run: `strategy/evidence/phase3/generic_agent_vampi_suite.py`.
- **QAX:** the deterministic scan already recorded in the scorecard ledger.

## Result

Generic functional suite: **13 passed, 1 failed.**
- The single failure was `wrong password → 200 (not 401)`. On inspection it is not
  an auth bypass (no token issued; body `status:"fail"`) — it is VAmPI's
  "200-for-everything" convention. A developer would most likely **change the test
  to expect `status:"fail"`** and move on, NOT recognise the username/password
  **enumeration** vuln hiding behind it (that needs comparing two error messages —
  security reasoning the test doesn't do).
- **Every one of the 13 passing tests passes *on the vulnerable behaviour*.** The
  suite green-lights the API while it is wide open.

| VAmPI documented vuln | Generic "write tests" agent | QAX |
|---|---|---|
| JWT forgery (weak secret `random`) | missed | ✅ P1 confirmed (forge-jwt probe) |
| SQLi in `/users/v1/{username}` | missed — `get user` test passes | ✅ P1 confirmed (sqli probe) |
| BOLA (any user reads any book) | missed — `create/list books` passes | ✅ P1 confirmed (bola probe) |
| Excessive data (`/_debug` leaks passwords) | missed | ✅ P1 confirmed (excessive-data probe) |
| Mass assignment (`admin=true` on register) | missed — `register succeeds` passes | ⚠️ caught by red-team (deep/LLM, non-deterministic) |
| User/password enumeration* | symptom tripped 1 test, likely misattributed | ❌ also missed (no probe) |

*Not in QAX's truth file either — an honest gap on both sides. (VAmPI also has
RegexDoS and unauthorized-password-change, which neither side tested.)

**Score:** generic agent caught **0 of 5** planted security vulns as security
issues; QAX confirmed **4 of 5** deterministically (zero LLM, zero false positives).

## Honest reading

- For **finding vulnerabilities in an existing app** — the product thesis — QAX is
  categorically better. A generic "write tests" agent produces a passing functional
  suite that *rubber-stamps* a vulnerable API, and its one red test would likely be
  "fixed" to go fully green.
- They are **complementary, not the same tool.** The generic agent writes the
  functional/regression tests QAX does not; QAX proves the exploits the generic
  agent never thinks to try. QAX's wedge is "whole-system model → prove the exploit
  → don't cry wolf," which reads as security, not test-authoring.
- **Caveats:** one target; VAmPI is security-flavoured (plays to QAX's probes); on
  the Node business app (exapi) QAX found 0 real bugs too (it was clean) and on the
  FastAPI injection app (vfapi) QAX confirmed 1/4 + 4 leads. A generic suite on
  those would likewise pass functionally and miss the injection. The pattern holds;
  the magnitude is clearest on a vuln-rich API.

**Verdict contribution:** supports the PIVOT toward "exploit-proving / security QA,"
not "AI test-case generator." A plain coding agent already covers test authoring;
it does not cover proving the app is exploitable.
