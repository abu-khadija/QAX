# QAX — handover to a new machine (2026-10-21)

Everything needed to continue on another machine: what to move, how to set it up, how to check it works,
where things are, and what is open. Read section 1 first: **some of the work is not on GitHub.**

---

## 1. State right now

| | |
|---|---|
| Branch | `feat/functional-qa-track` (all work since 2026-10-19 is here) |
| Last commit | see `git log -1` — the handover commit, after `c8e2860` |
| On GitHub? | **No.** The branch has never been pushed (by agreement: no push without the owner). `main` on GitHub is still at `6cca1c6`. |
| Tests | 1020 passed, 4 skipped on the last full run; one mutant guard fixed afterwards (it passes on its own; **re-run the full suite once on the new machine**) |
| Gate | `make eval` → GATE: PASS |
| Benchmarks | functional 125/125 on 15 sets (×2); release check 4/4; bug reproduction 6/6; functional mutants 13/13; scan 32/32, 0 FP, mutants 40/40 |

## 2. Move the code (pick one)

**A — push the branch yourself (simplest):**
```bash
git push -u origin feat/functional-qa-track
```
then on the new machine: `git clone https://github.com/abu-khadija/QAX.git && cd QAX && git checkout feat/functional-qa-track`

**B — no push: a bundle file (prepared by the handover):** `~/Downloads/QAX-transfer/qax.bundle` holds every branch
and commit. Copy it to the new machine, then:
```bash
git clone ~/Downloads/QAX-transfer/qax.bundle QAX && cd QAX && git checkout feat/functional-qa-track
git remote set-url origin https://github.com/abu-khadija/QAX.git
```
Check: `git log --oneline -3` shows the same commits as here.

## 3. What is NOT in git — copy or recreate

| What | Where (here) | What to do | Secret? |
|---|---|---|---|
| `.env` (Anthropic / Jira / Xray keys, settings) | `QAX/.env` | copy it over a private channel, **or** start from `.env.example`. Then `chmod 600 .env` — here it is world-readable (`rw-rw-rw-`): fix that too | **yes** |
| The benchmark apps (10 open-source apps, built, with their test data) | `~/Downloads/qax-corpus` (1.3 GB) | copy the whole folder (keeps builds, databases, `linkding-old`), **or** rebuild from the `start` commands in `evals/data/functional_bench.json` | no |
| Benchmark logins | `~/Downloads/qax-corpus/.bench.env` (0600) | copy over a private channel; **never commit it**. Holds `QAX_VAR_*` logins of the local apps and eShop's public demo user | **yes** |
| Run history, benchmark raw results, reports | `QAX/output/` (80 MB) | optional: copy if you want the past runs in the overview and the raw evidence | may hold screenshots of test apps |
| Claude's notes for this project | `~/.claude/projects/-Users-islam-hanafy-Downloads-XQA/memory/` | optional: copy to the same path under the new user's home (the folder name follows the project path) | no |

**Corpus notes:**
- `pocketbase`, `memos` and `beszel` are **macOS arm64 binaries**. On another OS or CPU, download that platform's release.
- `linkding-old` is a git worktree of `linkding` at `c633db5`, with the two real bugs. If the home path changes,
  run `git -C ~/Downloads/qax-corpus/linkding worktree repair`.
- `.demo-petclinic-seeded` is rebuilt by the demo script when it is missing.

## 4. Set up the new machine

```bash
# Python 3.12 (3.11+ works) and the QAX virtualenv
python3.12 -m venv .venv-exec
./.venv-exec/bin/pip install -e '.[dev,visual,yaml]'
./.venv-exec/bin/python -m playwright install chromium firefox webkit
```

For the benchmark and the demo apps:
- **Java 17+:** `brew install openjdk`. The demo script finds Homebrew's java; else set `QAX_JAVA`. macOS's `/usr/bin/java` may be Java 8, which is too old.
- **Node:** for PetClinic Angular.
- **.NET:** for eShopOnWeb.

The commands are in `~/Downloads/qax-corpus/start-all.sh` and `evals/data/functional_bench.json`.

## 5. Check it works (in this order)

```bash
./.venv-exec/bin/python main.py doctor             # machine ready?
./.venv-exec/bin/python -m pytest -q tests         # ~10 min — expect 1020+ passed, 4 skipped
make eval                                          # expect GATE: PASS
bash demo/prepare_demo.sh                          # all ports 200 (8930 may be 500 — it is an API)
set -a; . ~/Downloads/qax-corpus/.bench.env; set +a
make bench-functional                              # ~25 min — 15 sets equal to truth
```

Healthchecks rate-limits logins after many runs in a day. Its cases then come out ERROR, cause environment
(never FAIL). That is the designed behaviour, so wait and re-run.

## 6. Where things are

| | |
|---|---|
| Plan and decision log | `strategy/PLAN.md` |
| Product audit, strategy, roadmap (P0–P3), findings E1–E16, AI architecture §4a/§4b | `strategy/intel/PRODUCT_AUDIT_2026-10-20.md` |
| Competitors (incl. ContextQA Ship) | `strategy/intel/COMPETITORS_2026-10-20.md` |
| Functional evidence (corpus, before/after, real bugs, independent author, mutants) | `strategy/evidence/FUNCTIONAL_QA_CORPUS_2026-10-19.md` |
| Every claimed number, its truth and re-run command | `/how-we-measure` (`web/static/measure.html`) |
| What changed | `CHANGELOG.md` |
| Demo for a manager | `demo/DEMO_SCRIPT.md`, `demo/prepare_demo.sh`, `demo/stop_demo.sh` |
| MCP tools for coding agents | `docs/MCP.md` |

**Commands added in this stretch:**
- `release-check`: GO / NO-GO / NOT PROVEN, one decision per change.
- `reproduce`: a bug ticket → REPRODUCED or NOT REPRODUCED.
- `import-recording`: Chrome Recorder or Playwright codegen → test cases.
- `execute --only`.

**Make targets:** `eval-functional-mutants`, `eval-release`, `eval-repro`, `audit-deps`.

## 7. Open items

**Waiting on the owner (nothing engineering can do without them):**
1. AI credit, or approval to download a local model, to run `make eval-generation`. AI-written cases are
   still unmeasured; this is the biggest unknown.
2. A Jira (and Xray) trial: the story-in and bug-out loop has never run live for functional runs.
3. A repository QAX may push to, with CI: a real PR comment from `release-check` has not been tried.
4. The IP and legal review before any use at the company. Confirm who owns QAX first.
5. The positioning decision, Phase-0 interviews, and a human baseline (a person testing the same apps).

**Engineering, small:**
- Run the full test suite once on the new machine (see §1).
- The plain-language step reader (manual steps, tickets) is now strict: it refuses rather than guesses. Watch
  real tickets for wording it should learn; add each as a test.
- `QAX_SETTLE_MAX_MS` (default 6 s) bounds the wait after a click. Revisit it if a real app updates slower.

## 8. Rules that stay in force

- Do not deploy, publish, merge, or write to live Jira or Xray.
- Do not use unauthorized repositories, targets, code, credentials or data.
- Keep secrets out of logs, URLs, screenshots and reports.
- Read-only by default; consequential external writes need the operator's explicit OK.
- Commit only on `feat/functional-qa-track`. **Never push without the owner.**
- Never commit `~/Downloads/qax-corpus/.bench.env`.
- Claim only what was measured. Never say Xray works, or that a human gate passed, until it did.

## 9. To resume with Claude on the new machine

Open the project folder and say:

> Continue QAX from `docs/HANDOVER.md`. First run §5 (doctor, full suite, `make eval`) and tell me what differs from §1.
> Then pick up the open items in §7 that do not need me.
