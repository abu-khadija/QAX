# QAX — Current State (honest inventory)

> Analysis only. Every claim tagged **[VERIFIED]** (from the code/repo in this session)
> or **[ASSUMPTION]** (reasoned, not proven). No code was changed to produce this.

---

## 1. What QAX actually is today

**[VERIFIED]** QAX is a Python CLI + Streamlit UI that reads a codebase, uses the Claude
API to understand it, generates test cases, executes them (HTTP + Playwright), and can
push to Jira/Xray/CSV/Excel. Scale of the real code:

| Area | Lines | Notes |
|---|---|---|
| `core/` | ~10,087 | client, validator, adapters, cache, pipeline, smart-check |
| `services/` | ~4,727 | the agent stack built this session (20 files) |
| `main.py` | ~2,594 | flat CLI, 12 subcommands incl. the new `scan` |
| `tests/` | ~3,195 | **162 tests collected**, offline/mocked |

**[VERIFIED]** The agent stack (this session): `appmodel_service` (whole-system model),
`detector_service` (failure-pattern scan), `redteam_service` (adversarial generation),
`probe_service` + `execution/probes.py` (JWT-forgery + crash probes), `coverage_goals`
(deterministic templates), `critic_service` (classify failures), `investigator_service`
(verify on the live app), `reporter_service` (group by root cause),
`orchestrator_service` (conduct all of it, `qax scan`).

## 2. What is real vs stubbed vs hardcoded

- **[VERIFIED] LLM-based understanding, not AST.** There is no C#/Roslyn or JS parser.
  QAX feeds source text to the model. So "analyzes C#/JS" really means "the LLM reads
  whatever text you give it" — language-agnostic, but also *shallow*: no call graph, no
  data-flow, no type analysis beyond what the model infers in one pass.
- **[VERIFIED] The system "model" is one Claude call over a digest**, now cached. It is a
  structured snapshot (endpoints/entities/config/risks), **not** a persistent, queryable
  knowledge graph, and it is capped (~90k chars of digest).
- **[VERIFIED] Execution is split by Python version.** Playwright is NOT installed on the
  primary interpreter (3.14); UI/browser execution only runs in a separate 3.12 venv the
  user set up. API execution (requests) works anywhere.
- **[VERIFIED] Jira/Xray/Zephyr clients exist as real code**, but credentials are not
  configured (`.env` pending per CLAUDE.md) and they have not been exercised end-to-end
  in this session.
- **[VERIFIED] `selectors.json` is per-project and mostly empty** for the real projects
  (CLAUDE.md lists "fill selectors.json with real CSS selectors" as pending) — the live
  tour was added to derive selectors, but UI generation→execution isn't wired end to end.
- **[VERIFIED] Validated on ONE app only.** Everything was demonstrated against TaskFlow
  (a FastAPI todo app the user wrote with 4 planted bugs). The probes/templates were
  generalized via `core/auth_recipe.py` and proven **offline** on 3 synthetic app shapes,
  but **never run against a second real app**.
- **[VERIFIED] Quality judged by one person.** No external QA reviewer has graded QAX's
  output. The "it works" evidence is single-run demos, not measured rates.

## 3. Assumptions tied to the original employer / stack

- **[VERIFIED]** The repo still carries employer-specific project folders/config
  (`example`, legacy `example-context.md`) and the lineage `example-qa-agent → QAFlow → QAX`.
- **[ASSUMPTION]** The original design centred on Jira/Xray + a .NET/MVC + commercial-
  widget stack. The generic multi-source/adapter layer was a
  deliberate decoupling, but the prompts/examples still lean toward that world.
- **[VERIFIED — from the user's own account]** QAX was built while employed under an
  outsourcing contract. **IP ownership is unresolved and is the single most important
  open question** (see VERDICT §4, ROADMAP M0).

## 4. Honest weaknesses (the "fake/weak" list)

1. **No persistent, incremental knowledge.** Every scan re-derives the model (now cached
   on a content hash, but not *incrementally updated* per commit).
2. **Test-case quality is unmeasured.** No eval harness, no bug-detection-rate number,
   no cost-per-run number tracked over time.
3. **The "deep" end-to-end has never completed once** — credit limits cut every full run;
   only the quick (deterministic) path has run cleanly end to end.
4. **Trust layer is real but imperfect** — the Critic false-positives; the Investigator
   false-confirmed a mass-assignment (since fixed with a control experiment). These are
   LLM judgement calls that will keep having an error rate.
5. **Coverage is still partly luck** outside the deterministic templates.
6. **Single-developer, single-codebase, single-reviewer** — the three biggest credibility
   gaps for turning this into a product.

## 5. What genuinely works (not flattery — demonstrated)

- **[VERIFIED]** Whole-system model surfaced a hardcoded JWT secret + a schema/bcrypt
  contradiction + the pagination bug — things a per-file tool misses.
- **[VERIFIED]** The JWT-forgery probe proved full account takeover on the live app with
  no password (pure-stdlib HMAC). The crash probe forced a real 500.
- **[VERIFIED]** Deterministic coverage templates caught the empty-title bug and the
  pagination off-by-one **with zero successful model calls** (quick scan).
- **[VERIFIED]** Against the user's own ground truth (`INTERNAL_BUGS.md`, 4 planted bugs):
  QAX now catches **2 of 4** (pagination, empty-title) + **2 real bonus security bugs**
  not in the planted set (JWT, bcrypt-500). It misses the race condition (needs
  concurrency testing) and the search/pagination-reset (needs frontend E2E).
