# QAX — Roadmap (solo founder, part-time)

> Effort = calendar time for ONE person working part-time (~10–15 hrs/week).
> Every milestone has an explicit **kill criterion**. If you hit it, stop — that's the
> point of writing them down before starting.

---

## Milestone 0 — Legal / IP (do this FIRST, before any more code)

- **Goal:** know, in writing, that you own QAX and can commercialize it.
- **Deliverables:** a lawyer's review of your employment/outsourcing contract (IP
  assignment, moonlighting, non-compete); a clean-room statement of what was built on
  personal time/equipment; removal of all employer-specific artifacts (`example*`, any
  client context) into a separate private repo.
- **Success metric:** written legal opinion that you own the IP (or a clear path to).
- **Kill criterion:** **the employer owns it and won't release it → NO-GO. Stop here.**
- **Effort:** 1–3 weeks (mostly waiting on a lawyer). **Cost: a consult fee. Cheapest
  insurance you'll ever buy — a single YC/investor will kill the deal over this.**

## Milestone 1 — External validation (prove it's not just-works-on-TaskFlow)

- **Goal:** evidence QAX finds real bugs on code it has never seen, graded by someone
  who isn't you. **Define the numbers before running — no moving the goalposts.**
- **Deliverables:** run QAX (deep scan) on **3+ diverse open-source API apps** with known
  or seeded bugs; 2+ external QA/security engineers grade the output blind.
- **Success metric (set NOW):**
  - **Bug-detection:** QAX confirms **≥ 60%** of the seeded/known real bugs per repo.
  - **Trust:** **false-positive rate < 15%** of reported bugs (a security tool that cries
    wolf is dead — this matters more than detection).
  - **Beats the baseline:** finds **≥ 2 real bugs per repo that a plain coding agent
    (Claude Code / Cursor "write tests") misses** — otherwise QAX is a feature, not a
    product.
  - **Cost:** **< $2 per full scan** of a mid-size repo (with caching).
- **Kill criterion:** can't beat the coding-agent baseline, OR false-positive rate stays
  above ~25% → **PIVOT hard or NO-GO.**
- **Effort:** 3–5 weeks. Needs an eval harness (ARCHITECTURE §3.2) built first.

## Milestone 2 — Sharpen the wedge (MVP of the PIVOT)

- **Goal:** the narrowest product that's clearly differentiated — **"PR-time white-box
  security+QA that proves the bug."**
- **Deliverables:** `qax scan` on every GitHub PR → a report of **confirmed** (proven)
  security/logic bugs with evidence + repro; the model-agnostic provider layer;
  incremental per-PR model updates; the concurrency probe + frontend E2E to close the
  last planted bugs.
- **Success metric:** on a design partner's real PRs, QAX posts **≥ 1 confirmed real bug
  the team agreed was worth fixing, with < 1 false alarm per 5 PRs.**
- **Kill criterion:** design partners ignore the reports (no action taken) → the output
  isn't trusted/valuable → rethink.
- **Effort:** 6–10 weeks.

## Milestone 3 — First design partners + pricing hypothesis

- **Goal:** 2–3 teams using it on real PRs, and a price someone will pay.
- **Deliverables:** on-prem/self-host option (security buyers won't send code to your
  cloud); Jira/CI integration; a usage-based or per-repo price.
- **Pricing hypothesis [ASSUMPTION]:** land ~**$200–500/repo/month** for the
  security+proof angle (higher than a test-gen tool because proven security bugs are
  worth more than test cases). Validate against willingness-to-pay, don't assume.
- **Success metric:** 2 teams who would be "very disappointed" to lose it (Sean Ellis
  test) + at least 1 paying.
- **Kill criterion:** interest but no one pays after 2 months → it's a vitamin, not a
  painkiller.
- **Effort:** ongoing, 8–12 weeks.

## Open-source vs closed [ASSUMPTION]

- **Open-source the engines, keep the moat closed.** OSS the probe toolkit + deterministic
  templates (distribution, trust, inbound) — they're copyable anyway. Keep closed: the
  failure-mode knowledge base (your craft), the learned per-customer system models, the
  eval/benchmark, and the hosted trust layer. OSS is the funnel; the knowledge + data is
  the business.

## The one-line sequence

> **IP cleared → validated externally with pre-set numbers → narrow PR-time security
> wedge → 2 paying design partners.** Any of the first two failing = stop. Don't build
> Milestone 2+ until 0 and 1 pass.
