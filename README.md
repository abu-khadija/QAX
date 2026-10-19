# QAX

**QAX proves what your running app actually does.** It attacks your API (and tours your UI),
keeps only the bugs it can prove with a real request and response — checked against a control
request — explains the fix, and drafts the Jira bug for you. It also generates test cases from
source code. Runs locally. Built for QA teams.

---

## See it work in under a minute (no API key, no account, no config)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-scan.txt     # lean: just what the scanner needs
python main.py demo
```

`demo` boots QAX's bundled, deliberately-vulnerable app on a free local port, runs a quick scan
(deterministic probes — zero LLM calls), prints the confirmed findings, and stops the app. On the
author's machine that is **about 30 seconds from an empty virtualenv** to 44 confirmed findings
(measured 2026-10-17: 2 s venv + 7 s `pip install` with a warm cache + 18 s scan; a cold pip cache adds 15–20 s).

## Scan your own app

```bash
python main.py scan --url https://your-app.example --quick --authorized \
    --token <a valid token> --token-b <a second, ordinary user's token>
```

* Only scan apps you are allowed to test (`--authorized` is your confirmation). Some probes
  create disposable test records; QAX removes what it can and **lists what it left behind**.
* **Confirmed** findings come from deterministic probes: scan again and you get the same list.
  `--reverify 2` re-runs the probes and confirms only what reproduces every time (and stamps a
  measured "Reproduced 3/3" on the ticket); anything flaky is reported as *unstable*.
* **`NOT TESTED`** lists the checks that could not run (no second user's token, a probe that
  crashed, …) with what would unblock them. A quiet result there means "unknown", not "safe".
* **`--read-only`** (or `QAX_READ_ONLY=1` in CI) sends no write request at all — only GET/HEAD/OPTIONS
  leave QAX (enforced on the HTTP session and proven in `make eval` by counting requests on the server).
  Checks that need to create data, register users or log in are listed under NOT TESTED. Use it for a
  first scan of a staging you do not own; pair it with `--token`.
* **Known / by-design findings** go in `.qax/accepted.json` (or `--accepted FILE`, `QAX_ACCEPTED` in CI):
  `{"accepted": [{"probe": "cors", "target": "/", "category": "by-design", "reason": "…", "expires": "2027-03-01"}]}`.
  A `reason` and an `expires` date (at most a year ahead) are required; an entry without them is rejected.
  Accepted findings are not counted, not blocking and not filed — but they are always listed under
  **ACCEPTED**, an expired acceptance counts again, and an entry that matches nothing is reported.
* A **quick** scan needs no API key. A **deep** scan (AI test generation, red-team, critic)
  needs `ANTHROPIC_API_KEY` in `.env` (about $0.5–1 per run).

**Compare two scans:** `python main.py diff before.json after.json` → NEW · STILL THERE · GONE ·
NOT RE-CHECKED · FLAKY? · ACCEPTED. A finding that disappeared is only GONE when its probe really ran on a
changed build; if the probe could not run it is NOT RE-CHECKED, and if the build did not change it is
FLAKY? — never "fixed". `--fail-on-new P1,P2` makes it a CI check.

## From finding to Jira, and back

```bash
python main.py scan ... --out scan.json                 # 1. find
python main.py file-findings --from scan.json           # 2. preview the Jira bugs (nothing is filed)
python main.py file-findings --from scan.json --live    #    file them (needs JIRA_* in .env)
python main.py verify-fixes --from fresh_scan.json      # 3. later: are they actually fixed?
```

* Bugs follow a real QA bug-report layout (steps with a direct URL, expected as a MUST rule,
  evidence, fix, reproducibility, workaround) and carry the evidence as an attachment.
  `--client-safe` writes tickets with no tool name in them. Fields your Jira project doesn't
  have are left out, and a repeat scan never files a duplicate.
* `verify-fixes` answers **FIXED / STILL REPRODUCES / COULD NOT TEST** — never rounded together.
  A probe that merely went quiet is not clearance, and a bug that vanishes on an unchanged build
  is flagged as flaky. QAX never closes a ticket on its own (`--apply` shows the plan,
  `--apply --yes` writes, and only for FIXED tickets).

## How we know it works

`make eval` boots a golden vulnerable app and a fixed copy of it: every planted bug must be
caught (32/32), nothing may be flagged on the fixed copy, two scans must agree, and a quick scan
must work with no API key. Five real open-source apps (Node, Go, Java, Django, Gitea) have been
scanned with 0 false alarms — QAX raised false alarms on two of them first; the case studies in
`strategy/` say so and show the fixes.

---

## Functional QA: generate → run in a real browser → report → Xray

```bash
pip install -r requirements.txt && playwright install chromium   # full set + the browser
cp .env.example .env                     # ANTHROPIC_API_KEY for generation
python main.py pipeline --input story --source "As a user I can reset my password" \
    --exec-mode ui --base-url http://localhost:8000 --plan-only   # 1. see the plan first
python main.py pipeline --input story --source "…" --exec-mode ui --base-url http://localhost:8000
python main.py execute --file output/pipeline/<run>/test_cases.json --project <yours>  # re-run
```

* Generated cases are **AI proposals until they run**. A case whose steps did not all run is
  **SKIP/ERROR, never PASS**. Every run writes the cases, the raw results and an HTML report
  (screenshot + page snapshot of each failure) to `output/pipeline/<timestamp>/`.
* Engines: `--exec-mode api` (HTTP, cases with `api_steps`), `ui` (Playwright; selectors from
  `config/projects/<project>/selectors.json`), `both`.
* **Nothing is written outside your machine without `--live`** (and `DRY_RUN` not `true`): test
  issues in Xray/Zephyr, results as one Xray Test Execution, auto-filed Jira bugs. Results go
  to Xray only under the Jira keys the export created — never under local ids.
* **No page content goes to the AI provider during execution** unless you pass `--ai-heal`
  (selector repair sends the page HTML / a screenshot).
* The web UI (`python -m web.server` → `/app`, *Generate test cases*) does the same: generate or
  load a cases JSON, **Preview run**, then **Run tests** with the HTTP or Browser engine.
* Try it with no API key: `python evals/targets/ui_demo_app.py --port 8911`, then run
  `evals/data/ui_demo_cases.json` with `--project uidemo` — 3 PASS, 1 FAIL (a planted bug), 1 SKIP.
* Xray has **not** been exercised against a real Xray instance yet.

```bash
python main.py analyze --file your_file.cs --project example --dry-run   # generate only
python main.py analyze --file your_file.cs --project example --yes       # + push to Xray (asks otherwise)
```

---

## Configuration

Copy `.env.example` to `.env` and fill in these variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | **Yes** | Claude API key — get from console.anthropic.com |
| `JIRA_BASE_URL` | Yes (for push) | Your Jira Cloud URL, e.g. `https://company.atlassian.net` |
| `JIRA_EMAIL` | Yes (for push) | Email you use to log into Jira |
| `JIRA_API_TOKEN` | Yes (for push) | Jira API token (not your password) — generate at id.atlassian.com |
| `JIRA_CLOUD_ID` | Yes (for push) | Found in Jira admin settings |
| `JIRA_PROJECT_KEY` | Yes (for push) | Jira project key, e.g. `EXAMPLE` |
| `XRAY_CLIENT_ID` | Yes (for push) | Xray API key — Jira → Apps → Xray → API Keys |
| `XRAY_CLIENT_SECRET` | Yes (for push) | Xray API secret |
| `DEFAULT_PROJECT` | No | Project context to load. Default: `example` |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` / `ERROR`. Default: `INFO` |
| `DRY_RUN` | No | `true` = never push to Jira. Default: `false` |
| `ENABLE_SCREENSHOT` | No | `true` = capture UI screenshots (requires Playwright) |
| `EXAMPLE_BASE_URL` | No | EXAMPLE test environment URL for screenshots |
| `EXAMPLE_USERNAME` | No | EXAMPLE login username for screenshots |
| `EXAMPLE_PASSWORD` | No | EXAMPLE login password for screenshots |

---

## Commands

### analyze — Generate test cases from source code

```bash
# Single file
python main.py analyze --file path/to/Controller.cs --project example

# Dry run (no Jira push)
python main.py analyze --file path/to/Controller.cs --project example --dry-run

# Entire folder
python main.py analyze --folder src/Orders/ --ext .cs --project example

# With module hint (improves test case quality)
python main.py analyze --file ChecklistController.cs --module Orders --project example

# Large file: split into 300-line chunks
python main.py analyze --file BigController.cs --chunk-size 300 --project example

# Include dependency files for richer context
python main.py analyze --file Form.cs --include-deps --project example

# Include git history risk analysis
python main.py analyze --file Form.cs --include-git --project example

# Include Jira bug patterns (requires --module and Jira creds)
python main.py analyze --file Form.cs --module Orders --include-bugs --project example

# Save output as Markdown (for PR comments)
python main.py analyze --file Form.cs --output pr_comment.md --project example

# Skip pushing test cases below confidence score 5
python main.py analyze --file Form.cs --skip-low-confidence 5 --project example
```

### run — Config-driven pipeline with session tracking

```bash
# Run a preset config
python main.py run --config configs/smoke.json --project example

# Run by mode (uses default config for that mode)
python main.py run --mode smoke --project example
python main.py run --mode regression --project example

# Generate mode: analyze one file
python main.py run --mode generate --file path/to/file.cs --project example

# Module mode: analyze all files in a module
python main.py run --mode module --module Orders --project example

# Bug mode: generate tests targeting specific Jira bugs
python main.py run --mode bugs --bugs EXAMPLE-101,EXAMPLE-205 --project example

# Release mode: generate regression tests for a version
python main.py run --mode release --version 12.28.5 --project example

# List recent sessions
python main.py run --sessions
```

### execute — Run test cases with Playwright browser

```bash
# Execute a single test case (fetches from Xray by ID)
python main.py execute --test-case EXAMPLE-501 --project example

# Execute a suite
python main.py execute --suite smoke --project example
python main.py execute --suite regression --project example

# Execute from a local JSON file
python main.py execute --file output/test_cases_20260319.json --project example

# Watch execution (headed browser)
python main.py execute --suite smoke --headless false --project example

# Upload results to Xray after execution
python main.py execute --suite smoke --upload --project example

# Stop on first failure
python main.py execute --suite smoke --stop-on-fail --project example
```

### feedback — Review and improve generated test cases

```bash
python main.py feedback --accept TC-TRK-0001
python main.py feedback --reject TC-TRK-0001
python main.py feedback --stats
python main.py feedback --optimize
```

### check-connection — Test all API connections

```bash
python main.py check-connection
```

---

## Projects

QAX supports multiple projects. Each project has its own context file and selectors file.

### Current projects

| Project | Context file | Selectors file |
|---------|-------------|----------------|
| `example` | `config/projects/example/context.md` | `config/projects/example/selectors.json` |
| `example2` | `config/projects/example2/context.md` | _(add when needed)_ |
| `example3` | `config/projects/example3/context.md` | _(add when needed)_ |

### Adding a new project

1. Create a folder: `config/projects/myproject/`
2. Copy `config/projects/example2/context.md` to `config/projects/myproject/context.md`
3. Fill in the context file with your platform details
4. Optionally copy `config/projects/example/selectors.json` as a starting point for selectors
5. Set `DEFAULT_PROJECT=myproject` in `.env` or pass `--project myproject` on each command

---

## First Run — Step by Step

**Step 1 — Install Python 3.11 or 3.12** from python.org (3.14 works but Playwright requires 3.11/3.12)

**Step 2 — Install dependencies**

```bash
cd D:\AI\qax
pip install -r requirements.txt
```

**Step 3 — Configure**

```bash
copy .env.example .env
```

Open `.env` in any text editor. Fill in `ANTHROPIC_API_KEY`. Get your key at: console.anthropic.com/account/keys

**Step 4 — Test the connection**

```bash
python main.py check-connection
```

You should see: `Claude API: OK`

**Step 5 — Analyze your first file**

```bash
python main.py analyze --file "D:\path\to\YourController.cs" --project example --dry-run
```

`--dry-run` means nothing is pushed to Jira. You'll see generated test cases in `output/`.

**Step 6 — Review output**

Open `output/test_cases_*.json`. Check the quality report — look for `avg_confidence`. Score 8+ is excellent.

**Step 7 — Push to Jira**

Fill in Jira/Xray fields in `.env`, then run without `--dry-run`:

```bash
python main.py analyze --file "D:\path\to\YourController.cs" --project example
```

---

## Troubleshooting

**`ANTHROPIC_API_KEY is not set`**
Run `copy .env.example .env` and add your API key.

**`Project context file not found`**
Make sure `config/projects/example/context.md` exists. See the Projects section above.

**`JSON parse failed — retrying with correction prompt`**
Handled automatically. If it happens on every chunk, check that `config/system_prompt.md` starts with `OUTPUT FORMAT — READ THIS FIRST`.

**`Insufficient API credits`**
Add credits at: console.anthropic.com/settings/billing

**`Playwright not installed`**
Requires Python 3.11 or 3.12. Install with:
```bash
pip install playwright==1.44.0 && playwright install chromium
```

**Duplicate TC IDs (e.g. `TC-TRK-0001_B`)**
This is automatic deduplication. Two chunks generated a test for the same scenario — the `_B` suffix is applied automatically and is expected behavior.

**Low confidence scores (below 5)**
Provide more complete source files. Use `--include-deps` for richer context.

**`Jira API error 401`**
Check `JIRA_EMAIL` and `JIRA_API_TOKEN` in `.env`. The token is NOT your Jira password — generate one at id.atlassian.com/manage-profile/security/api-tokens.

**`No test cases generated`**
The file may contain no testable logic (config files, constants, type definitions). Try a Controller or Service file instead.

---

## Architecture

```
main.py                  CLI entry point (QAX)
core/
  claude_client.py       Anthropic API client, JSON parsing, deduplication
  code_reader.py         File reading, chunking, dependency extraction
  validator.py           Schema validation, duplicate ID detection, quality report
  xray_client.py         Jira/Xray API push
  screenshot_capture.py  Playwright screenshot capture (optional)
  feedback_store.py      Accept/reject feedback (SQLite)
  git_analyzer.py        Git history risk analysis
  bug_fetcher.py         Jira bug pattern fetching
  session_manager.py     Run session tracking
  prompt_optimizer.py    Feedback-driven prompt improvement
  markdown_reporter.py   PR comment generation
config/
  system_prompt.md       Claude system prompt (generic, works for all projects)
  xray_schema.json       JSON validation schema
  projects/
    example/
      context.md         EXAMPLE platform context (injected into every prompt)
      selectors.json     CSS selectors for Playwright execution
    example2/
      context.md         Template for EXAMPLE2 project
    example3/
      context.md         Template for Example3 project
execution/
  playwright_runner.py   Browser lifecycle management
  action_mapper.py       Natural language to Playwright action translation
  test_executor.py       Test execution orchestration
  claude_healer.py       Self-healing selectors via Claude
  results_reporter.py    HTML report generation
web/
  server.py              The one supported UI (scan, plan preview, run history, Jira export)
configs/
  smoke.json             Smoke test suite config
  regression.json        Regression test suite config
output/                  Generated test cases and reports (gitignored)
```

---

## Tech Stack

- Python 3.11+
- anthropic SDK >= 0.40.0 (Claude claude-sonnet-4-20250514)
- pydantic >= 2.0
- jsonschema
- requests
- playwright (optional — for execution and screenshots, requires Python 3.11/3.12)
- flask (the web UI and the demo)
- sqlite3 (built-in — for feedback store)
- pytest
