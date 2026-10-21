# QAX — Whole-Company Review and Feasibility Study (2026-10-20)

*An assessment written as if by a full product company (CEO, product, engineering, QA, security, UX,
DevRel, sales, legal, finance, support). It is not a roadmap: phases and dates stay in
[PLAN.md](../PLAN.md). The action register at the end is the punch list; its status is updated in
place. Facts come from this repository and `strategy/evidence/`; anything about competitors is from
general knowledge to mid-2026 and must be verified before it is used externally.*

## 1. Executive summary
- **What QAX is:** self-hosted CLI + local web UI with two tracks — *Scan & prove* (deterministic
  probes, reports only what it proves: 32/32 planted bugs, 0 FP on the golden target; 8/8 true
  positives on 10 real apps) and *Functional QA (E2E)* (URL + repo + story → traced cases → real
  browser run → ISTQB-shaped Jira bugs: 45/45 correct verdicts, 14/14 negative controls fail on 7 real apps).
- **Verdict:** technically feasible and genuinely differentiated on one axis — *proof instead of
  guesses*. Market demand is **unproven** (0 external users, 0 demos). **GO, with conditions**, in the
  order PLAN.md already sets: prove demand and clear IP before building scale.

## 2. Product status
| Area | Status |
|---|---|
| Security proof engine | Strong (gate, mutation sweep 40/40, read-only 0 writes) |
| Functional E2E | Alpha-ready technically (7 apps) |
| Reading the app from its URL | Good (login, SPA, menus, risky-link filter) |
| Generation with a live model | Not exercised (no credit) |
| Jira | Partial (2 live bugs from Scan; functional = dry run) |
| Xray | Never exercised |
| Engineering quality | High (900+ tests, merge gate) |
| Delivery | Self-hosted only; no tenants, billing, hosted mode |

## 3. Market
Four categories each do one part: AI E2E testing (Mabl, testRigor, Functionize, Momentic, Octomind,
QA Wolf), DAST/API security (StackHawk, Burp, ZAP, Escape, Bright), test management (Xray, Zephyr,
TestRail), coding agents (Claude Code, Copilot, Cursor). The open space: one tool that tests the
*running* app functionally **and** for security, claims only what it proves, runs on the customer's
machines and speaks ISTQB + Jira. Warning: "AI writes test cases" is a commodity — never position on it.

## 4. Feasibility
- **Technical:** high. The hard parts are built and proven locally.
- **Market:** unproven. Hypothesis: mid-size QA/dev teams with staging + Jira and no dedicated security engineer.
- **Financial (hypotheses):** near-zero run cost without AI (~$0.02/run recorded), cents–dollars per
  story with AI; self-hosted ⇒ ~no infra cost. Pricing to test: Community free · Team $30–60/user/month
  · Enterprise annual (on-prem, SSO, audit).
- **Legal:** the biggest risk — IP review before any company beta; LICENSE open; authorization terms.
- **Operational:** one founder + AI (bus factor 1).

## 5. What makes it unique (ranked)
1. Proof-grade QA as the declared standard. 2. One story → functional **and** security cases.
3. Requirement-level verdicts down to Jira. 4. Honest self-healing (never FAIL → PASS).
5. Every filed bug becomes a regression case. 6. Real PR gate. 7. Per-project memory.
8. Accessibility / visual / performance basics in the same run. 9. MCP server (QAX as the proof tool
of coding agents). 10. Arabic + English reports.

## 6. Potential
Weak case: respected open-source tool + consulting. Middle: paid tool for QA teams (MENA, Europe).
Strong: the standard *proof gate* for AI-written code — every agent-written change passes QAX before merge.

## 7. Main risks
IP (can stop everything) · no proven demand · single AI provider and unstable credit · funded
competitors adding "proof" · bus factor 1.

## 8. Action register
Owner **E** = engineering (doable in the repo) · **H** = needs a person, money or an external system.
Status: ✅ done · 🟡 partly / bounded · ⏳ open. Competitors are now checked against sources: [COMPETITORS_2026-10-20.md](COMPETITORS_2026-10-20.md).

| # | Role | Item | Owner | Status |
|---|---|---|---|---|
| A1 | Legal | IP review with counsel before any company beta | H | ⏳ |
| A2 | Legal | LICENSE decision | H | ⏳ |
| A3 | Legal | Authorization / acceptable-use terms (draft for counsel) | E→H | 🟡 — Draft for counsel: `strategy/legal/AUTHORIZED_USE_DRAFT.md` (not in force) |
| A4 | Finance | Anthropic credit; then live generation on the 7 apps, case quality measured | H→E | ⏳ |
| A5 | Eng | Docker image + one-command install + doctor check | E | ✅ — `make install` / `doctor`; Docker image built (colima) and run: Python 3.12, a real browser run inside the container; compose validated |
| A6 | Eng | iframes in runs and in app reading | E | ✅ — Reader lists iframe elements as `iframe#x >>> #el`; runner enters the frame (real-browser test) |
| A7 | Eng | Xray against a real instance | H | ⏳ |
| A8 | Security | Security review of the web UI itself (token, CORS, artifact paths, repo input) | E | ✅ — Fixed a real CSRF/DNS-rebinding hole (local API accepted cross-site text/plain); Origin/Sec-Fetch-Site/Host checks, JSON-only writes, security headers, `/files` images only, safe git clone, refuses 0.0.0.0 without a token |
| A9 | Security | External pentest before hosted mode | H | ⏳ |
| A10 | QA | More stacks (Java, .NET, Angular) in the functional corpus | E | ✅ — 10 apps: + Spring PetClinic (Java/Thymeleaf), PetClinic Angular 22 over a Java REST API, eShopOnWeb (.NET) — 68/68 PASS, 20/20 negative controls FAIL; 6 reader defects found and fixed |
| A11 | UX | Unaided Quickstart by a new user | H | ⏳ |
| A12 | UX | Run history for functional QA | E | ✅ — Per-project run history: newly failing / fixed / flaky shown in the report |
| A13 | DevRel | Landing page states measured numbers only (audit) | E | ✅ — Landing numbers match the evidence; functional evidence added; Xray marked untested |
| A14 | DevRel | 3-minute demo video | H | ⏳ |
| A15 | Sales | 3 Phase-0 demos; design partners; pricing from interviews | H | ⏳ |
| A16 | Docs | Command docs + troubleshooting | E | ✅ — `docs/TROUBLESHOOTING.md`, `docs/MCP.md`, QUICKSTART §4c, README |
| U1 | Product | One story → functional + security (access-control) cases | E | ✅ — Deterministic security risks per criterion (BOLA, BFLA, auth, injection, upload, logic) shown and fed to generation; ISTQB rule; one click to prove them with the scanner. Generation itself not run live (no credit) |
| U2 | Product | Requirement verdicts + bugs linked to the story in Jira | E | ✅ — Bugs linked to the story; requirement verdicts commented on it; a Jira key as the story input (read-only). Dry run only — never exercised live |
| U3 | Product | Honest self-healing reported per step | E | ✅ — HEALED steps counted and flagged in the report (AI healing stays opt-in) |
| U4 | Product | Every filed bug becomes a regression case | E | ✅ — Live-filed bugs go to `output/regression/<project>.json` |
| U5 | Product | PR gate on a real repository | H | ⏳ |
| U6 | Product | Per-project memory across runs | E | ✅ — Run history + the app map/selectors per project; re-reading keeps the names existing cases use |
| U7 | Product | Accessibility + performance basics per case (no new dependency) | E | ✅ — Per page: WCAG basics (alt, labels, names, lang, title, ids, headings, tabindex, contrast) + TTFB/DOM/load/LCP/CLS — observations, not verdicts |
| U8 | Product | MCP server for coding agents | E | ✅ — `main.py mcp` (stdio, no dependency): 6 tools, same gates, never writes to Jira (real-browser test) |
| U9 | Product | Arabic report option | E | ✅ — Bugs and the HTML report in Arabic (RTL) |
| R1 | Risk | Single AI provider: everything except generation works with no AI | E | ✅ — QAX_AI_PROVIDER=openai: any OpenAI-compatible API or a local model (Ollama, LM Studio, vLLM); every AI call goes through core/ai_provider.py |

### Found while closing the register (2026-10-20), all fixed with tests
- **Security:** the local web API could be driven by any web page the operator visited (it accepted
  cross-site `text/plain` POSTs; no Host check) — scans, browser runs and git clones on the operator's behalf.
- **False bug:** a FAIL that followed a step that could not run (unknown element → never logged in) was
  reported as an app bug; it is now ERROR with the reason, and no bug is filed.
- **Re-reading the app deleted selector names** existing cases used (hand-written selectors replaced).
- **Run history from tests leaked into the repo** and produced a false "flaky" flag.
- **Login POSTs counted as "data changed while reading".**

### Second round (2026-10-20): the gaps listed after the first round
| # | Item | Status |
|---|---|---|
| B1 | Harness measuring AI-written cases (`make eval-generation`) | ✅ built and tested with a stand-in generator; the live measurement waits for AI credit |
| B2 | Shadow DOM (web components) read and used | ✅ real-browser test |
| B3 | SSO / 2FA: log in by hand once, runs reuse the session (`login`, UI button) | ✅ real-browser test; session file owner-only, git-ignored, never served |
| B4 | Functional E2E in CI: `execute --yes --junit`, GitHub + GitLab jobs | ✅ CLI tested; the workflow files are validated YAML, **not run on a real CI** (no pushable repo) |
| B5 | Devices (mobile/iPhone/tablet), browser choice, parallel workers | ✅ mobile + parallel tested; Firefox and WebKit installed (founder's OK) and give the same verdicts |
| B6 | Test-data cleanup (`cleanup_steps`) | ✅ tested: nothing left on the demo app |
| B7 | Visual regression for functional runs (no new dependency) | ✅ tested |
| B8 | Second AI provider | ✅ see R1 |
| B9 | Packaging (`pyproject.toml`, `qax` / `qax-web`) | ✅ `pip install -e .` in a clean venv: `qax` and `qax-web` work from any folder |
| B10 | Code split: main.py 2,700 → ~2,000 lines (cli/), web UI → app.html + app.js + app.css, functional routes → web/functional.py | ✅ |
| B11 | Competitors checked against sources | ✅ COMPETITORS_2026-10-20.md — positioning proposal for the founder |
| B12 | Mobile-friendliness observation (no viewport meta) | ✅ |

### Founder decisions, 2026-10-20
- Downloads approved and done: Firefox/WebKit, colima + Docker CLI + compose, JDK (Java 25 used; 17 failed in a network outage),
  .NET 10 SDK, the Java/Angular/.NET corpus apps.
- Positioning (COMPETITORS §"Updated positioning"): explained, awaiting the founder's choice.
