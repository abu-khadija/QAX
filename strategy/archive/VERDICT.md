# QAX — Viability Verdict

> Blunt, committed, evidence-tagged. One position, no hedging.

## TL;DR

**VERDICT: PIVOT. Confidence: medium (60%).**
The thing you *described* QAX as — "AI that generates test cases from code" — **is a
feature, not a company**: a well-funded player (Qodo) and every coding agent already do
it. But the thing you *actually built this session* — a whole-system model that **hunts
and PROVES real security/logic exploits and refuses to cry wolf** — is a narrower, more
painful, more defensible wedge. Pivot QAX from "AI test-case generator" to **"autonomous
white-box QA + security that proves the bug."** And none of it matters until the IP
question is cleared.

---

## 1. Is there a real, painful problem buyers pay for? Who buys?

**Yes — [VERIFIED by the market].** Teams pay real money for test coverage they can't
staff: Mabl (AI-native since 2017), QA Wolf (managed, "80% coverage in 4 months"),
TesterArmy (YC P26, €1M, 30+ teams in months). The pain is real and funded.

**Who buys QAX specifically — [ASSUMPTION]:** NOT the same buyer as TesterArmy (that's a
PM/founder who wants "tests without writing them"). QAX's natural buyer is an
**engineering/security lead at an API-heavy product company** who wants to know *what's
risky and provably broken in my own code* before shipping — closer to a
pentest-on-every-PR than a click-recorder.

## 2. Where QAX wins vs the field — and where it loses

| vs | QAX wins | QAX loses |
|---|---|---|
| **TesterArmy / Mabl / QA Wolf** (black-box E2E) | Understands the code → decides WHAT to test & WHERE risk is; proves exploits (JWT forgery, IDOR) they can't reach | They own execution, mobile, polish, CI/PR UX, funding, customers, trust |
| **Qodo** (white-box, code-level test gen) | Whole-system + adversarial + security proof + trust layer, not just unit/PR tests | Qodo owns the "white-box" story, the IDE, a free tier, funding — **"white-box" is NOT your moat** |
| **Coding agents** (Cursor/Claude Code) | A dedicated trust layer (verify-before-report) + probe toolkit + knowledge base | They're free, in-editor, improving monthly, and will generate "good enough" tests for most teams |

**[VERIFIED]** Octomind (AI E2E startup) **shut down May 2026.** The space is brutal and
already has a corpse. That is a data point, not a vibe.

**Honest read:** your edge is NOT "white-box" (taken) and NOT "generate tests" (commodity).
Your only real edge is the **combination we built**: *model the whole system → find
risk → PROVE the exploit → don't cry wolf.* That reads as **security**, not QA.

## 3. Is "test design / system understanding" defensible?

**Argument it is NOT (will be absorbed in 12–24 months):** foundation models already read
code and generate tests; coding agents already sit in the repo with full context; "what
to test" is exactly the kind of judgement each model release gets better at for free. The
one-shot "system understanding" QAX does is the first thing a better base model eats.

**Argument it IS (for a while):** the defensible parts are NOT the LLM call — they're (a)
the **accumulated failure-mode knowledge base** (your real QA craft, which a generic agent
doesn't have), (b) the **probe toolkit** that turns a hunch into proof (crypto forgery,
IDOR, fuzzing — code, not prompts), (c) a **trust layer with measured false-positive
rates**, (d) **customer-specific, persistent system models + learned escaped-bug history**,
and (e) **integrations + compliance** (on-prem, Jira/CI).

**Decision:** the *generic* capability is NOT defensible — assume it commoditizes. The
*moat you can build* is **proprietary knowledge + proof tooling + per-customer learned
models + trust you can measure**. That's a data/eval/trust moat, not a tech moat. Build
for that or don't build.

## 4. Biggest risks (ranked)

1. **Legal / IP — potentially existential.** Ownership must be confirmed by counsel; without it there
   is **no company** — stop.
   This must be cleared by a lawyer **before anything else** (ROADMAP M0).
2. **[VERIFIED] Never validated outside one codebase, graded by one person.** Zero external
   evidence. Until 3+ diverse repos + outside QA engineers grade it, "it works" is opinion.
3. **[ASSUMPTION] Commoditization by base models / coding agents** within ~18 months.
4. **[VERIFIED] Cost & trust per run.** A deep run is ~9 model calls; credits ran out
   repeatedly. The trust layer still has a non-zero false-positive rate — and a security
   tool that cries wolf is dead on arrival.
5. **[VERIFIED] Crowded market with a recent death (Octomind).** Distribution, not tech,
   decides who survives — and you have none yet.
6. **[VERIFIED] Solo, part-time founder** vs YC teams and a 9-year-old incumbent.

## 5. The verdict, committed

**PIVOT**, because:
- NO-GO would be wrong — there *is* a real, differentiated wedge (proof-grade white-box
  security/QA) that the current leaders don't occupy.
- GO would be dishonest — "AI generates test cases" as pitched is a crowded feature, the
  IP is unresolved, and there is zero external validation.

**Pivot target:** *"An AI that reads your code, finds the risky/broken parts, and PROVES
the exploit — on every PR — without crying wolf."* Lead with **API/security + logic bugs**
(where you already beat the E2E tools), not with "we write your E2E tests."

**Two viable shapes (pick in M1):**
- **(A) Product:** autonomous white-box security+QA for API-heavy teams (compete narrow).
- **(B) Intelligence layer:** the "what/where to test + proof" brain that FEEDS TesterArmy/
  Playwright/CI (partner on execution; don't own the browser). Lower capital, faster.

**What would flip me to GO:** IP cleared in writing + external validation on 3+ repos
showing QAX finds real security/logic bugs that a coding agent misses, at a sane
cost/run + 2 paying design partners.
**What would flip me to NO-GO:** employer owns the IP, OR validation shows QAX finds
nothing a free coding agent doesn't.
