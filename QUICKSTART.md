# QAX Quickstart

QAX scans a **running** web app or API. It only reports bugs it can **prove**: every finding comes
with the request that triggered it, the response, and a control request that shows the difference.
It also tells you exactly which checks it could **not** run, and why.

> **Before you start**
> - Only scan an app you own or are explicitly allowed to test.
> - Use a **staging / test** environment, never production.
> - Start with `--read-only` (QAX then sends no request that writes data).

---

## 1. Install (about 1 minute)

You need Python 3.11 or newer and `git`.

```bash
git clone https://github.com/abu-khadija/QAX.git && cd QAX
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-scan.txt
```

Check that it works: this starts a small, deliberately vulnerable demo app on your machine, scans it,
and stops it. No account and no API key needed.

```bash
python main.py demo
```

You should see a list of **CONFIRMED** findings after about 30 seconds.

## 2. Your first scan: read-only

```bash
python main.py scan --url https://staging.your-app.example --quick --authorized --read-only \
    --token "<a valid token for a test user>" --out first-scan.json
```

- `--authorized`: you confirm you are allowed to test this app. The scan will not start without it.
- `--read-only`: QAX only sends GET, HEAD and OPTIONS requests. Checks that need to create data,
  register users or log in are skipped and listed under NOT TESTED.
- `--token`: what a logged-in test user sends to the API. The easiest way to get it: log in to the
  app in your browser, open DevTools → Network, click any API request, and copy the value of its
  `Authorization` header (without the word `Bearer`).
- `--out`: saves the full result, so you can compare later scans or file the findings to Jira.

**If your app does not use `Authorization: Bearer <token>`:**

| Your app sends | Add these options |
|---|---|
| `Authorization: Token <key>` | `--auth-scheme Token` |
| `X-Api-Key: <key>` (or another header) | `--auth-header X-Api-Key --auth-scheme ""` |
| A session cookie only | `--auth-header Cookie --auth-scheme "" --token "sessionid=<value>"` (read-only scans only; not yet verified on a real app, so please tell us if it works) |

## 3. Reading the result

| Section | What it means | What to do |
|---|---|---|
| **CONFIRMED** | Proven with a real request and response. The same scan gives the same list every time | Look at the evidence, then fix it or file it |
| **AI leads** (deep scans only) | Found by the AI layer, not proven, and may vary between runs | Check them by hand |
| **NOT TESTED** | Checks that could not run, with the reason and what would enable them | Nothing here does **not** mean "safe", it means "unknown" |
| **UNSTABLE** | Fired once but did not repeat when re-checked | Treat it as flaky until it repeats |
| **LEFT ON THE TARGET** | Test records QAX created and could not delete (never shown for `--read-only`) | Remove them by hand |

If NOT TESTED says QAX **knows only a few endpoints**, it could not find your API's endpoints. QAX
finds them best from an OpenAPI/Swagger document (for example `/openapi.json`, `/swagger.json` or
`/api/schema/`). If your app has one somewhere else, tell us where.

## 4. A full scan (only if you may write test data)

Without `--read-only`, QAX also runs the checks that write data: object access between users,
mass assignment, stored XSS, input validation and more. It creates small, clearly named test records,
deletes what it can, and lists anything it could not delete.

```bash
python main.py scan --url https://staging.your-app.example --quick --authorized \
    --token "<user A token>" --token-b "<user B token, a different ordinary user>" --out full-scan.json
```

`--token-b` enables the checks where user B tries to reach user A's data.

## 5. Next steps

```bash
python main.py diff first-scan.json full-scan.json          # what is new, gone, or not re-checked
python main.py file-findings --from full-scan.json          # preview the Jira bugs (nothing is filed)
python main.py scan ... --reverify 2                        # keep only findings that repeat 3/3
```

To file the findings to Jira for real, add `--live` and set `JIRA_BASE_URL`, `JIRA_EMAIL` and
`JIRA_API_TOKEN` in a `.env` file.

## 6. Known limits (today)

- **The quick scan sends no data to any AI service.** A deep scan (without `--quick`) uses the
  Anthropic API, needs `ANTHROPIC_API_KEY` in `.env`, costs about $0.5–1 per run, and sends the API
  surface (and the source code if you pass `--repo`) to Anthropic.
- Apps **without an OpenAPI/Swagger document** are scanned much less thoroughly. QAX says so under
  NOT TESTED.
- Login by **username/password form** or **cookie session with CSRF** is not supported yet. Use a
  token, an API key, or the cookie workaround above for read-only scans.
- The scan sends many requests in a short time (hundreds on a small app). Tell whoever runs the staging
  environment before you scan, and expect rate limits on some apps.

## 7. Problems

| You see | Fix |
|---|---|
| `Refusing to scan` | Add `--authorized` (and only if you are allowed to test the app) |
| `could not self-register or log in` | Pass `--token` |
| `needs two supplied identities` / `--token-b` | Pass a second, ordinary user's token with `--token-b` |
| `knows only N endpoint(s)` | QAX could not find your API's endpoints; see section 3 |

## 8. Feedback

What helps us most:
1. What you expected QAX to find, and whether it found it.
2. Any finding you think is **wrong**, with the reason.
3. Anything in the output you did not understand.

The `--out` file contains your app's endpoint names and the evidence. Look through it, and remove
anything sensitive before you send it.
