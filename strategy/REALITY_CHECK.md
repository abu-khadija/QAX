# QAX — 3-Repo Reality Check (protocol)

> The point: run QAX on code it (and I) have NEVER seen, with OBJECTIVE ground
> truth we did not plant, and see if it finds real bugs. This is the single test
> that turns "it works on TaskFlow" into evidence. Numbers are set BEFORE running.

## What matters when choosing a repo (selection criteria)

1. **API-heavy web backend with auth.** QAX's strength is API testing + probes
   (JWT forgery, IDOR, auth-bypass, validation). Pick apps with HTTP endpoints +
   login — NOT libraries, CLIs, or pure frontends.
2. **Runs locally with low setup.** QAX must EXECUTE against a live instance.
   Prefer SQLite/in-memory, single-service, `pip/npm install && run` or one Docker
   command. Avoid multi-service monorepos for the first pass.
3. **Objective ground truth.** Two acceptable sources:
   - **(a) Vulnerable-by-design app** with a DOCUMENTED vuln list (best for the
     security probes — IDOR, JWT, mass-assignment are known to exist).
   - **(b) Real OSS app + git history**: check out the commit JUST BEFORE a known
     bug-fix commit → the bug is provably present, and the fix is the answer key.
4. **Small-to-medium.** Fits the whole-system model digest (~90k chars). Not a
   500-file app for the first run.
5. **Diverse stacks across the 3.** Not all FastAPI (TaskFlow was FastAPI). Mix in
   a Node/Express or Django/Flask app to test generalization honestly.

## The 3 repos (shape, not locked until cloned & verified to run)

| # | Type | Why | Ground truth |
|---|---|---|---|
| 1 | **Vulnerable-by-design REST API** (e.g. a "vulnerable FastAPI/Flask API", or OWASP crAPI if setup is feasible) | Exercises the security probes on KNOWN vulns | documented vuln list |
| 2 | **Real OSS API, different stack** (Node/Express or Django) with a known fixed bug | Honest generalization + real code | pre-fix git commit |
| 3 | **Real OSS API, third stack** or a second vulnerable app | Breadth; avoid a one-stack fluke | documented or git history |

## How each run goes

1. Clone, get it running locally (localhost only). [free]
2. `python main.py scan --quick --repo <path> --url <localhost>` → **free** (probes +
   deterministic coverage, zero model calls). Record confirmed bugs.
3. When credits allow: `python main.py scan --repo <path> --url <localhost>` (deep).
4. Grade against the answer key (documented vulns / the fix commit).

## Pass / fail numbers — SET NOW (from ROADMAP M1, do not move)

- **Detection:** QAX confirms **≥ 60%** of the repo's known real bugs.
- **Trust:** **false-positive rate < 15%** of what QAX reports.
- **Beats baseline:** finds **≥ 2 real bugs a plain coding agent ("write me tests")
  misses** — else QAX is a feature, not a product.
- **Cost:** full deep scan **< $2** on a mid-size repo (with cache).
- **Generalization:** the auth-recipe / probes / templates work on a non-FastAPI
  stack WITHOUT code changes (only config).

### Kill criterion
If QAX can't beat the coding-agent baseline on 2 of 3 repos, OR false-positives
stay above ~25%, OR it only works on FastAPI → **PIVOT hard or NO-GO.** Better to
learn this for ~$6 and a weekend than after building more.

## Grading sheet (per repo)

| field | value |
|---|---|
| repo + commit | |
| stack | |
| known real bugs (answer key) | |
| QAX confirmed (quick) | |
| QAX confirmed (deep) | |
| true positives / false positives | |
| bugs a coding agent also found | |
| bugs QAX found that it missed | |
| cost | |
| generalization notes | |

## Honest caveat
The **free quick scan** only exercises the deterministic layer (probes + templates)
— it tests the security/validation classes, not the creative generation. A clean
quick-scan result is a *good sign on a subset*, not the full verdict. The full
answer needs the deep run (credits) + an external grader (not me, not you).
