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
| A1 | **`--read-only`**: skip the data-mutating probes and list them as NOT TESTED (`needs: write permission`) | ▶ next | Small. Makes every scan of somebody else's staging safe (D9). Done before A3 |
| A2 | **`qax diff a.json b.json`**: new / fixed / still there / accepted, keyed on the Jira fingerprint | todo | Small. Needed to compare re-scans during A3–A5 |
| A3 | **Auth types**: cookie session + CSRF, login form, API key in a header. Measure on 3 OSS apps first, then build what is missing | todo | The biggest real-world risk (G2) |
| A4 | **Evidence to 10 apps** (Django / Rails / Laravel / Express / Spring / .NET; A3's apps count). Per app: confirmed, FP after a manual check, NOT TESTED ratio, scan time, request count | todo | The "0 FP" claim needs it (G1) |
| A5 | **Scale + 429/WAF**: measure time and requests; on 429 back off and record it in the ledger | todo | Measured during A4 (G3) |
| A6 | **`verify-fixes` live** on SCRUM-5/6: dry run first, write only with the OK | todo | Waits on the owner's OK for the write |
| A7 | **Release hygiene**: `pyproject.toml`, version 0.1.0, CHANGELOG, SECURITY.md, LICENSE | todo | The license type is the owner's call (tied to B9) |

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
- **C — private alpha** (2026-11-16 → 12-06): 3–5 trusted people, on their own apps, `--read-only` by default.
- **D — design-partner beta** (Dec → Jan): **entry condition is B9 (legal) settled in writing.**
- **E — public** (target Feb 2027): v1.0 + B9 + the open benchmark (Q5).

Details and exit criteria: [PLAN_TO_V1.md §5–6](PLAN_TO_V1.md).

## Waiting on the owner
| What | Blocks |
|---|---|
| B9: a lawyer on ownership + git history (EXAMPLE traces in old commits) | Phases D and E |
| The LICENSE type | A7 |
| OK before each clone of an external repo (A3/A4) and each write to Jira (A6) | A3, A4, A6 |
| The Xray trial app | finding → regression test → Xray (Q3 step 2) |

## Background track (when a phase is waiting)
KB mining: file 02 (lifecycle), 06 (automation), 08–10, the rest of 13. Every idea from it goes through the
same filter: does it make results more accurate, clearer, the workflow easier, or the data safer (PLAN_TO_V1 §7)?
