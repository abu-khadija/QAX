# Qodo: Competitive Intelligence Audit, and What It Means for QAX

**Research date:** 2026-10-14. All Qodo pages were accessed on this date unless noted.
**Method:** I fetched the live qodo.ai pages, docs.qodo.ai (the full doc index via `llms.txt`) and the trust center, and inspected the pricing and home pages visually in a real browser (desktop plus a 375px mobile view). I checked third-party sources for history, funding, pricing context and sentiment. For QAX, I read the repository (`strategy/ROADMAP.md`, `strategy/archive/GTM_AND_ROADMAP.md`, `execution/probes.py`, the tests and the service layer) plus the project memory.
**Evidence labels used throughout:**
- **[F]** = a fact confirmed by an official Qodo source.
- **[3P]** = supported by a credible third-party source.
- **[M]** = a Qodo marketing claim that has not been independently verified.
- **[I]** = my inference.
- **[U]** = unknown.
- **[R]** = my recommendation.

Copyright note: Qodo's copy is paraphrased. I quote one headline verbatim.

---

## DELIVERABLE 1: Executive summary

**What Qodo sells today.** Qodo sells a team-level AI code review and code governance platform for pull requests. In October 2026 it is not a test-generation tool and not a coding assistant. Its main surface is a Git app that runs automatically on every PR across GitHub, GitLab, Bitbucket, Azure DevOps and Gerrit [F]. Several pieces sit around that app:
- a "context engine" that indexes the codebase, PR history and tickets [F]
- a rules system that learns standards from past reviews [F]
- an org-wide governance layer: findings analytics, a Software Map, PR Triage and audit trail [F]
- an "Agentic Toolbox" that pushes the same review engine into Claude Code, Codex and Kiro via CLI, MCP and plugins [F]

**The pivot matters most for QAX.** Qodo began in 2022–23 as CodiumAI, a tool for generating unit tests in the IDE (TestGPT) [3P]. Since then it has deliberately left test generation:
- **Qodo Cover** (the test-coverage agent) has been unmaintained since June 2025 [F, per the GitHub repo notice].
- **The IDE plugin** (formerly Qodo Gen, the home of test generation) retires on **30 Oct 2026** [F]. Qodo's docs say code-generation features are being deprecated and the focus is moving fully to review and governance [F].
- **PR-Agent** (its open-source PR bot, 13.4k stars) has been donated to a community org and is called a "legacy" project [F].

**Positioning.** The current hero headline is "Code review for agents. Governance for humans." [F] The story is that coding agents now produce code faster than humans can govern it, and Qodo is the independent quality layer inside that "software factory". The audience has moved up-market: enterprise logos (Intel, NVIDIA, Walmart, Box, monday.com), a SOC 2 Type II claim, on-prem and air-gapped deployment, BYOK, SSO and audit logs [F/M].

**Business model.**
- There is **no free tier**. You get a 14-day trial with unlimited use and no card [F].
- After that comes **Pro Team**: usage-based, **$0.012 per credit pooled across the team, not per seat**, monthly, no commitment. Packs are 2,500 credits ($30, about 18 reviews), 5,000 credits (about 36) and 20,000 credits ($240, about 144). I confirmed the $30 and $240 figures by clicking the plan cards [F].
- From those packs, one review costs about **139 credits ≈ $1.67** [I, derived from Qodo's own numbers].
- Overage is charged at the same rate up to a cap the customer sets [F].
- **Enterprise** (30+ users) is contact-sales only. It adds SSO/SAML, audit logs, BYOK, self-learning rules, advanced analytics, Gerrit, single-tenant or on-prem deployment, an SLA and a dedicated CSM [F].
- Qualifying open-source projects with 200+ stars get basic PR review for free [F].

**Strongest apparent advantages** [I]:
1. Distribution and brand. Qodo claims over 1M IDE installs [M]; the marketplace counters read 909K on VS Code and 652K on JetBrains [F as displayed].
2. Deep in-workflow integration across five Git providers.
3. Context on many repos at once: cross-repo review, Software Map, PR history.
4. An enterprise trust package: SOC 2, on-prem, BYOK.
5. A public, vendor-run benchmark that gives it a "highest F1" claim [M].
6. About $120M raised, including a $70M Series B in March 2026 [3P].

**What Qodo does not do (as far as public evidence shows):** it never *runs* the application [U→I]. All of its analysis is static, at review time, over code, diffs, tickets and history. I found no documented DAST, live-API probing, runtime exploit proof or end-to-end test execution. Qodo also no longer markets test generation.

**The headline for QAX** [R]: Qodo has left the two areas QAX works in. It dropped test generation, and it never did runtime proof. QAX should **not** compete with Qodo on code review or governance. QAX should be **the runtime half**: Qodo (and Copilot, CodeRabbit and the coding agents) review the diff, and QAX proves what the running application actually does, with reproducible evidence, filed into Jira. One caution applies, from QAX's own Phase 3 result: coding agents already commoditize test *authoring*. So the defensible part is **proof with evidence**, not "AI writes tests".

---

## DELIVERABLE 2: Website audit

### 2.1 Sitemap of pages actually inspected

| # | URL | Title / purpose | Audience | Main message (paraphrased) | Proof used | CTAs | Claims to verify |
|---|---|---|---|---|---|---|---|
| 1 | qodo.ai/ | Home: category and value proposition | Eng leaders, then developers | Agents ship code fast; Qodo is the quality and governance layer in the "factory" | Logo strip (8 enterprises), 8 named testimonials, NVIDIA/Jensen Huang video, marketplace install counters, live-looking Software Map visual, security badges | Book a demo (primary), Start trial | "~1 hr saved per PR", "90% of initial review", "highest F1" |
| 2 | qodo.ai/pricing/ | Pricing | Team buyers | Credits pooled per team, switch packs anytime, no annual lock-in | Git provider icons, plan comparison, 11-item FAQ | Start now, Book a demo, See additional plan options | — (confirmed in the UI) |
| 3 | qodo.ai/solutions/enterprise/ | Enterprise | VP Eng / CTO / security | Coding agents outrun your ability to govern them | Stats 51.3% / 441% / 54% (asterisks, **no visible footnote**), "450K+ dev hours saved/yr", ratings 4.7 VS Code / 4.5 JetBrains, model logos | Request a 30-min executive demo | All three asterisked stats; 450K hours |
| 4 | qodo.ai/features/qodo-git/ | Multi-agent PR review | Eng managers, devs | Review that finds real issues rather than noise | Screenshots, benchmark link, anatomy-of-a-finding slider | Start trial for free, Book a demo | Benchmark superiority |
| 5 | qodo.ai/about/ | Company | Investors, recruits, buyers | Mission: make code integrity simple | Board list, investor logos | Careers | Founding history not given on the page |
| 6 | qodo.ai/ai-code-review-benchmark/ | Benchmark | Evaluators | Highest F1 among the tools evaluated | Charts (images), dataset on GitHub and Hugging Face | — | Vendor-run; competitors not named in the page text |
| 7 | qodo.ai/book-a-demo/ | Demo funnel | Enterprise | 3 promises: earlier issues, self-learning rules, visibility | Logos, install counters | Form (not submitted) | — |
| 8 | qodo.ai/blog/introducing-qodo-a-new-name… (30 Sep 2024) | Rebrand post | Market | CodiumAI becomes Qodo, a "code integrity platform" | — | — | — |
| 9 | docs.qodo.ai/ + llms.txt | Documentation hub (≈100 pages indexed) | Admins, devs | Get started → Git review → Toolbox → Governance → Portal → On-prem | — | — | — |
| 10 | docs …/whats-new | Release history 2.0 → 3.0 | Customers | Dated changelog | — | — | — |
| 11 | docs …/core-concepts/qodo-platform-architecture | Architecture | Technical evaluators | Ingest agents, a knowledge layer (graph DB, vector DB, PR history, generated .md), research agents, a judge, review agents | Diagram | — | — |
| 12 | docs …/code-review/overview, /comment-anatomy, /use-qodo-in-prs, /dismiss-a-finding | Review mechanics | Devs | (see Deliverable 3) | Screenshots | — | — |
| 13 | docs …/governance/rule-enforcement/rule-miner | Rule Miner | Leads / admins | Mines rules from accepted review comments | — | — | — |
| 14 | docs …/agentic-toolbox/agentic-toolbox-overview | Toolbox | Devs using coding agents | Qodo inside your agent | — | — | — |
| 15 | docs …/pricing-and-usage | Billing model | Admins | Usage-based, workspace credits, monthly renewal | — | — | Credit cost per feature not published |
| 16 | docs …/deployment-models, /on-prem | Deployment | Platform / security | Multi-tenant / single-tenant VPC / on-prem on Kubernetes | — | — | Hardware and LLM requirements not on these pages |
| 17 | docs …/install-qodo/git-provider-feature-support | Provider matrix | Admins | Feature parity per provider | Table | — | — |
| 18 | docs …/integrations/ticketing-integrations | Ticket context | Leads | Reads Jira / Linear / ADO / GitHub Issues / Monday | — | — | Write-back not documented |
| 19 | docs …/open-source-program | OSS program | OSS maintainers | Free basic review for repos with 200+ stars | — | Install app | — |
| 20 | docs …/qodo-ide | IDE plugin | Devs | **Retiring 30 Oct 2026** | — | Move to the Toolbox | — |
| 21 | trust.qodo.ai | Trust center (published 17 Jun 2026) | Security reviewers | SOC 2. Documents available on request (gated) | — | Request access | Subprocessors not public |
| 22 | github.com/qodo-ai/pr-agent | Open-source legacy bot | Devs | Community-maintained, MIT, being donated to a foundation | 13.4k stars | — | — |
| — | g2.com/products/qodo/reviews | Reviews | — | **Inaccessible (HTTP 403)** | — | — | — |
| — | Book-a-demo form fields, post-submit flow | — | — | **Not inspected** (I submitted nothing) | — | — | — |
| — | Signed-in app (app.qodo.ai) | — | — | **Not inspected** (no account created, as instructed) | — | — | — |

### 2.2 Homepage, section by section

| # | What the visitor sees | Question it answers | Action it pushes | Why it sits here [I] | Improvement / lesson for QAX |
|---|---|---|---|---|---|
| 0 | A purple announcement bar for Qodo 3.0, a sticky dark nav, two CTAs | "Is this product alive?" | See what shipped | Release momentum is social proof | QAX: show a dated changelog. A recent date builds trust |
| 1 | Hero: two-line headline (white line, then purple line), one-sentence subheadline, **Book a demo** (filled) next to **Start trial** (outline), and a perspective-grid background | "What is this, and who is it for?" | Demo first | Demo is the primary CTA, which tells you the business is sales-led and enterprise | QAX should lead with **self-serve try**: its buyers are smaller and its proof can be shown in minutes |
| 2 | A large interactive **Software Map**: a clustered graph showing "8 clusters · 2,733 repos", filter panel, severity counts, "graph updated 4 min ago" | "Can it handle *my* scale?" | Keep scrolling | Signals enterprise scale; demo data presented like a live product | This section **traps the scroll wheel** (scrolling over it doesn't move the page) and was empty on first paint on mobile. It is impressive but has a usability cost. QAX: show a *real* finding with evidence instead of a scale visualization |
| 3 | 4 cards: ground agents, enforce standards, independent review in the loop, govern across the factory | "How does it fit the agentic world?" | — | Category framing before features | — |
| 4 | "High-precision review on every change": PR review, shift-left skills, local review | "What does it actually do every day?" | — | Moves from the category to the core job | — |
| 5 | Deterministic, enforceable standards: wikis become rules, enforcement tracking, rule health | "Will it follow *our* conventions?" | — | Answers the most common objection to AI review (generic advice) | Interesting: Qodo uses the word **"deterministic"**, which is QAX's own trust word. Expect the term to get crowded |
| 6 | Governance layer: risk intelligence, audit trail, repo map, system of record | "What does my VP get?" | — | Speaks to the economic buyer | — |
| 7 | Context engineering, NVIDIA video and case-study link; four context sources | "Why is it smarter than Copilot?" | See the case study | Borrowed authority (NVIDIA / Jensen Huang) | QAX can't borrow authority yet. Use **reproducible evidence** as its authority |
| 8 | 8 testimonials with names, titles and companies (TUI, HiBob, Pax8, monday, Slickdeals, Macmillan, LoopUp) | "Who else trusts it?" | — | Peer proof right before the "how it works" | Named people plus titles carry far more weight than logos alone |
| 9 | "How Qodo works" tabs: PR Review / Review Standards | "What will I see?" | — | Concrete product UI after the abstract story | — |
| 10 | 3 recent blog posts (dated 5–12 Oct 2026) | "Are they thought leaders?" | Read | SEO plus freshness | — |
| 11 | Security block: zero data retention, SOC 2 Type II, on-prem, single-tenant | "Will legal/security block this?" | — | Removes the last enterprise objection just before the FAQ | QAX: "local-first, runs on your machine, your API key" is a real and cheap version of this. **Do not** claim certifications |
| 12 | FAQ: what it is, why review breaks with AI, differentiation, standards, cross-repo, ROI, AI-generated code, integrations, **pricing in the FAQ**, security | Long-tail objections plus SEO | Links into Academy and docs | — | Putting pricing in the homepage FAQ is a good pattern (it removes a click) |
| 13 | Footer: product, resources, a "Learn" cluster of **Qodo & Copilot / Cursor / Windsurf / Amazon Q / Tabnine** pages, glossary, academy, legal (Terms / Privacy / DPA) | SEO capture of competitor searches | — | Comparison pages capture people searching for "X alternative" | QAX: later, "QAX + Qodo / CodeRabbit / Snyk" pages that frame QAX as complementary |

### 2.3 Visual design system (observed)

Measured from the page's computed styles:
- **Theme:** dark-only. The background is near-black (`rgb(17,16,19)`), body text is light grey and headings are white.
- **Accent:** a single violet/purple, used for the second headline line, the primary buttons, the selected card outline and the logo mark. A **mint green** accent appears on the pricing subheadline. Feature cards use pastel blocks (white, violet, mint, lime).
- **Type:** a two-font system.
  - **Figtree**, a humanist sans, for headings and body. H1 is 72px/500 and H2 is 52px/500; the restrained weight reads as calm and premium.
  - **IBM Plex Mono** in **uppercase, letter-spaced 12px** for buttons, nav and labels. This signals "engineering tool".
- **Shapes:** thin 1px outline borders, mostly flat (little shadow), rounded-rectangle cards, a recurring **perspective grid / wireframe** motif.
- **Media:** 88 images and 6 video/iframe embeds on the homepage. Product UI is shown inside framed dark panels.
- **Motion:** an interactive force-graph and scroll-pinned sections.
- **Mascot:** a cartoon character in the announcement bar softens the enterprise tone.
- **Mobile (375px):** no horizontal overflow, the CTAs stack full-width and a hamburger menu appears. The hero visual was blank on first paint (probably lazy-loaded).

**Trade-offs** [I]:
- Dark mode with mono-uppercase UI labels is now the default "devtool" look (Linear, Vercel, Warp). It signals belonging but doesn't differentiate.
- Heavy interactive visuals impress executives in demos but cost performance and scrolling usability.
- Two typefaces with one accent keep things consistent across pages; the pricing, enterprise and feature pages all share the system.

### 2.4 Copy and positioning (Phase 6)

**Messaging hierarchy** [I, built from the pages]:

| Level | Qodo's message (paraphrased) |
|---|---|
| Category | Quality control / governance for the *agentic* software factory (new category framing in 3.0) |
| Company | The independent layer that keeps quality and standards intact as AI agents write more code |
| Product | Multi-agent PR review plus self-learning rules plus a governance dashboard |
| Feature | Severity-first findings, agent-ready fix prompts, requirement validation against tickets, cross-repo breaking-change detection |
| Proof | Enterprise logos, named testimonials, NVIDIA case study, self-run benchmark (F1), install counts, SOC 2 |
| Conversion | Executive demo (enterprise), 14-day no-card trial (teams) |

**How they turn technical features into business value:** every capability is phrased as an answer to a manager's worry:
- PR Triage answers "Do we know which agent PRs are waiting?"
- Software Map answers "Where is risk concentrated?"
- Rule analytics answers "Are standards drifting?"
- The analytics dashboard answers "Is quality improving?"

The enterprise page is literally organized as four leadership questions. This is the most transferable copy pattern [I].

**The same page, four readers** [I]:
- **Developer:** "Another bot commenting on my PRs. Will it be noisy?" Qodo answers with "real issues, not noise", committable one-click fixes and agent prompts.
- **Eng manager:** "Will it reduce review load?" Qodo answers with "~1 hr/PR" and "90% of initial review".
- **Security reviewer:** "Where does code go?" Qodo answers with zero data retention, SOC 2, on-prem, BYOK.
- **Enterprise buyer:** "Can I govern AI adoption?" Qodo answers with the governance dashboard, audit trail and "independent reviewer".

**Notable objection-handling copy** (enterprise FAQ, paraphrased):
- "Why not my coding assistant's built-in review?" The tool that writes code shouldn't be its only reviewer.
- "Isn't this just code review?" Review asks whether a change is ready; governance asks whether the codebase is improving.

These two answers are Qodo's moat argument [I].

**Weak spots** [I]:
- Asterisked stats (51.3% / 441% / 54%) with no visible source.
- A self-run benchmark presented as the main differentiator.
- Jargon density in 3.0: "software factory", "Wisdom Base", "work packages", "skills lifecycle".
- The homepage leads with demo over trial, which raises friction for small teams.

---

## DELIVERABLE 3: Feature catalog (current, publicly documented)

Unless marked otherwise, the source is docs.qodo.ai as of 2026-10-14. "Plan" comes from the pricing page comparison table.

### 3.1 PR review (core)

| Feature | What it does / problem solved | Trigger | Inputs | Output and where | Plan | Status |
|---|---|---|---|---|---|---|
| **Agentic review** (`/agentic_review`) | Multi-agent review. Specialized agents look at critical issues, breaking changes, ticket compliance, duplicated logic and rules [F architecture page]. Replaces a single generic pass | Auto on PR open, reopen or ready-for-review; manual comment; optional "review every push" | Diff, full codebase index, PR history, linked tickets, rules, skills, cross-repo map | PR comment with summary plus findings, inline comments | Pro Team+ | GA since 2.0 (Feb 2026) [F] |
| **Finding anatomy** | Each finding shows: category (Bug / Rule violation / Requirement gap / Cross-repo conflict / Skill insight), quality label (Correctness / Security / Reliability / Performance / Observability), severity High/Medium/Low, plus description, code refs, **evidence** (rule, ticket, cross-repo link), **relevance** stars from past PRs, **agent prompt**, committable suggestion | — | — | In the PR, expandable | Pro Team+ | [F] |
| **Committable suggestion** | One-click "Apply suggestion" or batch commit, for small single-file fixes | User click | — | Commit on the PR | GitHub and GitLab only | [F] |
| **Dismiss with reason** | Reasons: False positive / Intentional / Fix later / Not specified; up to 2,000 chars; **irreversible** | User, in the PR, portal, chat or agent | — | Struck-through in the PR, recorded in the portal | Pro Team+ | [F]. Docs don't say whether dismissals tune relevance in general. For mined rules they do: dismissals reduce a rule's signal [F] |
| **Resolved / outdated tracking** | Re-reviews strike through resolved findings (✓ Resolved / ✗ Dismissed / ⊖ Outdated) | New commits | — | PR comment, "Previous reviews" history by commit | Pro Team+ | [F] |
| **PR summary** (`/agentic_describe`) | Description of intent and impact, tags, review-time estimate, walkthrough, architecture diagram, file change list | Auto or command | Diff | PR comment or the native description | Pro Team+ | [F] |
| **Chat in PR** (`/ask`) | Q&A about the PR. Replaced the legacy `/ask` in 2.4 | Comment | PR plus context | Reply comment | Pro Team+ | Beta (2.4) [F] |
| **Review presets / effort** | Minimal / Standard / Comprehensive / Custom; "review effort mode" matches depth to risk; modes such as Fast and Balanced appear in history badges | Config | — | — | — | Presets GA 2.9; effort mode is a research preview [F] |
| **Blast radius** | Risk classification of a change, published as a native label | Auto | Dependency map | Label | — | Research preview (2.9) [F] |
| **Requirement validation / Spec agents** | Checks the PR against linked tickets or spec docs; flags partial implementation | Auto when a ticket is linked (PR link or branch name) | Jira / Linear / ADO / GitHub Issues / GitLab Issues / Monday (read-only) | "Requirement gap" findings | — | Ticket compliance is GA; spec agents are a research preview (2.5) [F] |
| **Design review agent** | Checks UI changes against Figma | Auto | Figma | Findings | — | Research preview (2.5) [F]. **Note:** this is the closest Qodo gets to UI/UX quality, and it is still static |
| **Remediation agent** | Auto-fix | — | — | — | — | Research preview (2.4) [F] |
| **`REVIEW.md`** | Repository-specific review guidance file | Present in repo | — | — | — | Research preview (2.6) [F] |
| **CI feedback** (`/checks`) | Analyses failed CI checks | Auto | CI results | Comment | GitHub only | [F provider matrix] |

### 3.2 Context engine (supporting and differentiating)

[F, architecture page] Ingest agents parse repos (AST chunking, configs, CI, docs) into a knowledge layer made of a **graph DB** (call/dependency graph), a **vector DB** (embeddings from Qodo-7b and text-embedding-3-large), **PR and commit history** (up to about 1,000 recent merged PRs per repo, per the Rule Miner page) and **auto-generated .md summaries**. Research agents query that layer through MCP, and a **judge layer** filters evidence before review agents use it. Web search for dependency context is a research preview (2.8).

### 3.3 Rules and standards (core differentiator)

| Feature | What it does | Plan | Status |
|---|---|---|---|
| Rules system | Define, manage and enforce standards org-wide; import from AGENTS.md / CLAUDE.md-style files; selective import from chosen repos (3.0) | Pro Team (no limit) | Beta 2.1, expanded since [F] |
| **Rule Miner** | Mines rules from *accepted* review comments that led to code changes. Up to 10 rules per repo on the first run, then up to 5 every 2 weeks. Auto-approve is **on by default for orgs created after 1 Sep 2026**; earlier orgs get a pending queue | Self-learning is listed as **Enterprise** on the pricing page [F]. The docs page doesn't state a plan [F] | Beta 2.4 |
| Suggested rules, rule analytics, rule health | Track violations, how often issues are fixed pre-merge, rule decay and conflict | Analytics Pro; advanced Enterprise | [F] |
| Skills governance | Central management of agent "skills" across repos | — | Beta 2.4 [F] |

### 3.4 Governance and portal (enterprise-oriented)

These are documented in the docs [F]:
- **Findings page:** org-wide, 30-day analytics (2.3).
- **Code review analytics dashboard** (3.0, beta).
- **Cross-repo review** (2.4 beta) and auto-discovery of repo relationships (2.5, org-wide in 2.10).
- **Software Map** (2.10 beta).
- **PR Triage** (3.0 research preview): groups agent PRs into "work packages", scores them on impact, effort and wait time, and lets one reviewer claim each package.
- **Account management:** SSO (including IdP-initiated), invite-only access, roles and permissions, teams, delegated edit, restricting reviews to registered users, API key management.

### 3.5 Agentic Toolbox (new distribution surface)

This is a CLI (`qodo login`, `qodo update`) plus MCP server plus plugins for **Claude Code, Codex and Kiro** [F]. Skills: Review (same engine as PR review, on local changes), Review Resolver, Codebase Wisdom, Manage Standards and Get Rules. It went GA in 2.9 (Sep 2026). It is the replacement for the IDE plugin. Credit consumption for Toolbox use is **not documented** [U].

### 3.6 Deprecated / retired (verified)

- Qodo IDE plugin, which included local review, fixes and "identify and run tests": retires 30 Oct 2026 [F]. Code generation (autocomplete, chat codegen) is being deprecated [F].
- Qodo Cover / Cover-Agent: unmaintained since June 2025 [F, repo notice].
- Qodo Command / Qodo Gen CLI: deprecated [F, Toolbox page].
- Legacy v1 PR commands: removed 1 Oct 2026 [F].
- PR-Agent: donated to the community as "legacy" [F].

### 3.7 Not found in public documentation (≠ "Qodo lacks it")

- Runtime/dynamic testing.
- DAST or live API probing.
- Exploit reproduction.
- Executing generated tests against a deployed environment.
- Writing issues *back* to Jira.
- Xray/Zephyr test-management integration.
- Per-feature credit prices.

---

## DELIVERABLE 4: Workflow map

| Workflow | Start | Steps | AI does | Human checkpoints | Friction [I] | Source type |
|---|---|---|---|---|---|---|
| **A. Developer discovers and tries it** | Homepage, a comparison page, or the marketplace | Start Trial → sign in with GitHub / Google / email → **install the Git app on repos (needs admin)** → open a PR | Reviews automatically | Read and dismiss findings | Admin-install permission is the main gate. No free tier after 14 days | Pricing FAQ + docs [F] |
| **B. Daily coding** | The developer's agent (Claude Code / Codex / Kiro) | Install the Toolbox plugin, then the agent calls the Review skill on local changes before the PR | Same review engine locally; fetches rules | Developer applies fixes | Requires using a coding agent. The IDE plugin path closes 30 Oct | Docs [F] |
| **C. Pull request** | PR opened | Auto review → summary + findings → apply suggestion / copy agent prompt / chat / dismiss with reason → push → re-review strikes resolved items | Multi-agent review, judge filtering, relevance from history | Merge decision stays human | Comment volume depends on the preset. Noise complaints appear in third-party sources [3P, low confidence] | Docs [F] |
| **D. Team configures standards** | Qodo portal | Onboarding wizard asks questions and builds presets (3.0) → import rules from repos / CLAUDE.md → Rule Miner proposes rules (auto-approve or pending) → watch rule analytics | Mines and proposes rules | Admin accepts rules (for older orgs) | Rules need review history to mine. Quiet repos produce few rules [F] | Docs [F] |
| **E. Org evaluates** | Enterprise page | Book an executive demo → trial on several repos → analytics (acceptance rate by severity, PRs impacted) | — | — | Sales-led | Enterprise page [F] / [I] |
| **F. Enterprise security review** | Trust center | Request SOC 2 docs (gated) → choose MT / single-tenant VPC / on-prem on Kubernetes → BYOK (OpenAI / Anthropic / Azure OpenAI / self-hosted) → SSO / SAML, audit logs → MSA / DPA | — | Security sign-off | Subprocessors and on-prem hardware requirements aren't public | Trust center, deployment docs, homepage FAQ [F] |

---

## DELIVERABLE 5: Pricing report

**Inspected:** 2026-10-14, at qodo.ai/pricing (visually, with pack prices confirmed by selecting the cards) and docs.qodo.ai/pricing-and-usage.

| Plan | Target customer | Published price | Billing | Included usage | Key features | Restrictions | Admin | Support | Enterprise capabilities | Upgrade triggers [I] | Source | Confidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Trial | Any team | $0, no card | 14 days | "Unlimited" reviews and credits | Full product | Ends after 14 days; reviews pause | Same | — | — | Trial end (the dashboard suggests a pack sized from trial usage) | Pricing page + FAQ | High |
| Pro Team | Teams up to ~30 users | **$0.012/credit**, pooled. Packs: **2,500 = $30** (~18 reviews/mo), **5,000** (~36/mo; $60 by arithmetic, not visually confirmed), **20,000 = $240** (~144/mo) | Monthly only, no commitment, no annual self-serve | Pack credits; no rollover; overage at the same rate up to a customer-set cap | Agentic PR review, unlimited rules, Git + IDE, pre-PR skills, dashboard and analytics, cross-repo | No self-learning, custom agentic workflows, Gerrit, advanced analytics, SSO, audit logs, BYOK or private deployment | Basic | Standard (docs, in-product, tickets) | — | PR volume, SSO / audit requirement, BYOK / on-prem, >30 users | Pricing page | High |
| Enterprise | 30+ users, regulated | **Not publicly listed** (contact sales) | Annual contracts, negotiated; MSA / DPA | Not public | + SSO / SAML, audit logs, self-learning, BYOK, governance / advanced analytics, Gerrit, custom workflows | — | Full | Priority, SLA, dedicated CSM | Single-tenant SaaS, on-prem, air-gapped | — | Pricing page | High that it exists; price unknown |
| Open Source | Public GitHub repos with 200+ stars | Free | — | Not stated | PR review, summaries, `/ask`, suggestions | No Rules or PR history | — | — | — | — | OSS docs | High |

**Unit economics observable from outside** [I]:
- 2,500 credits ÷ 18 reviews ≈ **139 credits ≈ $1.67 per review**.
- Credits scale with PR size and complexity [F], so "per review" is an average.
- There are **no seat fees**, and developers don't need Qodo accounts [F].

**Historical pricing (not current, don't cite as current)** [3P]: third-party pages describe a former free "Developer" tier (30 PR reviews/month per org, plus IDE credits) and **Teams at $30/user/month** (2,500 IDE credits per user). Some sites still show $19/user. The current model (team-pooled credits, no seats) replaced that.

**Business-model analysis** [I]:
1. **What is free:** a 14-day full-product trial and OSS repos. No permanent free tier, which means they qualify leads by time rather than by features.
2. **What makes teams upgrade:** volume (credits) is the main lever. Enterprise is gated by **identity and compliance** (SSO, audit), **data control** (BYOK, on-prem) and **intelligence** (self-learning, advanced analytics), not by core review quality. That is the textbook "SSO tax" packaging.
3. **Natural upgrade pressure:** credits expire monthly, overage runs to the cap, and there is a ~30-user ceiling on Pro.
4. **Team-oriented features:** pooled credits, rules, dashboard, cross-repo.
5. **What justifies enterprise:** governance analytics, audit trail, private deployment, BYOK, SLA.
6. **Individual vs org value:** the individual tier has effectively been removed. Value is sold at the org level, and developers are users, not buyers.
7. **Costs that scale with usage:** LLM tokens per review (larger PRs cost more), indexing and storage of the graph and vector stores per repo.
8. **Value alignment:** credits roughly track PR size and complexity, which tracks review effort saved. That alignment is reasonable.
9. **Unknown:** credit cost of chat, Toolbox and Rule Miner; enterprise price points; discount policy; margins.

**Competitor context** [3P, vendor-adjacent sources, verify before relying on them]:
- CodeRabbit is about $24–$30 per developer per month (Essentials), up to $72 for Advanced, and free for public repos.
- Greptile is $30 per seat with 50 reviews included, then $1 per review (changed in March 2026). It has a free single-developer tier.
- GitHub Copilot code review moved to token-based AI credits on 1 Jun 2026; GitHub's own estimate is ~$0.05–$5 per review depending on effort.

Market signal: **AI review is converging on usage-based pricing at roughly $1–2 per review.**

---

## DELIVERABLE 6: Trust and technical report

| Topic | Finding | Classification |
|---|---|---|
| Deployment | Multi-tenant SaaS (default); single-tenant in the customer's VPC (Enterprise); on-prem on Kubernetes; air-gapped (Enterprise) | Confirmed (docs, pricing) |
| Git providers | GitHub, GitLab (Cloud + Server), Bitbucket (Cloud + Data Center), Azure DevOps, Gerrit (Enterprise / on-prem; all deployment models since 3.0) | Confirmed |
| Model providers | Model-agnostic: logos for Anthropic, OpenAI, Gemini, DeepSeek, NVIDIA. BYOK for OpenAI / Anthropic / Azure OpenAI / self-hosted (Enterprise). Own embedding model Qodo-7b | Confirmed (enterprise page, FAQ, architecture page). Which model runs the default SaaS reviews is **unknown** |
| Data retention | "Zero data retention" plus no training on customer code (homepage, pricing FAQ). The enterprise page mentions 48-hour log retention. **But the architecture stores a persistent index** (graph DB, vector DB, PR history, generated summaries) | Claims confirmed as *stated*. **Interpretation [I]:** "zero retention" most plausibly applies to LLM-provider processing, not Qodo's own index. Evaluators should ask exactly what is persisted, where, and for how long |
| Compliance | SOC 2 Type II claimed on the homepage; the trust center lists SOC 2 and gates documents behind a request | Claim confirmed as stated; the report itself not inspected |
| Identity / access | SSO / SAML, IdP-initiated SSO, RBAC roles, invite-only, teams, API keys, audit logs (Enterprise) | Confirmed (docs) |
| Network | Outbound IP allowlist documented | Confirmed |
| Human oversight | Human merges; findings can be dismissed with a reason (irreversible); rule auto-approve configurable (default on for new orgs) | Confirmed |
| Failure handling | Notifications before an overage cap pauses reviews; partial GitHub-update failures surfaced in the portal | Confirmed |
| Rate limits, SLA numbers, subprocessors, on-prem hardware/GPU | Not public | Unknown |
| Independent security verification | None found beyond the SOC 2 claim | Unknown |

---

## DELIVERABLE 7: Qodo vs QAX matrix

**QAX assessment basis:** I inspected the repository. QAX has:
- 33 `*_probe` functions in `execution/probes.py`
- 38 test files (the roadmap reports 354 tests passing)
- a CLI of about 2.7k lines (`main.py`), a Streamlit UI (`ui/app.py`, 2.3k lines) and a Flask server (`web/server.py`)
- an eval gate (`make eval`) with golden target and truth files

External validation (from `strategy/evidence/REALITY_CHECK_RESULTS.md` and project memory): 3 external stacks (Node, Go, Java) with 0 false positives; go-gin-vul recall 7/9; VAmPI 4–5/5; one real P1 found in an OSS Flask app. Jira filing is **built but never exercised live** (DRY_RUN, no credentials).

Maturity key: **E** = exists, **W** = works (shown on a real target), **T** = tested (automated), **R** = reliable (gate-proven repeatable), **S** = secure, **U** = usable by a stranger, **C** = commercially ready.

| Capability | Qodo | Qodo evidence | QAX current | QAX evidence | Competitive importance | Gap | Recommended action |
|---|---|---|---|---|---|---|---|
| AI PR code review | Verified (core) | docs/code-review | Not applicable / not a goal | — | High for Qodo's market | Huge | **Don't compete.** Integrate alongside |
| Rules / standards governance | Verified | Rule Miner docs | None | — | High for enterprises | Huge | **Don't build** |
| Runtime API vulnerability proof (live probes) | Not found in public docs | — | E/W/T/R. 33 deterministic probes; 0 FP on 3 external stacks; golden gate | probes.py, evals/gate.py, REALITY_CHECK_RESULTS | **High; QAX's wedge** | QAX leads | Deepen and evidence it (B4, B7) |
| Reproducible evidence per finding | Partial: "evidence" tab = rule / ticket / repo links, not runtime proof | comment-anatomy | E/W/T. Redacted replay artifact (S6); deterministic vs AI-lead split (B1) | ROADMAP S6, B1 | High | QAX leads in kind | Make replay a one-click "reproduce" command |
| Static review-time security flags | Partial ("Security" quality label) | comment-anatomy | Partial (LLM comprehension / detector) | services/detector_service.py | Medium | Qodo leads | Not a priority |
| UI runtime testing (browser tour, JS errors, visual regression) | Not found (Figma design-review agent is static, preview) | whats-new 2.5 | E/W. Playwright tour, UI findings, visual diff; requires the `.venv-exec` venv | live_tour.py, visual_regression.py | Medium–High | QAX leads | Harden setup; fold into the B4 case studies |
| Test-case generation (story / code / Jira → cases) | **Retired**: IDE plugin ends 30 Oct 2026; Cover unmaintained | docs/qodo-ide, repo notice | E/W/T. API-mode validator fix (0 → 13–14 valid) | ROADMAP Phase 1 | Medium (commoditized by coding agents) | Qodo exited | Keep as an **on-ramp** tied to confirmed bugs, not a headline |
| Regression test from a confirmed bug | Not found | — | Partial (the finding becomes a replayable case) | S6 replay | **High (unique loop)** | Open | Build: confirmed finding → runnable regression test → Xray |
| Jira integration | Read-only ticket context | ticketing docs | E/T, **not W** (DRY_RUN; never filed live) | B6 pending creds | High for QA buyers | QAX unproven | **Do B6 now** |
| Xray / Zephyr / Excel test management export | Not found | — | E/T (adapters), not live-proven | core/adapters | Medium (QA-team niche) | QAX ahead on paper | Prove Xray live with B6 |
| Git provider integration / PR gate | Verified (5 providers) | provider matrix | E (CI PR-scan gate; GitHub / GitLab CI yml) | ci/ | High for distribution | Qodo far ahead | A GitHub Action that scans a preview deploy and comments **confirmed only** |
| Coding-agent distribution (MCP / Claude Code plugin) | Verified (Toolbox GA) | toolbox docs | None | — | Rising | Gap | Medium-term: expose QAX scan as an MCP tool |
| Multi-repo / org context | Verified | Software Map | None | — | Enterprise | Huge | Don't build |
| Accounts, SSO, RBAC, audit | Verified (Enterprise) | docs | Minimal (X-QAX-Token web auth, S3) | ROADMAP S3 | Needed only for SaaS | Large | Defer until there are paying teams |
| Deployment / privacy | SaaS / VPC / on-prem / BYOK | docs | **Local-first by design**: runs on the user's machine with the user's own key | architecture | High for the privacy story | Different shape | Market "local-first, your key" honestly; no certification claims |
| Benchmark / public proof | Self-run benchmark + public dataset | benchmark page | Internal gate + truth files; not public | evals/ | High | Qodo ahead | Publish the QAX golden target + scorecard as an open benchmark |
| Measured accuracy reporting per run | Not found (per-run) | — | E/W/T. Scorecard: recall / FP / $ per run | services/scorecard.py | Medium–High (trust) | QAX leads | Surface it in reports |
| Safety controls for scanning | N/A | — | E/T. Authorized-target gate, egress boundary, mutating-probe labels | S2, S4 | **Must-have** for any hosted scanner | — | Add ownership verification before any hosted mode |
| Usability for a stranger (install → first result) | Verified (one-click app install) | pricing FAQ | Not demonstrated (2 venvs, CLI-heavy; UI deps recently declared) | S5, memory | **Critical** | Large | Packaging sprint: one command / Docker, sample target |
| Commercial readiness | Yes | — | No (no billing, accounts or hosted runtime; IP clearance pending) | GTM doc, ip-status memory | — | — | Design partners before SaaS |

**QAX's most credible differentiators** [I, evidence-based]:
1. Findings are **proven against the running app**, deterministic, with 0 FP across 3 external stacks. Qodo can only *suspect* from code.
2. Every run reports **measured accuracy** (scorecard), which a reviewer bot doesn't do.
3. **Local-first plus your own key:** the code never leaves the machine except in LLM calls the user controls.
4. A QA-native output (Jira bugs, Xray tests), aimed at the QA lead, whom Qodo ignores.

**Where to compete, complement, or avoid** [R]:
- **Compete:** "find real bugs" credibility (precision). Qodo's benchmark F1 is about 60% [M, self-reported]; QAX's claim is a *different kind* of evidence.
- **Complement:** sit next to whatever reviews the diff. QAX checks the deployed preview.
- **Avoid:** PR comments on code style or standards, rules mining, governance dashboards, multi-repo maps, an IDE plugin, and enterprise on-prem.

---

## DELIVERABLE 8: QAX strategic positioning

### 8.1 Opportunity assessment

Scores run 1–5; for "Competition" and "Difficulty", a higher score means *harder*. All entries are [I/R].

| # | Opportunity | Pain | Target | Competition | Difficulty | Time to value | Defensibility | Money | Key risk | Evidence needed |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **Proof-backed runtime bug and security findings for APIs** (0-FP, replayable) | High. Scanners are noisy and AI reviewers guess | Startups / SMB eng and QA leads without AppSec staff | 3 (DAST vendors: ZAP, Burp, StackHawk, etc., but few with AI generation plus 0-FP discipline) | 3 | Minutes, if packaging is fixed | Medium: probe library, truth sets, the trust brand | Medium–High | Coverage breadth vs mature DAST; legal / authorization | 3 public case studies on non-seeded apps; 5 design partners re-running weekly |
| 2 | **Confirmed bug → regression test → Jira / Xray, automatically** | High for QA teams (manual repro and ticket writing) | QA leads in Jira/Xray shops | 2 (nobody connects runtime proof to test management) | 2–3 | Days | Medium–High (workflow lock-in) | Medium | Xray niche size; real-Jira path unproven | B6 live; 3 QA leads say they'd replace manual repro write-ups |
| 3 | **Preview-environment PR gate** ("scan the deploy, comment only confirmed bugs") | Medium–High | Teams with preview deploys (Vercel / Render / K8s) | 3 | 3 | Days | Medium | Medium (usage) | Needs auth recipes per app; flaky envs | GitHub Action installs; % of PRs with a comment that devs act on |
| 4 | **Local-first / privacy-conscious QA** | Medium (regulated, IP-sensitive) | Agencies, fintech / health SMBs, EU teams | 2 | 1 (already the architecture) | Immediate | Low–Medium (a stance, not a moat) | Low on its own | Hard to monetize a local tool without licensing | Willingness-to-pay interviews |
| 5 | **Open accuracy benchmark for runtime findings** | Medium (buyers can't compare tools) | Evaluators, community | 1 | 2 (the gate exists) | Weeks | Medium (category authority, Qodo's own playbook) | Indirect (funnel) | Vendor-benchmark skepticism | Stars, citations, inbound |
| ✗ | AI PR code review | — | — | 5 (Qodo, CodeRabbit, Greptile, Copilot) | — | — | Low | — | Head-on with $120M-funded players | — |
| ✗ | Standalone AI test-case generation | Medium | QA | 4 (coding agents do it for free; Qodo exited) | — | — | Low | Low | Commoditized (QAX's own Phase 3 showed a generic agent writes passing tests) | — |

**Top 5, ranked:** **1 → 2 → 3 → 5 → 4.**

Why 1–3 beat "another generic AI feature": each produces an **artifact a generic LLM can't fake**, namely a replayed HTTP exchange that proves the bug, and each connects to a workflow buyers already pay for (Jira / Xray, CI). Generic AI features (chat, summaries, "AI test ideas") get commoditized each time a model improves. Runtime proof gets *better* as models improve, because the probe stays deterministic and the LLM only widens recall.

### 8.2 Recommended position

> **QAX proves bugs in your running app. Each finding comes with a replayable request and a Jira-ready bug, and it never cries wolf.**
> Category: *runtime quality and security proof* (the evidence layer that complements code review).

**Why customers would choose QAX** [I]:
- **Owner / CTO of a 5–50 dev company:** "I can't afford AppSec; my AI reviewer comments on code but I don't know if the app is actually exploitable." QAX shows proven issues only.
- **QA lead:** "I spend hours reproducing and writing up bugs." QAX files them with evidence plus a regression test.
- **Developer:** "Scanners waste my time with false positives." Deterministic CONFIRMED findings are kept separate from AI leads.

---

## DELIVERABLE 9: QAX product roadmap

Principle: **reliability, evidence and trust before breadth.** This roadmap fits into, and slightly reorders, the existing MASTER WORKLIST in `strategy/ROADMAP.md`.

### A. Next 2 weeks: Must-have

| Initiative | Problem | User | Solution | Dependencies | Complexity | Success metric | Validation | Risk |
|---|---|---|---|---|---|---|---|---|
| A1. **B6 live Jira / Xray filing** | Jira integration is unproven, and it's the core of the buyer story | QA lead | Use real trial creds; file one confirmed finding as a Bug and one test as an Xray Test; add a live-gated test | Owner's trial creds | Low | 2 / 2 artifacts created, with correct fields, idempotent (no duplicates on re-run) | Screenshot plus a re-run check | Xray API / auth quirks |
| A2. **First-run packaging** | A stranger can't get to a first result | Evaluator | `docker compose up`, or a single `pipx install qax` plus the bundled golden target; `qax demo` gives a proven finding in under 5 min | S5 done | Medium | Time-to-first-confirmed-finding ≤ 10 min on a clean machine | 3 people outside the project try it; time them | Two-venv Playwright split |
| A3. **B4 case study #1** (messy, non-seeded OSS app) | No public proof outside seeded targets | All | Run quick + deep, verify every finding by hand, publish the scorecard and replay evidence (after disclosure where needed) | A2 helps | Medium | Published write-up with honest recall / FP numbers | Peer review by one external engineer | Finding 0 bugs; that is still publishable as an FP result |

### B. Next 30 days: Must-have + differentiator

| Initiative | Problem | Solution | Complexity | Success metric | Risk |
|---|---|---|---|---|---|
| B1. **Confirmed finding → regression test** | Fixes regress silently | Each CONFIRMED finding emits a standalone pytest / HTTP replay test (and an Xray Test) that fails while the bug exists and passes once fixed | Medium | ≥ 90% of confirmed findings produce a test that flips red→green on the golden target's SECURE=1 control | Mutating probes need state reset |
| B2. **GitHub Action: scan the preview / staging URL** | Not in the dev workflow | Action takes a URL plus an auth recipe, runs the quick scan, and posts a PR comment with **CONFIRMED only** (leads collapsed) | Medium | Works on 2 real repos; median runtime < 5 min | Auth per app; flaky preview envs |
| B3. **Case studies #2–#3** | Need a pattern, not an anecdote | Different stacks; at least one non-security "business" app | Medium | 3 published | — |
| B4. **Public QAX benchmark v0** | Buyers can't verify claims | Open-source the golden targets + truth files + `make eval` | Low | Anyone can reproduce QAX's recall / FP with one command | Exposes weaknesses. That's fine and builds credibility |
| B5. **Design-partner program** | No demand signal | 3–5 teams, free in exchange for weekly use + interviews | Low (eng) / High (sales) | ≥ 3 teams run QAX weekly for 4 weeks | **IP clearance gate** (see `ip-status`) before external distribution |

### C. 60–90 days: High-value differentiators

- **B7 business-logic probe**, guided by design-partner apps. This is the hardest and most valuable class.
- **Auth recipes library:** OAuth / OIDC, API keys, cookie sessions. Silent auth failure is QAX's documented #1 false-negative cause.
- **Scheduled scans + history/diff:** "new since last scan", for regression monitoring.
- **MCP tool exposure:** `qax.scan(url)` callable from Claude Code / Codex, following Qodo's Toolbox distribution lesson.
- **Hosted pilot (single-tenant per customer):** only if design partners demand it, and only with **target-ownership verification** (DNS TXT or file token) built in first.

### D. 3–6 months: only if demand is validated

- Minimal SaaS: accounts, workspaces, usage metering, Stripe, a hard spend cap.
- Team features: shared findings, assignment, Slack notification.
- Self-hosted license (Docker) for privacy-sensitive buyers.

### Do NOT build yet (premature / low-priority)

- AI PR code review or style comments: owned by Qodo, CodeRabbit, Copilot.
- Rules mining / governance dashboards / Software Map: enterprise categories with no QAX advantage.
- IDE plugin: Qodo itself is retiring its plugin, which is a strong signal.
- SSO / SAML, RBAC, audit logs, on-prem Kubernetes: no enterprise buyer yet.
- Multi-agent "agent team" orchestration: conflicts with the deterministic-trust thesis (already decided in memory).
- Expanding standalone test generation: commoditized.
- More probe classes without case-study evidence that they matter (B3 is complete enough).

### Benchmark: how to know QAX is improving

| Metric | Definition | How to measure |
|---|---|---|
| True-positive recall | Confirmed findings ∩ truth ÷ truth vulns | `make eval` on golden targets plus each case-study truth file (truth written *before* the scan) |
| False-positive rate | Confirmed findings not in truth and rejected after manual verification ÷ confirmed findings | Manual triage per case study. The SECURE=1 control must be silent |
| Reproducibility | % of confirmed findings identical across 3 runs | Gate already checks 2 runs (B1); extend to N=3 on external apps |
| Replay validity | % of findings whose replay artifact re-triggers the bug from a clean state | Automated replay against a freshly booted target |
| Regression-test quality | % of generated regression tests that fail on vulnerable and pass on fixed | Golden target, vulnerable vs SECURE=1 |
| Execution reliability | % of scans that complete without crash or silent-leg warnings | B2 `silent_legs()` telemetry across all runs |
| Auth success rate | % of scans where authenticated probes actually ran (created_records > 0 or the token was accepted) | Scorecard field. Catches "clean because blind" |
| Time to first useful result | From install start to first CONFIRMED finding on the demo target | Timed sessions with new users |
| Setup completion rate | % of trial users who reach a completed scan of *their own* app | Opt-in telemetry or interview logs |
| Export success rate | % of file-to-Jira/Xray attempts that create correct artifacts | Adapter result logging (live mode) |
| Retention | % of design partners running ≥ 1 scan per week at weeks 4 and 8 | Partner logs |
| $ per scan | LLM cost per quick / deep scan | `services/usage.py` (already measured) |

No current scores are claimed here beyond what's recorded in the repo.

---

## DELIVERABLE 10: QAX website blueprint

> ⚠️ **Gate:** per `strategy/legal/IP_CHECKLIST.md` and the IP memory, no public site, launch or open-sourcing before counsel clears IP.

### 10.1 Sitemap

- `/` Home
- `/how-it-works`
- `/proof` (benchmark + case studies)
- `/security` (how QAX handles your code and your target; responsible-use policy)
- `/integrations` (Jira, Xray, GitHub Actions, Excel)
- `/pricing`
- `/docs` (quickstart, auth recipes, probe catalog, CLI reference)
- `/changelog`
- `/compare` (later: "QAX + your code reviewer")
- `/design-partners` (apply)
- Legal: Terms, Privacy, Acceptable-use / authorized-testing policy

### 10.2 Homepage, section order and draft copy

Claims marked **⚑** need evidence before publication.

**1. Hero.** Answers "What is it, in five seconds?"
- **Headline:** *Your AI reviewer reads the code. QAX proves what the running app actually does.*
- **Subhead:** QAX scans your live API and UI, proves each bug with a replayable request, and files it to Jira with a regression test. Findings are split into *Confirmed* (deterministically proven) and *Leads* (AI suspicions), so you always know which is which.
- **Primary CTA:** `Run the 5-minute demo` (local, no signup). **Secondary CTA:** `See a real finding` (opens a case study).
- Purpose: self-serve first, because QAX's proof is quick to show and the buyers are small teams.

**2. The problem.** Answers "Why do I need this if I have code review?"
> Code review and scanners give you opinions. A reviewer bot can't tell whether `/users/{id}` actually leaks another user's data. A traditional scanner floods you with "possible" issues. Teams end up ignoring both.

**3. How it works (3 steps).** Answers "What do I do?"
1. **Point it at your app**: a URL, plus the repo if you have one. QAX maps the endpoints and screens.
2. **QAX attacks and tests it**: deterministic probes (injection, broken auth, BOLA/IDOR, JWT forgery, SSRF, XXE, SSTI…) plus AI-generated tests.
3. **Get proof, not noise**: each confirmed bug has the exact request/response, severity, a fix, and a one-click Jira ticket with a regression test.

**4. One real finding, shown in full.** Answers "Show me." Use a screenshot of a real report card: endpoint, payload, response excerpt, "CONFIRMED · deterministic", replay command, Jira preview. This is the most important visual on the site.

**5. Proof / trust numbers.** Answers "Why believe you?"
- "0 false positives across 3 external stacks (Node, Go, Java)" ⚑ *Only with a link to the published methodology and the caveat that the sample is small.*
- "Every scan reports its own accuracy" (scorecard screenshot).
- "Open benchmark: reproduce our numbers with one command" ⚑ *Only after the benchmark is public.*
- Case-study cards ⚑ *Only after publication and responsible disclosure.*

**6. Confirmed vs Leads.** Answers "Will it waste my time?" Explain the split. Make it the brand's signature idea.

**7. Integrations.** Answers "Does it fit my workflow?" Jira (Bugs) ⚑ *only after B6 live*, Xray / Zephyr (Tests), Excel, GitHub Actions / GitLab CI ⚑ *only after B2 works*.

**8. Security and privacy.** Answers "Is it safe to point this at my stuff?"
> Runs on your machine. Your code stays local; only the prompts you allow go to the LLM provider, on your own API key. Export is off until you turn it on. QAX only scans targets you mark as authorized, never follows requests off your origin, and labels every probe that writes data.
(Every sentence here maps to built features: S1, S2, S4. **Never** mention SOC 2 or similar unless obtained.)

**9. Works alongside your code reviewer.** Answers "Do I replace Qodo / CodeRabbit?" No. They check the diff, QAX checks the deployed app.

**10. Pricing teaser + FAQ.** Answers "What does it cost?" Pricing in the FAQ, the way Qodo does it.

**11. Final CTA:** Run the demo / Apply as a design partner.

### 10.3 Feature-page template

Problem → what QAX does → **one real artifact** (screenshot or replay) → how it's verified (control, determinism) → limits (be honest: "no-spec apps: discovery is best-effort") → integrations → CTA.

### 10.4 Evidence strategy

- Truth files written before scans.
- Published scorecards including misses.
- Replay artifacts for each confirmed bug.
- A dated changelog.
- Founder's QA background (1000+ hand-built E2E cases) as the human credibility anchor.

### 10.5 FAQ topics

- How is this different from a code reviewer / SAST / DAST?
- What does "Confirmed" mean?
- What leaves my machine?
- Which stacks are supported?
- Do I need an OpenAPI spec?
- How does auth work?
- Can it damage my data (mutating probes)?
- Legal: only test what you own.
- Pricing / limits.
- Jira / Xray setup.

### 10.6 Signup / trial journey

1. `pipx install qax` / Docker, then `qax demo`: a proven finding on the bundled target in under 5 min, no account.
2. `qax scan --url <your staging> --authorized`: the first real scan.
3. Prompt: "Connect Jira to file this".
4. Design-partner form: the only form, and it asks for minimal data.

### 10.7 Screenshots / demos to produce

- A finding card with the replay.
- The scorecard.
- Confirmed vs Leads.
- A Jira Bug created by QAX (after B6).
- A PR comment from the GitHub Action (after B2).
- A 60-second screen recording of `qax demo`.

---

## DELIVERABLE 11: QAX pricing proposal

**Recommended starting model** [R]: **per-application subscription with a fair-use scan allowance, plus a free local tier (BYO API key).**

Why this model over the alternatives:
- **Per application:** value tracks "apps I protect", which is easy for owners to understand and maps to the existing `max_router_files` coverage lever.
- **Not per seat:** QA and security value isn't per developer.
- **Not pure per-scan:** pure per-scan pricing makes people avoid scanning, which hurts a regression-monitoring product.
- **Local-first plus BYO key:** while QAX runs locally on the user's key, *LLM cost is the user's*. Charging is for the software, the deterministic probe library and updates. Usage metering only matters once there is a hosted mode.

| Plan (hypothesis) | Target | Included | Limits | Upgrade trigger | Support | Cost drivers | Abuse risk | Ops complexity |
|---|---|---|---|---|---|---|---|---|
| **Free / Local** | Individual devs, evaluators | All deterministic probes, quick scan, CLI + local UI, Excel export, BYO key | 1 app; quick scan only; no Jira / Xray; no CI action | Want deep scan, Jira, CI | Community / GitHub issues | ~$0 to QAX (runs locally) | Scanning targets you don't own; mitigated by `--authorized` + an acceptable-use policy | Low |
| **Pro**, hypothesis **$29–49 per app/month** | Small teams / agencies | Deep scan, Jira + Xray / Zephyr, regression-test generation, GitHub Action, scan history | 3 apps; BYO key, or a hosted LLM allowance (e.g. 50 deep scans) if hosted | More apps, team sharing | Email | If hosted LLM: ~$0.38 per deep scan (measured) → 50 scans ≈ $19 | Shared licenses; hosted scanning of third-party targets (needs ownership verification) | Medium |
| **Team**, hypothesis **$199–399/month** | 10–50 dev orgs | 10+ apps, scheduled scans, shared workspace, Slack, priority probe requests | Fair use; hard spend cap | SSO, self-host, SLA | Priority email, onboarding call | Hosted compute + LLM | Spend runaway; mitigated by caps (Qodo's pattern) | Medium–High |
| **Enterprise / Self-hosted** | Regulated | Self-hosted Docker license, custom probes, SLA | Contract | — | Dedicated | Support load | — | High (defer) |

**Assumptions and context:**
- These numbers are **hypotheses, not validated**.
- Market reference [3P]: AI code review costs about $24–30 per dev/month (CodeRabbit), $30 per seat plus $1 per review (Greptile), or about $1.67 per review at Qodo's pack rate [I]. Commercial DAST tools are typically sold per application per year at well above these numbers. **I did not verify current DAST prices in this research, so check them before anchoring.**
- QAX's measured scan costs ($0.02 quick / $0.38 deep) leave large gross margin at these prices, even if hosted.

**How to test pricing:**
1. Run design partners free, then ask a **Van Westendorp** question set (too cheap / cheap / expensive / too expensive) per app.
2. Put a "Pro: $X/app" fake-door button on the site and measure clicks to the waitlist across 2–3 price variants.
3. Convert 1–2 partners to a **paid pilot** (any amount). That is the first real WTP signal.
4. Track which limit people hit first: apps, deep scans or Jira. That tells you the real value metric.

**Preventing runaway AI / scanning cost** (copy Qodo's mechanics):
- Customer-set **monthly hard cap**, with notifications at 80% and 100%.
- Per-scan token budget (deep scan already budgeted via `max_endpoints`).
- Model routing (cheap model for comprehension, stronger model only for red-team).
- Cache by repo hash.
- Deterministic probes cost no LLM tokens. Make "quick scan" the default and deep scan opt-in.
- Hosted mode must require target-ownership verification, both to prevent abuse and for legal exposure.

---

## DELIVERABLE 12: Source register

All accessed 2026-10-14. Reliability: **H** = official / primary, **M** = credible secondary, **L** = vendor-adjacent or affiliate.

| Title | URL | Type | Date | Supports | Rel. | Uncertainty |
|---|---|---|---|---|---|---|
| Qodo homepage | https://www.qodo.ai/ | Official marketing | live | Hero, sections, FAQ pricing, security claims, testimonials, install counters | H (as claims) | Testimonial companies partly inferred from image filenames |
| Pricing | https://www.qodo.ai/pricing/ | Official | live | Plans, credit rate, packs ($30 / $240 confirmed in the UI), FAQ | H | $60 for 5k by arithmetic only |
| Enterprise | https://www.qodo.ai/solutions/enterprise/ | Official | live | Enterprise features, stats, objections, 48h log retention | H (claims) | Asterisked stats unsourced |
| Git review feature page | https://www.qodo.ai/features/qodo-git/ | Official | live | Review features, PR Triage, benchmark claim | H | — |
| About | https://www.qodo.ai/about/ | Official | live | Founders, board, mission | H | Investor names via logo filenames |
| Benchmark | https://www.qodo.ai/ai-code-review-benchmark/ | Official (vendor-run) | undated | F1 claim, methodology | M | Vendor-run; competitor scores in images |
| Introducing Qodo 2.0 | https://www.qodo.ai/blog/introducing-qodo-2-0/ | Official blog | Feb 2026 | 2.0 multi-agent, F1 60.1% / recall 56.7% | H (claims) | Self-reported |
| Qodo 2.0 press release | https://www.globenewswire.com/news-release/2026/02/04/3232129/0/en/qodo-2-0-redefines-ai-code-review-for-accuracy-and-enterprise-trust.html | Press release | 2026-02-04 | Launch date, customers | H | Marketing |
| Rebrand post | https://www.qodo.ai/blog/introducing-qodo-a-new-name-the-same-commitment-to-quality/ | Official blog | 2024-09-30 | CodiumAI → Qodo, Gen / Merge names | H | — |
| Bloomberg, Qodo raises $40M | https://www.bloomberg.com/news/articles/2024-09-30/ai-code-checker-qodo-raises-40-million-to-serve-bigger-clients | News | 2024-09-30 | Series A | H | Paywalled; via search snippet |
| Series B reports | https://parsers.vc/fund/qumracapital.com/ ; https://f4.fund/firms/qumra-capital/activity | Aggregators | Mar 2026 | $70M Series B, Qumra lead, ~$120M total | M–L | No Qodo press release found by me |
| Docs index | https://docs.qodo.ai/llms.txt | Official docs | live | Full doc map | H | — |
| What's new | https://docs.qodo.ai/whats-new | Official | 2.0 → 3.0 | Release timeline | H | — |
| Architecture | https://docs.qodo.ai/core-concepts/qodo-platform-architecture | Official | live | Knowledge layer, agents, Qodo-7b | H | — |
| Review overview / trigger / anatomy / dismiss | https://docs.qodo.ai/code-review/overview ; …/use-qodo-in-prs ; …/comment-anatomy ; …/dismiss-a-finding | Official | live | Review workflow | H | — |
| Rule Miner | https://docs.qodo.ai/governance/rule-enforcement/rule-miner | Official | live | Self-learning rules | H | Plan not stated on the page |
| Agentic Toolbox | https://docs.qodo.ai/agentic-toolbox/agentic-toolbox-overview | Official | live | Toolbox, skills, agents | H | Credit use undocumented |
| Pricing and usage | https://docs.qodo.ai/pricing-and-usage | Official | live | Usage-based billing, unlimited users, trial | H | — |
| Deployment models / On-prem | https://docs.qodo.ai/deployment-models ; https://docs.qodo.ai/on-prem | Official | live | MT / ST / on-prem, Kubernetes | H | Requirements not shown |
| Git provider matrix | https://docs.qodo.ai/install-qodo/git-provider-feature-support | Official | live | Provider parity | H | — |
| Ticketing integrations | https://docs.qodo.ai/integrations/ticketing-integrations | Official | live | Read-only ticket context | H | Write-back undocumented |
| Open-source program | https://docs.qodo.ai/open-source-program | Official | live | Free OSS tier | H | — |
| IDE plugin retirement | https://docs.qodo.ai/qodo-ide | Official | live | Retiring 30 Oct 2026 | H | — |
| Trust center | https://trust.qodo.ai/ | Official | 2026-06-17 | SOC 2, gated docs | H | Report not reviewed |
| PR-Agent repo | https://github.com/qodo-ai/pr-agent | Official / community | live | Donation, legacy status, 13.4k stars | H | — |
| Qodo Cover repo | https://github.com/qodo-ai/qodo-cover | Official | notice 2025-06-15 | Unmaintained | H | — |
| Data privacy (v1 docs) | https://docs.qodo.ai/v1/data-sharing | Official (legacy) | v1 | ZDR wording, `/similar_issue` exception | M (may be outdated) | Legacy version |
| HN benchmark thread | https://news.ycombinator.com/item?id=46891860 | Community | 2026 | Skepticism of the benchmark premise | M | Single thread |
| Qodo pricing (3P) | https://aicodereview.cc/blog/qodo-pricing ; https://toolradar.com/tools/qodo/pricing | Third-party | 2026 | Old $30/user Teams, Developer free tier | L | Conflicting, possibly stale |
| Competitor pricing | https://macroscope.com/content/greptile-pricing-2026 ; https://dev.to/rahulxsingh/coderabbit-pricing-in-2026-free-tier-pro-plans-and-enterprise-costs-1pc4 ; https://docs.github.com/en/copilot/concepts/billing/copilot-requests | Third-party / official (GitHub) | 2026 | Market context | L–H | Competitor-written for Greptile |
| Sentiment | https://dev.to/rahulxsingh/qodo-merge-review-is-ai-pr-review-worth-it-46j1 ; https://chromewebstore.google.com/detail/qodo-merge-ai-powered-cod/ephlnjeghhogofkifjloamocljapahnl/reviews | Third-party | 2026 | Noise / reliability anecdotes | L | Individual opinions, not patterns |
| G2 reviews | https://www.g2.com/products/qodo/reviews | Review site | — | **Inaccessible (403)** | — | — |
| Wikipedia, Qodo | https://wiki.riteme.site/wiki/Qodo (mirror) | Encyclopedia mirror | — | Founding 2022, TestGPT Mar 2023 timeline | M | Mirror; rebrand month conflicts (Jul vs Sep 2024) |

---

## Appendix A: Verified timeline

| Date | Event | Source |
|---|---|---|
| 2022 | Founded as CodiumAI by Itamar Friedman (CEO) and Dedy Kredo (CPO), Tel Aviv | [3P] Wikipedia; founders [F] About |
| Mar 2023 | TestGPT: IDE unit-test generation | [3P] |
| 2023 | ~$11M seed (TLV Partners, Vine); PR-Agent open-sourced | [3P] |
| 2024 | AlphaCodium (Jan), Cover-Agent (May), enterprise platform (Jul) | [3P] |
| 30 Sep 2024 | Rebrand to **Qodo** ("code integrity platform"): Qodo Gen (IDE), Qodo Merge (PR), Qodo Cover; $40M Series A (Susa, Square Peg) | [F] blog; [H] Bloomberg |
| Jun 2025 | Qodo Cover marked unmaintained | [F] repo |
| Late 2025 – Mar 2026 | IDE moves from code generation to review-only for Teams | [3P] changelog summaries |
| 4 Feb 2026 | **Qodo 2.0**: multi-agent PR review GA; benchmark F1 claim | [F] |
| Feb–Sep 2026 | 2.1 Rules (beta), ADO · 2.3 Findings page · 2.4 cross-repo review, Rule Miner, PR chat, Research Preview program · 2.5 spec + design agents · 2.8 Agentic Toolbox (beta) · 2.9 Toolbox GA (Claude / Codex / Kiro), presets, blast radius · 2.10 Software Map | [F] |
| Mar 2026 | $70M Series B (reported Qumra lead); ~$120M total | [3P] |
| 17 Jun 2026 | Trust center published | [F] |
| 1 Oct 2026 | **Qodo 3.0**: "Quality control for the agentic software factory"; PR Triage, analytics dashboard, Gerrit everywhere; legacy v1 commands removed | [F] |
| 30 Oct 2026 | IDE plugin retirement (scheduled) | [F] |

**Positioning arc** [I]: *AI test generation for developers* (2023) → *code integrity platform* (2024) → *AI code review* (2025–Feb 2026) → *governance layer for agent-written code, for enterprises* (Oct 2026). Each step moved up-market and away from individual developers. Test generation, the founding product, was abandoned.

## Appendix B: Product map

```
Qodo
├── Git PR Review (CORE)
│   ├── Multi-agent review → findings in PR → fewer bugs reaching merge
│   ├── PR summary / chat / committable fixes / agent prompts (SUPPORTING)
│   ├── Ticket + spec compliance (DIFFERENTIATING; spec agents = PREVIEW)
│   └── Blast radius, effort modes, design-review agent (EXPERIMENTAL)
├── Context Engine (DIFFERENTIATING): graph + vector + PR history + generated docs
├── Rules & Skills (DIFFERENTIATING): rules system, Rule Miner (self-learning = ENTERPRISE)
├── Governance (ENTERPRISE): findings analytics, Software Map, cross-repo, PR Triage (PREVIEW), audit
├── Agentic Toolbox (DISTRIBUTION, NEW): CLI / MCP / Claude Code / Codex / Kiro
├── Trust (ENTERPRISE): SOC 2, ZDR claim, BYOK, ST / on-prem / air-gapped, SSO / RBAC
├── Monetization: pooled credits (Pro Team) · contracts (Enterprise) · free OSS tier
└── Retired: IDE plugin (Gen), Cover, Command CLI, PR-Agent (donated)
```

## Final quality check

| Question | Answer |
|---|---|
| More than the homepage inspected? | Yes: 22 pages/sources, including visual pricing and mobile |
| Current pricing verified? | Yes: UI-confirmed $30 / $240 packs; old $30/user flagged as historical |
| Products, features and integrations distinguished? | Yes |
| Actual UI / demos inspected? | Marketing UI yes. **The signed-in app and demo form were not inspected** (no account) |
| Facts and assumptions separated? | Yes, labelled |
| Fair QAX comparison? | Yes: maturity split E/W/T/R/S/U/C; Jira marked not-working-live |
| Realistic roadmap? | Yes, with an explicit do-not-build list |
| Testable pricing? | Yes: hypotheses plus test plan |

**Gaps that remain:**
- G2 (403).
- Enterprise prices.
- Credit cost per non-review feature.
- Default SaaS LLM.
- Subprocessors.
- Independent verification of SOC 2 and the benchmark.
- Current DAST market prices (not researched).
