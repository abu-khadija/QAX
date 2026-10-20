# QAX — Troubleshooting

Start with `make doctor` (or `python main.py doctor`): it checks Python, the packages, the browser,
git, the output folder and which integrations are configured, and prints the exact fix for each line.

## Install and start

| You see | Fix |
|---|---|
| `doctor` says Python ✗ | Use Python 3.11 or 3.12 (`brew install python@3.12`); Playwright 1.44 does not support newer versions. Then `make install`. |
| `Browser engine: Chromium not found` | `.venv-exec/bin/python -m playwright install chromium` |
| `make install` fails part-way or imports hang | Delete `.venv-exec` and run `make install` again from a normal terminal (some embedded terminals cannot load native libraries). |
| `refusing to listen on 0.0.0.0 without QAX_API_TOKEN` | You asked the web server to listen beyond this machine. Set `QAX_API_TOKEN` in `.env` first (the UI asks for it once per tab). |
| The UI answers `this host is not allowed` | You opened it by a name other than `localhost`/`127.0.0.1`. Set `QAX_API_TOKEN` and add the name to `QAX_ALLOWED_HOSTS`. |
| `cross-site request refused` / `send JSON` | Another web page tried to use the local QAX API. QAX refuses that on purpose; use the QAX page itself. |
| Docker: the app under test is not reachable | From inside the container your machine is `http://host.docker.internal:<port>`, not `localhost`. |

## Functional QA (browser runs)

| You see | Fix |
|---|---|
| `No element named 'x' on this page` (ERROR, not FAIL) | The case names an element QAX never saw. Run **Read the app** (or `main.py map --url …`) for the project, or fix the step's `selector_key`. |
| A step on an embedded form fails | Elements inside an iframe are read as `iframe#… >>> #element`. Re-read the app so the map has them. |
| `login did not succeed` while reading | Check the test user; QAX fills the app's own login form. Apps with SSO/2FA: log in by hand and run with a session cookie (scan) or a cases file that starts after login. |
| Runs pass locally, fail on CI | Run with `--trace` and open it with `playwright show-trace`; most cases are timing (QAX waits for a stable page) or data left by an earlier run (use `{{unique}}` in values). |
| `N request(s) that change data were sent` | Browser runs create real data through the UI. Use a disposable staging app; QAX does not undo it. |
| A text check fails although the record was saved | Lists that render only the visible rows (virtualized tables) leave older rows out of the page. Filter or search for the record first, and delete what each case creates (`cleanup_steps`) so test data does not pile up. |
| `the app redirected to … — not followed` | The app sent a request off its own origin. QAX keeps credentials on the target; if the other host is yours (an SSO provider), add it to `QAX_ALLOWED_ORIGINS`. |
| `A scan is already running` | One scan at a time; wait for it (closing its page stops it). |
| A case is `SKIP`/`ERROR`, never `PASS` | By design: a step that did not run proves nothing. The reason is on the step. |
| `Generation failed: … no credit left` | Add credit to the Anthropic account, or load your own test-cases file (Login, project and more inputs → Load a test-cases file). |

## Logins, devices, CI

| You see | Fix |
|---|---|
| The app uses SSO / 2FA / a CAPTCHA | `python main.py login --url … --project … --authorized` (or 🔐 in the UI): log in by hand, close the window. Delete it with `--forget`. |
| A saved session stopped working | It expired: record it again. |
| `--browser firefox` fails to launch | `python -m playwright install firefox webkit` (optional). |
| Mobile run looks like desktop | The page has no `<meta name="viewport">` — QAX lists it as an observation. |
| CI hangs at "Proceed with execution?" | Add `--yes` (implied when `CI=true`). |
| Parallel runs disagree with a single run | The cases depend on each other or on shared data; make each self-contained (`{{unique}}`, `cleanup_steps`). |
| Writing cases without Anthropic | `QAX_AI_PROVIDER=openai` + `QAX_AI_MODEL` (+ `QAX_AI_BASE_URL` for Azure or a local Ollama). |

## Change-based runs

| You see | Fix |
|---|---|
| Steps feel slow, or a page is judged before it finished updating | After each action QAX continues when either the network is idle or the requests the action started are done. `QAX_SETTLE=networkidle` forces the network-idle wait only. |
| `--changed-since` runs everything | A shared file changed (layout, settings, dependencies, messages) or QAX could not place a file on any page — it says which. That is the safe answer. |
| A case you expected was skipped | Each skip lists the pages the case visits. Re-read the app (`map`) if its pages changed; a case with no navigate step and no known element always runs. |
| Failures are marked "test" or "environment" | They are not bugs in the app: fix the case, or check that the app/browser answered (rate limits included), then re-run. |

## Jira / Xray

| You see | Fix |
|---|---|
| Everything says *Dry run* | Live writes are off by default. The operator sets `QAX_ALLOW_LIVE_EXPORT=true` and the `JIRA_*` values in `.env`; the UI then asks before each live write. |
| `has no readable Bug issue type` | Add the *Bug* work type to the Jira project (Project settings → Work types). |
| Xray | Configured with `XRAY_CLIENT_ID` / `XRAY_CLIENT_SECRET`. Not yet exercised against a real Xray instance — check the first export by hand. |

## MCP (coding agents)

| You see | Fix |
|---|---|
| The agent gets `Confirm you own this app …` | The tool needs `authorized: true`; the agent must ask you first. |
| Nothing happens / the agent shows a JSON error | Run `python main.py mcp` by hand and type `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`: you should get the tool list. QAX writes logs to stderr only. |
