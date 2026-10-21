# Competitive landscape, checked against sources (2026-10-20)

*Replaces the from-memory list in COMPANY_REVIEW_2026-10-20.md §3. Searched the web on 2026-10-20.
Most sources are vendor pages, launch posts and comparison blogs — figures are **claims to verify
with the vendor**, not facts. Labels: [S] = stated by a source (linked), [I] = our inference.*

## What changed our view

1. **"We prove every finding" is not unique in security.** XBOW — autonomous web/API pentesting
   that "proves every finding with a working exploit" — raised a $120M Series C in March 2026 at a
   valuation over $1B [S][xbow-funding]. It is SaaS-only and priced per pentest (one source: from
   $6,000 per test; another: quote-only since July 2026) [S][xbow-review]. Escape raised $18M
   (March 2026) and RunSybil $40M for AI pentesting [S][xbow-alts]. Bright markets validation-driven,
   near-zero false positives [S][bright].
   → [I] QAX must not lead with "proof" alone. Its angle against them: **self-hosted, cents per run,
   in CI on every change, and the same story drives functional AND security checks** — a QA tool
   with security proof built in, not a pentest service.
2. **Writing test cases into Xray is now a built-in feature.** Xray's Standard edition includes AI
   Test Case Generation, and Advanced adds AI Test Script Generation [S][xray-mkt]; several
   Marketplace apps do the same [S][xray-apps]. Rovo can draft cases but is reported not to create or
   run them in Xray reliably [S][rovo].
   → [I] "Generate cases → Xray" is a commodity. QAX's value is what happens next: **running them
   against the real app and proving the result**, traced back to the story.
3. **Coding agents already check their own work in a browser.** GitHub's Copilot coding agent uses
   Playwright MCP to open the app and verify a change [S][ms-playwright]; Playwright ships Planner,
   Generator and Healer test agents [S][pw-agents].
   → [I] QAX's MCP server should be positioned as the **independent check** an agent calls — the one
   that traces to acceptance criteria, refuses to count a skipped step as a pass, and also probes
   security — not as "another browser tool".
4. **The closest functional competitor is Canary (YC W26).** It reads a PR's diff and source,
   runs end-to-end tests against the preview build in parallel browsers, and comments on the PR with
   recordings; early access, pricing by contact [S][canary-hn][canary-profile].
   → [I] Same direction as QAX's PR gate. QAX's differences: self-hosted, security probes, ISTQB
   traceability and requirement verdicts in Jira, honesty rules. The PR gate on a real repository
   (register U5) is now urgent, not nice-to-have.
5. **The AI E2E category is unforgiving.** Octomind shut down in mid-2026 after raising $4.8M
   [S][octomind]. Momentic: from $250/month self-serve, $18.7M raised, customers incl. Notion and
   Webflow [S][momentic]. QA Wolf: a managed service, estimates around $90K/year for a mid-size
   suite [S][momentic]. testRigor: a free public tier, private from $300/month [S][momentic].
   mabl: sales-led; sources list about $500/month entry [S][mabl].
   → [I] Price points for a Team tier of $30–60/user/month are plausible against $250–500/month
   entry tiers; validate in Phase-0 interviews.

## Updated positioning (proposal, for the founder)

> **QAX tests your running app end to end and proves what it finds — functional and security — on
> your own machines, on every change, traced to your stories in Jira.**

| Against | They do | QAX's answer |
|---|---|---|
| XBOW / Escape / Bright (AI pentest, DAST) | prove security bugs; SaaS; per-test or enterprise pricing | self-hosted, cents per run, CI on every PR, functional QA in the same flow |
| Momentic / mabl / testRigor (AI E2E) | write and run UI tests; SaaS | requirement-level verdicts, security cases from the same story, honest verdicts, self-hosted |
| Xray AI / Marketplace apps | generate cases in Jira | run them on the real app and report per criterion back to the story |
| Canary | PR-driven E2E from the code | self-hosted, security probes, ISTQB + Jira traceability |
| ContextQA Ship (launched 2026-10-05) | per-PR impact → regressions on the deployed app; bug reproduction from Jira/Linear/Slack; production-session mining; root cause + fix handed to Claude/Codex; SaaS (on-prem: Enterprise only), credits ($49–$249/mo) | self-hosted by default; security proven in the same flow; deterministic verdicts with a published method (their 72% / 94% figures have no stated method — and 72% caught means 28% missed); a test that did not run is never a pass |
| Playwright MCP / test agents | free building blocks for agents | an independent, criteria-traced check with security, exposed over MCP |

## ContextQA Ship — read 2026-10-21 (their own page, contextqa.com/ship)

**Claims:** analyzes each PR, maps impact, runs regressions on the deployed app before merge; reproduces bugs from
Linear/Jira/Slack/support desks with steps, trace and a suggested fix; mines production sessions; converts
requirement documents to tests; release quality report; integrations GitHub, GitLab, Linear, Jira, Slack, Teams,
Intercom, Zendesk, Claude, Codex; evidence = video, trace, screenshots, network traces.
**Not on the page:** API or security testing; how pass/fail is decided (AI judgement or assertions); where code and
session data are processed; how the headline numbers (72% regressions caught before merge, 94% change-to-test
coverage, 68% faster resolution, 43% lower cost) were measured. Workflow demos are labelled illustrative. Mobile:
"coming soon". Hosted; on-prem only on Enterprise.
**Where they are ahead of QAX today [V]:** live PR/CI integration and the integrations above actually connected,
bug reproduction from a ticket, production-session mining, a Chrome recorder, a polished hosted onboarding, paying
customers, ISO/SOC 2 badges.
**Where QAX is ahead [V]:** self-hosted by default (nothing leaves the machine); security bugs proven by control
requests in the same flow (32/32, 0 FP on the gate; 10 real apps); verdicts by deterministic code with a public
method — 125/125 on 15 benchmark sets, 2 real upstream bugs caught, 13/13 functional mutants; a test that did not
run is never counted as a pass; no per-step credits.
**What it means:** the change-aware proof loop (strategy §9, option 2) is now a funded, launched category — it
confirms the bet and removes "first" as a claim. QAX must win on trust (proof, self-hosted, security) and on getting
the loop live (PR comment + Jira) before the next round of launches.

**Closed the same week (2026-10-21):** a release check per change (GO / NO-GO / NOT PROVEN with a PR-ready report —
their "analyzes each PR"), import of Chrome Recorder / Playwright recordings (their Chrome recording), and a fix brief
per failure for Claude/Codex/Cursor over MCP (their agent hand-off) — each measured (see PRODUCT_AUDIT P2-2, 7, 8).
Still theirs: live integrations and customers; reproducing a bug from a ticket (needs AI credit here); production
session mining.

## Sources
- ContextQA Ship product page: https://contextqa.com/ship/ (read 2026-10-21)
- [xbow-funding] https://www.tamradar.com/funding-rounds/xbow-series-c-120m · https://www.sentinelone.com/s-ventures/blog/xbow-series-c-investment/
- [xbow-review] https://codeant.ai/blogs/xbow-features · https://stackpick.net/tools/xbow/
- [xbow-alts] https://zerothreat.ai/blog/xbow-alternatives
- [bright] https://brightsec.com/research/dast-pricing-and-cost-drivers-2026/ · https://www.stackhawk.com/blog/dast-tools/ · https://escape.tech/blog/dast-tools-buyers-guide/
- [xray-mkt] https://marketplace.atlassian.com/plugins/com.xpandit.plugins.xray/server/overview
- [xray-apps] https://marketplace.atlassian.com/apps/1235447/ai-test-copilot-for-xray · https://marketplace.atlassian.com/apps/2455956688/reqase-lite-ai-test-case-generator-for-xray
- [rovo] https://community.atlassian.com/forums/Rovo-questions/XRay-test-management-and-Rovo/qaa-p/3214538
- [ms-playwright] https://developer.microsoft.com/blog/the-complete-playwright-end-to-end-story-tools-ai-and-real-world-workflows/
- [pw-agents] https://testdino.com/blog/playwright-ai-ecosystem-2026 · https://testquality.com/?p=7943
- [canary-hn] https://hn.nuxt.dev/item/47441629
- [canary-profile] https://www.startuphub.ai/news/claudes-corner-canary-yc-w2026 · https://www.respan.ai/market-map/canary
- [octomind] https://qodex.ai/blog/octomind-alternatives · https://codenote.net/en/posts/ai-software-testing-startups-2026/
- [momentic] https://qodex.ai/blog/momentic-alternatives · https://rywalker.com/research/ai-qa-agents
- [mabl] https://toolradar.com/tools/mabl/pricing · https://www.xpay.sh/agent-ready-index/mabl/
