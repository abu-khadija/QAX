# QAX Roadmap — the task board

**The plan:** [PLAN_TO_V1.md](PLAN_TO_V1.md) has the decisions D1–D11, the phases A–E, the exit criteria
and the dates. **This file is the board**: what is being done now, in what order, and what it is waiting on.
History up to 2026-10-16 (S1–S8, B1–B9, items 1–28): [archive/ROADMAP_HISTORY_2026-10.md](archive/ROADMAP_HISTORY_2026-10.md).

**Rules (unchanged):** one task at a time. Each one is green (suite + `make eval`) and measured before
the next. Every external write (Jira, cloning repos, anything public) needs the owner's explicit OK.

## Where we are (2026-10-16)
40 deterministic probes · the golden gate catches the death of **40/40** of them (`make eval-mutants`) ·
golden truth 32/32, 0 FP · 5 real apps scanned, 0 FP · Jira filing live · PR gate · `qax demo` ~26 s ·
accepted-findings file · test-case lint · suite 752 passed.

## Phase A — the truth on real apps · target 2026-11-01
Exit: **10 real apps with 0 confirmed FP**, every auth type measured on at least one app, the numbers
published in `strategy/evidence/`, the gate green.

| # | Task | Status | Why it is at this position |
|---|---|---|---|
| A1 | **`--read-only`** — ✅ DONE 2026-10-16: enforced at the HTTP session (only GET/HEAD/OPTIONS leave QAX), proven in the gate by counting requests on the server (0 writes); found + fixed 2 real gaps on the way: the family sweep wrote on an unguarded, untracked session, and ~150 probe-created records were never reported (LEFT ON THE TARGET said 16, the truth was 172) | ✅ | — |
| A2 | **`qax diff a.json b.json`** — ✅ DONE 2026-10-16: NEW / STILL THERE / GONE / NOT RE-CHECKED / FLAKY? / ACCEPTED, keyed on the Jira fingerprint; gone ≠ fixed unless the probe ran on a changed build; e2e vulnerable → fixed build = 40 GONE, 2 NOT RE-CHECKED (correctly) | ✅ | Small. Needed to compare re-scans during A3–A5 |
| A3 ▶ | **Auth types** — progress 2026-10-17: Healthchecks (BSD-3, X-Api-Key) scanned → fixed a host-header FALSE POSITIVE, jwt-flaws on opaque keys, a tiny-surface gap; the real blocker on spec-less apps is endpoint DISCOVERY (QAX found 1 endpoint). Also fixed a privacy bug: quick scans called the AI Critic on failed templates (gate now proves 0 provider calls). Next: discovery for spec-less apps, then cookie/CSRF.: cookie session + CSRF, login form, API key in a header. Measure on 3 OSS apps first, then build what is missing | todo | The biggest real-world risk (G2) |
| A4 | **Evidence to 10 apps** (Django / Rails / Laravel / Express / Spring / .NET; A3's apps count). Per app: confirmed, FP after a manual check, NOT TESTED ratio, scan time, request count | todo | The "0 FP" claim needs it (G1) |
| A5 | **Scale + 429/WAF**: measure time and requests; on 429 back off and record it in the ledger | todo | Measured during A4 (G3) |
| A6 | **`verify-fixes` live** on SCRUM-5/6: dry run first, write only with the OK | todo | Waits on the owner's OK for the write |
| A7 | **Release hygiene**: `pyproject.toml`, version 0.1.0, CHANGELOG, SECURITY.md, LICENSE = proprietary / all rights reserved (D12) | todo | Unblocked: an open license is decided later with counsel |

## Phase B — one clear product · target 2026-11-15
Exit: two people who have never seen QAX finish scan → review → Jira on the golden target **without asking**.

| # | Task | Status |
|---|---|---|
| B-1 | `web/` becomes the only UI. `ui/` (Streamlit) and `dashboard/` frozen, then removed **with the owner's OK** | todo |
| B-2 | Plan preview before a run: endpoints, probes, what writes data, what is missing (token_b …), then "Start" | todo |
| B-3 | Run history + diff in the UI, from the scan JSON files (no database) | todo |
| B-4 | Jira from the UI: payload preview, dedupe, write after confirmation | todo |
| B-5 | ACCEPTED and NOT TESTED shown in the UI | todo |
| B-6 | One-page Quickstart | todo |
| B-7 | The new-user test with 2 people + fixing whatever confused them | todo |

## Phases C, D, E
- **C — private alpha** (2026-11-16 → end of Dec, extended while B9 waits): 3–5 trusted people, on their own apps, `--read-only` by default.
- **D — design-partner beta** (Jan → mid-Feb 2027): **entry condition is B9 (legal) settled in writing** — the owner expects early 2027.
- **E — public** (target Mar 2027): v1.0 (≈ end Feb) + B9 + the open benchmark (Q5).

Details and exit criteria: [PLAN_TO_V1.md §5–6](PLAN_TO_V1.md).

## Waiting on the owner
| What | Blocks |
|---|---|
| B9: a lawyer on ownership + git history (EXAMPLE traces in old commits) — owner: early 2027, will say if sooner | Phases D and E |
| Each write to Jira (A6). Cloning permissive OSS apps for A3/A4 outside the repo: **approved 2026-10-16** | A6 |
| The Xray trial app | finding → regression test → Xray (Q3 step 2) |

## Background track (when a phase is waiting)
KB mining: file 02 (lifecycle), 06 (automation), 08–10, the rest of 13. Every idea from it goes through the
same filter: does it make results more accurate, clearer, the workflow easier, or the data safer (PLAN_TO_V1 §7)?
