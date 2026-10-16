# QAX — Product Roadmap + Go-To-Market (honest)

*Not legal/financial advice. Prices are illustrative starting points. IP must be
cleared with counsel before any public launch or funding.*

## Where QAX is today
Strong deterministic engine (12 probes + UI tour + visual regression), honest
scorecard, a runnable product UI with live streaming, proven on real apps (2 real
P1s found, 0 FP on clean apps, beats a generic agent 4/5 vs 0/5). **Known unit
economics: quick scan ~$0.02, deep ~$0.38.** This is a strong prototype — not yet a
product or a business.

## Part A — Roadmap to a real PRODUCT

### P1 · MVP (what makes it sellable) — weeks
- Deep-scan mode in the UI (engine already done) with the live feed.
- Surface UI-tour findings + **screenshots** + visual-diff in the report (the API+UI story).
- **Remediation** per finding: what it is, why it matters, how to fix (buyers need this).
- **Exportable/shareable report** (PDF + shareable link) — the thing people forward.
- Packaging: one command / Docker image / clean README / sample target.
- Robustness: timeouts, unreachable-target handling, concurrent scans.

### P2 · SaaS (the business) — 1–3 months
- Accounts + auth + **multi-tenant** (each user's scans isolated).
- Hosted backend; a **worker queue** so scans run server-side at scale.
- Scan **history, trends, projects, scheduling** (scan on every deploy / nightly).
- **Billing** (Stripe): usage-based ($/scan) and/or tiers.
- Team: invite, roles, shared reports, SSO.
- **Integrations**: GitHub/GitLab app (scan on PR, comment only *confirmed* bugs),
  Jira/Slack (QAX already has Jira/Xray adapters), CI.
- Security/compliance basics: data handling, "only scan apps you own" guardrail,
  auth to reach behind-login apps.

### P3 · Depth / moat
- More deep probes (the wedge): mass-assignment, SSRF, race conditions, business
  logic, auth-flow abuse, GraphQL.
- Better comprehension for large / .NET codebases.
- Self-expanding probe library mined from your QA knowledge base.
- Public benchmarks ("QAX vs X").

### Non-technical gates
1. **IP cleared (counsel) — before any public launch/funding.**
2. **Responsible-disclosure policy** if scanning third-party apps.

## Part B — Monetization / Go-To-Market

### Business models (pick the stack, SaaS-first)
1. **SaaS subscription (primary).** Free trial (N scans, 1 app) → Pro (unlimited quick
   + deep + CI + history) → Team/Business (multi-user, SSO, priority). Usage add-on:
   price a deep scan at ~$2–5 (cost ~$0.38 → healthy margin). Anchor pricing on your
   real unit economics.
2. **Open-core.** Free CLI / a few open probes (trust + funnel); paid = cloud, team,
   history, integrations. Strong for dev-security tools.
3. **Self-hosted / on-prem license** (annual) — security buyers who want it in their
   own infra. Higher ACV.
4. **GitHub-App / CI wedge** — "scan every PR", per-repo or per-seat; bottom-up dev adoption.
5. **Design-partner pilots** (early money + proof): 3–5 companies, small pilot fee or
   free-for-a-case-study. This is how you START.
6. **Managed assessments** (bridge cash, not scalable): run QAX as a security-QA service
   for a few clients while building SaaS. Don't become a consultancy.

### How people find you (channels)
- **Startup events / accelerators / pitch comps** — bring the live demo + scorecard
  story + a responsibly-disclosed real-bug case study. (YC, EF, Techstars; MENA:
  Flat6Labs, Plug and Play, Antler.)
- **Launch platforms**: Product Hunt, Hacker News "Show HN", Indie Hackers.
- **Security/dev communities**: r/netsec, r/webdev, r/devops — lead with substance
  (they're critical). Share the "generic agent 0/5 vs QAX 4/5" and "we don't cry wolf".
- **Content/SEO + build-in-public**: technical posts on real bugs you found (after
  disclosure), the trust angle, demos on X/LinkedIn.
- **Open a slice** (open-core) as the top of the funnel.

### Positioning (the one message)
**"Find and PROVE the bugs other tools miss — any stack, API + UI. We don't cry wolf."**
Lead with TRUST (0 FP, confirmed vs leads, evidence) — the differentiator vs noisy
scanners *and* vs generic AI. Target: security-conscious startups, dev teams, QA leads.

### The honest sequence
1. IP cleared (counsel) — gate.
2. 2–3 responsibly-disclosed real-bug case studies (headlines).
3. MVP product (deep mode, remediation, export, packaging).
4. Open-core launch (free CLI + Show HN / Product Hunt) → funnel.
5. Design-partner pilots → revenue + testimonials.
6. SaaS (accounts, billing, CI) → scale.
7. Events / fundraise **with** traction + the story.

### Reality check
MVP = weeks of focused work; SaaS = months. This is a real company build. The tech
wedge is proven; the work left is product polish + go-to-market + the IP gate.
