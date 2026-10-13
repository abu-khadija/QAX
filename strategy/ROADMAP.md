# QAX Roadmap — the plan we walk on

Re-sequenced 2026-10-13 with the owner. Near-term goal chosen: **product/technical
hardening first** (lock quality before expanding). IP/legal track: **deferred**.

One mental model: **one platform, two capabilities** — *Scan & prove bugs* (the
wedge) and *Generate test cases* (the on-ramp), both filing to the team's tracker.
Buyers in order: owners → QA → dev. The deterministic probe layer is the trust
guarantee (0 false positives); the LLM layer adds recall but is non-deterministic,
so the Orchestrator stays deterministic around it.

**Discipline (non-negotiable):** every change is measured before it merges — the
golden target (below) + `evals/orchestrator_ab.py` must show it improved something
without breaking anything. No number, no merge.

---

## Where we are (2026-10-13)

- Scanner: ~22 deterministic probes, 0-FP, validated on 3 external stacks + a seeded
  target. All **7 orchestrator-weakness fixes done + measured** (3→5 live, evidence in
  `strategy/ORCHESTRATOR_FIXES_EVIDENCE.md`). Suite 321 passed, 3 skipped.
- Test generation: works (code/story/jira/pr → cases → Xray/Zephyr/Excel). On-ramp.
- Filing to Jira/Xray: built, DRY_RUN default, waiting on real credentials.
- UI: one-platform restructure done. CI PR-scan gate built. Pitch deck exists.
- **PR #1 open** (abu-khadija/QAX#1) — needs review/merge.

---

## Phase 1 — HARDEN (now, the chosen focus)

Goal: make quality provable and repeatable before adding surface.

- [ ] **Merge PR #1** — land the service layer + the 7 fixes.
- [ ] **Golden regression target** — move the seeded vulnerable Flask app into
      `evals/targets/` with a truth file in `evals/truth/`, so every future change
      gets a live before/after automatically (this is the "did it improve?" answer
      made permanent).
- [ ] **One-command eval** — a `make eval` / script that (a) boots the golden
      target, (b) runs quick + deep scans, (c) prints scorecard recall/FP, (d) runs
      `evals/orchestrator_ab.py`. The merge gate.
- [ ] **Full live before/after via scorecard** on the golden target for the
      deep-mode items (3 setup-repair, 5 no-router fallback, 6 forged-token-replay) —
      official recall/FP numbers, closing the "deep items are only unit-proven" gap.
- [ ] **Verify the original pipeline's `execute` leg end-to-end** — the one leg not
      yet proven E2E (code/story → cases → execute → bugs).
- [ ] **Generation polish** (only if it hurts demo quality): slowness — fewer
      cases / streaming / raise max_tokens.

## Phase 2 — EVIDENCE + JIRA (after hardening)

- [ ] **Real Jira filing E2E** when the owner's trial creds arrive — bug→Jira (Bug),
      test→Xray; set `.env` JIRA_EMAIL/JIRA_API_TOKEN/JIRA_BASE_URL/JIRA_CLOUD_ID,
      lift DRY_RUN on an opt-in flag only.
- [ ] **1–2 new OSS case studies** — vet first (boots cheaply + serves a spec, per
      the vet checklist), quick scan broadly, one budgeted deep scan.
- [ ] **Simplify "File to Jira/Xray" naming + add a tool picker** (owner-deferred UX).
- [ ] **KB → probes** — pull the top-ranked probe candidates from the knowledge base.

## Phase 3 — PACKAGING / PITCH (later, when expanding)

- [ ] **SaaS tiering** — the coverage budget (`max_endpoints`/`max_router_files`) as
      the free-vs-paid lever.
- [ ] **Competition slide** (only when actively pitching).
- [ ] Agent-team vision (Product-Owner/Acceptance agent) — Tier 3, far off.

## Deferred track — IP / legal

Time-sensitive because the owner is leaving the company, but **deferred by choice
now**. Revisit before any external distribution or fundraising. See the IP status
memory.

---

## New this session, folded into the plan
- The seeded vulnerable Flask target + `scan_dump.py` harness → becomes the Phase-1
  golden target.
- The A/B harness + scorecard → become the automated merge gate.
- Before/after evidence captured in `strategy/ORCHESTRATOR_FIXES_EVIDENCE.md`.
