# QAX — handover to a new machine (2026-10-21, updated 2026-10-23 after the move)

Everything needed to continue on another machine: what to move, how to set it up, how to check it works,
where things are, and what is open. Read section 1 first.

---

## 1. State right now (re-measured on the new Mac, 2026-10-22/23)

| | |
|---|---|
| Branch | `feat/functional-qa-track` (all work since 2026-10-19 is here) |
| Last commit | see `git log -1` (after `bcd61fd`, the Windows fixes) |
| On GitHub? | **Yes, private.** `abu-khadija/QAX` was deleted by the owner and recreated private; `main` (`0be95d4`), `feat/service-layer-and-reliability-fix` and `feat/functional-qa-track` were pushed with the **clean** history on 2026-10-22. |
| Tests | **1114 passed, 0 failed** (2026-10-24, after the AI-provider, CI and dead-code work) (`PyJWT` in the dev extra runs the 3 JWT tests that used to skip) |
| Gate | `make eval` → GATE: PASS. `make eval-deep` → PASS on `gemini-3-flash-preview` (4 AI calls, 25 cases, 44 confirmed findings all from probes) |
| AI features (2026-10-23, free Gemini tier, **never Claude**) | Generation on the demo notes story: `gemini-3.5-flash` met the bar 1 of 2 completed runs, `gemini-3-flash-preview` 0 of 2. Self-healing: 1 case healed. Ticket writer: 1 ticket, buggy app REPRODUCED / fixed copy NOT REPRODUCED. n is tiny; every number belongs to its model. Eight defects found and fixed (CHANGELOG "AI features run for real") |
| Release check on GitHub | **Ran on real pull requests 2026-10-23** (QAX's own private repo, demo app): PR #1 harmless change → **GO**; PR #2 login broken → **NO-GO**, closed unmerged. PR #1 is kept open as the proof. Variables set in the repo: `QAX_APP_URL`, `QAX_PROJECT`, `QAX_REPO`, `QAX_APP_START`; branches `demo/clean-change` and `demo/break-login` on GitHub. See `strategy/PLAN.md` §9 for the two defects the first runs found |
| Benchmarks | functional **120/120 on 15 sets (×2)**; release check 4/4; bug reproduction 6/6; functional mutants 13/13; scan 32/32, 0 FP |
| Windows | first run 2026-10-23: scan works (demo 44 findings, gate scan rows 32/32). The browser track did **not** run: Chromium would not start on that machine (see §4a) |

**125 vs 120 (checked 2026-10-23):** before the move this file said "functional 125/125". Both full-run result
files of 2026-10-21 (`functional_all_2026-10-21.json` and `...21b.json`) hold **120** cases in the same 15 sets
(93 expected PASS, 25 expected FAIL, 2 NOTPASS); "125" appears only in the prose of
`strategy/evidence/FUNCTIONAL_QA_CORPUS_2026-10-19.md`. Use 120. Why the prose said 125 is still not known. The
second full run (`...21b`) had 6 flips: Healthchecks cases that went PASS then ERROR (login rate limit, cause
environment); the first had 0. The public page `/how-we-measure` now says exactly this.

**First functional run on the new Mac:** one case each in memos and beszel gave PASS then FAIL (a flip, so
`make bench-functional` exits 1). Both apps had just been started. Re-run alone, `--repeat 3`: no flip.
Watch for it again; a second flip there needs a real look.

**History rewritten on 2026-10-22.** Everything related to the former employer was removed from **every**
commit with `git filter-repo`. The current tree is byte-identical (tree `b84ecc1`), so all commit IDs changed:
`docs/COMMIT_MAP_2026-10-22.txt` maps old IDs to new ones. The evidence documents and run records already use
the new IDs.

**GitHub is clean now.** The old repository (which held the old history) was deleted by the owner on
2026-10-22; the private one holds only the rewritten history. The original history is kept only in the
private legal archive outside the repo.

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

**What the 2026-10-22 move taught (the corpus is not portable as copied):**
- **The old user's home is written inside it.** Every venv's scripts (`#!/Users/islam.hanafy/...`), `start-all.sh`
  (the flaskr line) and eShopOnWeb's build (static-asset manifests) held `/Users/islam.hanafy/`. Replace it with
  the new home in those text files. eShopOnWeb also needs its NuGet packages: `dotnet build -c Release` in
  `eShopOnWeb/src/Web` restores them.
- **Venv Python versions.** linkding, linkding-old and healthchecks use Python **3.13**; microblog uses 3.12.
  Install `python@3.13`, then `python3.13 -m venv --upgrade <app>/.venv`.
- **macOS held the moved apps.** Files that came over AirDrop carry `com.apple.quarantine`; their programs and
  native modules hung at start (0 % CPU, `_dyld_start`), even after the flag was removed. A plain copy to a
  **new folder name** ran at once. On this Mac the corpus now lives in `~/Downloads/qax-corpus-live`, and
  `~/Downloads/qax-corpus` is a symlink to it, so every documented path still works.
- After those fixes all 11 apps answer (8945, 9090–9097, 9190, 4210, 8960).

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

**Before starting anything: stop old QAX servers.** On the new Mac an old web server from another copy of the
repo (started days earlier) still held port 8900, and `prepare_demo.sh` reused it, so the browser showed an old
UI. Check `lsof -nP -iTCP:8900 -sTCP:LISTEN` and look at the process's folder.

## 4a. Windows (first tried 2026-10-23)

PowerShell, no `make`, no bash. Use `py -3.12 -m venv .venv-exec`, then `.\.venv-exec\Scripts\python.exe` for
every command (`-m pip install -e ".[dev,visual,yaml]"`, `-m playwright install chromium`, `main.py doctor`,
`main.py demo`, `-m pytest -q tests`, `-m web.server`). `demo/*.sh` and the corpus need bash and macOS builds.

Fixed in `bcd61fd` after that run (each with a test): text I/O without `encoding=` (cp1252 broke on "✅" and
Arabic, and killed a scan's API-test step); Git's CRLF checkout changed the vendored axe-core (now
`.gitattributes`); doctor looked for browsers in the Linux folder and never started one; the suite read the
developer's real `.env`; two path tests assumed "/"; the HTTPS test needed `openssl`. **These fixes have not
been re-run on Windows yet.** An existing Windows checkout must take axe-core again after the pull:
`Remove-Item core\vendor\axe-core\axe.min.js; git checkout -- core/vendor/axe-core/axe.min.js`.

**Still open on Windows: Chromium does not start** on the tester's machine (`BrowserType.launch: spawn UNKNOWN`,
`chrome.exe`: "side-by-side configuration is incorrect"), also after installing the Visual C++ Redistributable.
The commands ran inside the Codex app's sandbox, which may block programs outside the project folder; Windows
Insider build 26300 with Smart App Control is the other suspect. Next: run `main.py doctor` from a plain
PowerShell window. If Chromium stays blocked there, a QAX option to drive the preinstalled Microsoft Edge
(Playwright channel `msedge`) is the fallback; it is not built.

## 5. Check it works (in this order)

```bash
./.venv-exec/bin/python main.py doctor             # machine ready? (it now starts Chromium for real)
./.venv-exec/bin/python -m pytest -q tests         # ~10 min (all but ~30 s is browser tests) — expect 1114 passed
make eval                                          # expect GATE: PASS
bash demo/prepare_demo.sh                          # all ports 200 (8930 may be 500 — it is an API)
set -a; . ~/Downloads/qax-corpus/.bench.env; set +a
make bench-functional                              # ~25 min — 15 sets equal to truth (120 cases)
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
   still unmeasured; this is the biggest unknown. (2026-10-23: the Anthropic key answers "credit balance too
   low"; `release-check` tried an AI call and still decided 4/4 right without it.)
2. A Jira (and Xray) trial: the story-in and bug-out loop has never run live for functional runs.
3. CI on the private repository: a real PR comment from `release-check` has not been tried.
4. The IP and legal review before any use at the company. Confirm who owns QAX first.
5. The positioning decision, Phase-0 interviews, and a human baseline (a person testing the same apps).

**Engineering, small:**
- Windows: re-run the suite after `bcd61fd`; get Chromium to start (§4a).
- Explain 120 vs 125 functional cases (§1).
- `prepare_demo.sh` reuses whatever answers on 8900; it should check that the server is this checkout's QAX.
- The plain-language step reader (manual steps, tickets) is now strict: it refuses rather than guesses. Watch
  real tickets for wording it should learn; add each as a test.
- `QAX_SETTLE_MAX_MS` (default 6 s) bounds the wait after a click. Revisit it if a real app updates slower.

**Added 2026-10-23, engineering, small:**
- Re-run the release-check demo on GitHub after any change to the workflow: open a PR from `demo/clean-change`
  (expect GO) and one from `demo/break-login` (expect NO-GO, never merge). The workflow's variables are in
  Settings → Secrets and variables → Actions → Variables.
- Workflow speed, measured on GitHub 2026-10-23 (`workflow_dispatch` on the branch): cache miss 76 s for the whole job
  (install 41 s), cache hit 66 s (cache restore 5 s, install 29 s): the Chromium cache saves about 10 s. The apt
  libraries are not cached and their speed varies a lot: the same step took 338 s (job 373 s) on a slow-mirror run.
  A Playwright container image would remove that variance; not tried.
- Re-running a pull request with close + reopen can run the OLD workflow: GitHub recomputes the PR's merge commit
  lazily. To test a workflow change use `gh workflow run qax-release-check.yml --ref <branch>` (the workflow has
  `workflow_dispatch`) or push to the PR's branch.
- `qax verify-fixes` and the Jira filing still run only from the command line; the web UI has "What changed between
  two scans?" (`/api/diff`), not the Jira-linked re-check.
- Removed as dead (static check, 2026-10-23): three services, four result types, two functions; see CHANGELOG.

**AI features: open after 2026-10-23 (nothing here is hidden):**
- Generation has only been measured on **one broken-app story** and never on Claude. Credit (~2026-10-29) → `make eval-generation`
  with Anthropic, three runs, and read the numbers per model. Two healthy stories (`logout`, `delayed-save`) were added; they had
  not been run live when this was written (the free tier's daily cap).
- `TC-SEC-0002` (a false failure, `gemini-3.5-flash`, 3 of 3 runs that generated it) could not be read: the record kept only ids. The
  harness now records the step and the engine's words for every false failure, the validator's reason for every refused case, and the
  generated cases themselves. The same day's reading of the new records found four causes of the SAME kind (a case-writing slip,
  not an app bug) and fixed them: a text check riding on a click (now triaged "unclear" and the prompt has a wrong/right example),
  an id area of 6+ letters (`TC-LOGOUT-0001`, refused by the schema: the story scored 0 cases 3 times; the validator now shortens it),
  and `not_visible` + a text on `page` (now read as "that text is not shown"). Whether `TC-SEC-0002` was one of these is NOT proven.
- After those fixes (`gemini-3.5-flash`, 2026-10-25, tiny n): `notes` met the bar 3 of 3 runs; `logout` went from 0 cases to bar met
  in 1 of 2 runs; `delayed-save` still missed in 2 of 2 (a case the model invented on a login the story did not mention). Never run on Claude.
- The deep scan added test cases but no findings on the golden target; whether the AI finds something the probes miss is unmeasured.
- Self-healing and the ticket writer were each seen working once. Add more tickets (a fixed copy of each is the control) before
  claiming either.
- Free tier: a small DAILY request cap per model. Do not run AI jobs in parallel; name the model next to every number.

**Lessons (2026-10-23) — each is also pinned by a test or a doc line:**
1. Mocks hid eight bugs; running the AI features once with a real provider found them. Run every integration for real before
   describing it.
2. A written promise needs a caller: the "AI writer" in the verdict text and the CHANGELOG had no code path to it.
3. Two contracts that each look right can disagree (the schema's default `visible` vs the engine enforcing it).
4. Never decide on `pytest | tail`: read the exit code (a commit went in with a failing test; fixed before any push).
5. Tests: use `monkeypatch` for the environment (a direct `os.environ` write leaked into later tests) and give helpers unique
   names (a second `_login_case` silently replaced the first).
6. `gh pr close/reopen` can run the OLD workflow (GitHub recomputes the merge commit lazily): test a workflow change with
   `gh workflow run <file> --ref <branch>`.
7. Read the version of anything before bumping it (`upload-artifact@v5` is still Node 20; v6 is Node 24).
8. A number without its model, n and date is a slogan. The eval prints and records the model.

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
