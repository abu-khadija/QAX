# QAX Strategy — Self-Review (red-team of my own verdict)

> I attacked my own Phase 2–4 output. Below: what I assumed without evidence, what three
> skeptics would attack, and what I changed as a result.

## Assumptions I made without evidence
- **"The buyer is a security/eng lead."** [ASSUMPTION] I have zero customer conversations.
  This is a guess dressed as a persona.
- **"QAX's wedge is security."** This is the weakest link: QAX's "security" is **literally
  two probe types (JWT forgery, oversized-input crash) + IDOR/auth templates.** That is a
  *demo of a direction*, not a security product. A real security tool implies SAST/DAST
  breadth QAX does not have.
- **ROADMAP numbers (≥60% detection, <15% FP, <$2/scan).** Reasonable targets, but
  invented. No data backs them.
- **"White-box is taken by Qodo."** True that Qodo does code-level gen, but I didn't verify
  how deep its *risk/exploit* reasoning goes — I may be overstating the overlap.
- **"Knowledge graph is the moat."** I hand-waved the effort. A persistent, incremental,
  per-customer system graph is possibly a multi-month build on its own.

## What three skeptics attack
- **YC partner:** "Solo, part-time, no customers, IP in a cloud, crowded market with a
  corpse (Octomind). One sentence: what's the wedge and who has paid? You can't answer the
  second half."
- **Senior QA lead:** "Your 'security' is two probes. False-positive rate: unknown. You
  validated on one app you wrote yourself. Coverage outside templates is still luck. Come
  back with a measured bug-detection rate on code you didn't write."
- **CTO buyer:** "You want my source code — on-prem? data retention? SOC2? And my IDE's
  coding agent already writes tests for free. Why send code to a one-person tool?"

## What I changed after self-review
1. **Confidence lowered 60% → ~50%.** The security wedge is thinner than my verdict
   implied; it's a promising *direction* built on two probes, not a product.
2. **Reframed the near-term shape.** Lead with **(B) the intelligence layer** (QAX decides
   WHAT/WHERE + proves it, feeds Playwright/TesterArmy/CI) over **(A) standalone security
   product** — because calling two probes "a security product" overstates reality. The
   security angle is the *long-term* direction to earn, not the launch claim.
3. **Added a near-zero-cost reality check BEFORE Milestone 1's big build:** run the
   **free quick scan** (deterministic probes + templates, ~$0) on **3 open-source API
   repos this week** and see if it finds *anything real on code you didn't write.* If the
   deterministic path finds nothing on unfamiliar code, the whole thesis is in doubt — and
   you learn it for free, before building an eval harness or clearing IP.
4. **Re-ordered priority:** the honest first move isn't "build the knowledge graph" — it's
   **(0) clear IP** and **(0.5) the free 3-repo reality check**. Only if both look good do
   you invest in evals/architecture.

## The single most important truth
QAX has real, demonstrated capability (whole-system model + proof-grade probes + a trust
layer). But **every proof so far is on one app the founder wrote, graded by the founder,
and the full deep run has never once completed.** Until that changes, the honest status is
**"a strong prototype with a plausible wedge,"** not "a company." The pivot is right; the
confidence should stay sober.
