# Orchestrator Reliability Fixes — Before/After Evidence

The 7 orchestrator-weakness fixes (branch `feat/service-layer-and-reliability-fix`,
commits `3f8ec32`, `dabfabb`, `36f805d`, `79525e6`, `449c871`) were measured, not
asserted. The agreed discipline: *a change that can't be shown to improve things
without breaking others doesn't get merged.* This is the evidence.

Date: 2026-10-13. Full suite at the time: **321 passed, 3 skipped.**

---

## 1) Deterministic layer (probes) — same target, same scan, old code vs new

**Method.** A purpose-built vulnerable Flask target (local only) whose protected
endpoints decode the JWT **without verifying the signature** — so a forged token is
accepted everywhere. Three protected GET endpoints: `/me`, `/admin/users`,
`/billing/invoices`. The identical **quick scan** (zero-LLM, deterministic) was run
against the running target twice: once on the pre-fix commit `01d2f64`, once on the
post-fix tip `449c871`.

| | BEFORE (`01d2f64`) | AFTER (`449c871`) |
|---|---|---|
| Confirmed bugs | **3** | **5** |
| `jwt-flaws` on `/admin/users` | yes | yes |
| Forged token swept to `/me` | **missed** | **caught** |
| Forged token swept to `/billing/invoices` | **missed** | **caught** |
| Findings lost (regression) | — | **none** |
| False positives | 0 | 0 |

```
AFTER − BEFORE (new findings the fixes surfaced):
  + [P1] forged-token-replay  /billing/invoices
  + [P1] forged-token-replay  /me
BEFORE − AFTER (lost): none
```

**Result.** Before, QAX proved the JWT was forgeable on a *single* endpoint and
stopped. After, the proven flaw is swept across the whole protected surface
(item 6, `forged_token_replay_probe`): **+2 real P1 auth bypasses, nothing lost,
zero false positives.**

**Control (no false positives on a correct app).** The same target with real
signature verification turned on (`QAX_SECURE=1`): `jwt-flaws` = 0 and
`forged-token-replay` = 0 — the probes go silent on a correct server; only the
legitimate P3 (missing security headers) remains.

---

## 2) Orchestration logic — the A/B harness

`evals/orchestrator_ab.py` replays a fixed scenario (one IDOR confirmed by a
coverage **template**, one sibling suspect the app cleared in round 1, one
setup-broken suspect) through the old predicate and the new one. Zero-LLM,
zero-network, deterministic. Run it any time: `python3 -m evals.orchestrator_ab`.

| Metric | before | after | goal |
|---|---|---|---|
| Family classes expanded (recall) | 0 | 1 | higher is better |
| Round-2 suspects re-attacked (cost) | 3 | 2 | lower is better |
| Unproven findings added (FP) | 0 | 0 | must stay 0 |

**Result.** Recall up (a template/LLM-confirmed class now sweeps its family —
item 1), wasted re-attacks down (suspects the app already defended are skipped —
item 2), false positives flat.

---

## 3) Deep mode — observed live this session (API credits available)

| Item | BEFORE | AFTER (observed live) |
|---|---|---|
| **5 — no-router fallback** | "no router files → functional generation produced nothing (skipped)" | emits `no router files — generating from the endpoint surface: 2 group(s)`; coverage map records `functional:endpoints:auth = generation (from endpoints, no router file)` |
| **3 — setup repair** | a test that breaks on setup stays broken → its suspect goes unverified | a setup-broken test is rebuilt with a known-good register→login preamble and re-run — **PASS** live (`201/200/200`); in a full deep scan a repaired test confirmed a bug in round 2 |
| **4 — budget + ranking** | budget = flat file count (4); routers ranked by file name only | budget spent in endpoints (`max_endpoints`); routers ranked by Detector suspects-per-file + git churn + surface |

**Live item-3 proof (real HTTP server).**
```
ROUND 1 (naive login-without-register): status=FAIL  steps=[(1, FAIL, 401)]
flagged as setup-broken: ['RT-NAIVE']
REPAIRED steps: [(1, POST, /auth/register), (2, POST, /auth/login), (3, GET, /admin/users, use_auth=token)]
ROUND 2 (repaired): status=PASS  steps=[(1, PASS, 201), (2, PASS, 200), (3, PASS, 200)]
```

---

## Honesty / scope

- The **deterministic probe layer is the trust guarantee** (0 FP by construction);
  the before/after in §1 is reproducible to the finding.
- The **LLM Investigator layer trades determinism for recall** — in the deep scan
  on this deliberately-broken app it added broken-access / validation / rate-limit
  findings (mostly legitimate, one borderline IDOR on a global resource), and the
  confirmed count varied run-to-run (9 vs 11). That non-determinism is exactly why
  the Orchestrator stays deterministic *around* the LLM and why probes, not the LLM,
  are the trust layer.
- The size of the §2 gains depends on the target: on an app with a real IDOR family,
  item 1's expansion surfaces many more siblings than this target exercises.
- No regression anywhere: every pre-fix finding still appears post-fix.
