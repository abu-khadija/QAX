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

You should see a list of **CONFIRMED** findings (44 on the bundled app) after about 30 seconds.

Prefer a browser to a terminal (for example to show QAX to a colleague)? This starts the same demo app and the web
interface together, with the target already filled in:

```bash
python main.py demo --web
```

Open the address it prints, tick the authorization box, click **Preview plan** (nothing is attacked), then **Run scan**.
It is read-only by default, so it shows the 15 findings that need no writes; untick read-only for the full 44.

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
| The bare token in `Authorization: <token>` (PocketBase, some Go/Node apps) | `--auth-scheme ""` |
| A session cookie | `--cookie "sessionid=<value>; ..."`. If the app also wants a CSRF token on writes, add `--header "CSRF-Token: <nonce>"` (the name is the app's; copy both from DevTools while logged in). `--header` can be repeated and is sent with every request |

> **Tokens that expire.** Some apps issue short-lived tokens (15 minutes is common). A scan with an expired
> token does not fail: the checks that need a login are listed under NOT TESTED. Get a fresh token and re-run.

## 3. Reading the result

| Section | What it means | What to do |
|---|---|---|
| **CONFIRMED** | Proven with a real request and response. The same scan gives the same list every time | Look at the evidence, then fix it or file it |
| **AI leads** (deep scans only) | Found by the AI layer, not proven, and may vary between runs | Check them by hand |
| **NOT TESTED** | Checks that could not run, with the reason and what would enable them | Nothing here does **not** mean "safe", it means "unknown" |
| **UNSTABLE** | Fired once but did not repeat when re-checked | Treat it as flaky until it repeats |
| **LEFT ON THE TARGET** | Test records QAX created and could not delete (never shown for `--read-only`) | Remove them by hand |

### When QAX cannot see your endpoints

QAX reads your API's OpenAPI/Swagger document when it finds one (it tries the usual places, including
`/openapi.json`, `/swagger.json`, `/apispec.json`, `/api/v1/swagger.json`). Without one it **guesses** by reading
your app's own docs pages and probing common names, and it tells you so under NOT TESTED
("QAX guessed the endpoints by name"). Three ways to give it the real surface:

```bash
# 1. Your spec is somewhere unusual (a URL, a path on the app, or a local file):
python main.py scan ... --spec /api/internal/docs.json
python main.py scan ... --spec ./openapi.yaml

# 2. No spec at all: list the endpoints yourself, one per line (a trailing ! marks a required field):
cat > endpoints.txt <<'EOF'
GET    /api/collections/notes/records
POST   /api/collections/notes/records        title!,body
GET    /api/collections/notes/records/{id}
DELETE /api/collections/notes/records/{id}
EOF
python main.py scan ... --endpoints endpoints.txt

# 3. You have the source: add --repo ./path/to/source
```

A trailing `!` on a field (`name!`) **claims it is required**: QAX will then report a finding if the app accepts the request
without it, so only mark what the app really requires.
Listing a `DELETE` route lets QAX clean up what its tests create. Without one, QAX reports the records it
could not delete under **LEFT ON THE TARGET** (its report is checked against the database in our tests).

## 4. A full scan (only if you may write test data)

Without `--read-only`, QAX also runs the checks that write data: object access between users,
mass assignment, stored XSS, input validation and more. It creates small, clearly named test records,
deletes what it can, and lists anything it could not delete.

```bash
python main.py scan --url https://staging.your-app.example --quick --authorized \
    --token "<user A token>" --token-b "<user B token, a different ordinary user>" --out full-scan.json
```

`--token-b` enables the checks where user B tries to reach user A's data. If NOT TESTED says user A "may
not create objects (HTTP 403)", creating needs a privileged account: pass an **admin or owner** token as
`--token` (user A) and an **ordinary** user's token as `--token-b`.

If the app **rate-limits** the scan (HTTP 429), QAX waits as the server asks, retries and slows down. Past a limit
of patience it stops and lists what it could not run under NOT TESTED. It never tries to get around a rate limit
or a firewall; ask whoever runs the target to allow-list your address.

## 4b. The web interface (optional)

```bash
python -m web.server        # then open http://127.0.0.1:8900/app
```

Enter the URL and (optionally) a token, a second user's token, a session cookie, extra headers (a CSRF nonce), your spec
location, an endpoint list or example bodies (the same things as the command-line options). Secrets are sent in the
request body, never in a URL. **Read-only is
on by default.** Click **Preview plan** to see what QAX will do (the endpoints it found, which checks write data,
what is missing) before anything is attacked, then run the scan. Finished scans are kept under **Past scans**.

## 4c. Functional QA end to end (web UI, real browser)

The full install adds the browser engine (Chromium) and checks the machine:

```bash
make install        # Python 3.11/3.12 venv, packages, Chromium, then `doctor`
make doctor         # what is ready and what is missing (never prints secrets)
make web            # http://localhost:8900/app → Functional QA (E2E)
```

Or in a container, with nothing to install but Docker: `make docker` (the port is published on this machine only;
an app running on your machine is `http://host.docker.internal:<port>` from inside the container).

In **Functional QA (E2E)**, give QAX the app's URL (and a test login), the repository (a folder or an https Git URL)
and the user story — or a Jira story key like `PROJ-123`. It shows what it understood (pages, criteria, the security
risks the story implies), writes test cases traced to each criterion, and lets you **export** them (CSV/JSON) or
**share** them to Jira/Xray (dry run unless live export is enabled). Then **Run tests**: PASS / FAIL per case with a
screenshot of every step, the page errors it saw, accessibility and performance observations, and what changed since
the last run. Failures become Jira bugs (English or Arabic), linked to the story, with the requirement verdicts as a
comment on it — previewed until an operator enables live export. Writing test cases needs an AI provider (`ANTHROPIC_API_KEY`, or any OpenAI-compatible API: see the README);
everything else works without AI (load your own cases file instead).

For coding agents: `python main.py mcp` serves the same tools over MCP — see [docs/MCP.md](docs/MCP.md).

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
  AI provider (Anthropic by default, or an OpenAI-compatible API you configure), needs its key in `.env`, and sends the API
  surface (and the source code if you pass `--repo`) to that provider. Measured once: ~$0.13 at Sonnet list prices on the golden test app.
- Apps **without an OpenAPI/Swagger document** are scanned less thoroughly unless you give QAX the surface
  (`--spec`, `--endpoints`, `--repo`). It says so under NOT TESTED.
- A **username/password login form** is not supported: log in yourself and pass the cookie or token.
- In cookie mode there is only **one identity** (your cookie), and the "no credentials" control requests of the
  token-based checks (BFLA, BOLA, JWT, forged-token) also carry your cookie, so those are listed as NOT TESTED.
  Use `--token` / `--token-b` where the app supports API tokens.
- Endpoints whose create calls need **valid domain data** QAX cannot guess (a foreign key, "already exists") are
  listed as NOT TESTED. Give QAX one valid body per create endpoint and it will use it:

  ```bash
  cat > examples.json <<'EOF'
  {"POST /api/v1/pages": {"title": "QAX page {{unique}}", "route": "qax-{{unique}}", "content": "hello",
                          "format": "markdown", "link_target": "_blank"}}
  EOF
  python main.py scan ... --examples examples.json
  ```
  `{{unique}}` is replaced by a fresh value for every request (use it for fields the app requires to be unique).
- Measured on ten open-source apps run locally; this is not a general accuracy figure.
- The scan sends many requests in a short time (hundreds on a small app). Tell whoever runs the staging
  environment before you scan, and expect rate limits on some apps.

## 7. Problems

| You see | Fix |
|---|---|
| `nothing is listening at ...` / `could not be resolved` / `did not answer` | QAX could not reach the address at all and stopped (an unreachable target is an error, not a clean scan). Check the URL, the port, your VPN |
| `could not verify the TLS certificate` | The server uses a self-signed or internal-CA certificate. For a staging server **you control**, add `--insecure` |
| `Refusing to scan` | Add `--authorized` (and only if you are allowed to test the app) |
| `could not self-register or log in` | Pass `--token` |
| `needs two supplied identities` / `--token-b` | Pass a second, ordinary user's token with `--token-b` |
| `knows only N endpoint(s)` / `guessed the endpoints by name` | QAX could not find your API's endpoints; see "When QAX cannot see your endpoints" |
| `valid token was not accepted` / many checks NOT TESTED after a while | The token expired; get a fresh one |
| `user A may not create objects (HTTP 403)` | Use an admin/owner token as `--token`, an ordinary user's as `--token-b` |
| `QAX stopped sending requests ... rate-limiting` | The target kept answering HTTP 429; re-run later or allow-list the scanner |

More (browser runs, Docker, the web server, MCP): [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## 8. Feedback

What helps us most:
1. What you expected QAX to find, and whether it found it.
2. Any finding you think is **wrong**, with the reason.
3. Anything in the output you did not understand.

The `--out` file contains your app's endpoint names and the evidence. Look through it, and remove
anything sensitive before you send it.
