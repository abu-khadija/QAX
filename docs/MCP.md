# QAX for coding agents (MCP)

`python main.py mcp` serves QAX over the Model Context Protocol (stdio). An agent that has just
changed code can ask QAX to **prove** the running app still works — instead of trusting its own tests.

```bash
# Claude Code
claude mcp add qax -- /path/to/QAX/.venv-exec/bin/python /path/to/QAX/main.py mcp
```

| Tool | What it does |
|---|---|
| `qax_doctor` | Is this machine ready (Python, browser, git, integrations)? |
| `qax_read_app` | Read the running app from its URL: pages, elements, stable selectors (saved per project). |
| `qax_write_tests` | Understand URL + repository + story/criteria + code; write ISTQB cases traced to the criteria, security cases included. Needs `ANTHROPIC_API_KEY`. |
| `qax_run_tests` | Run cases (inline or a cases file) in a real browser or over HTTP: PASS/FAIL per case with evidence, page errors, accessibility/performance observations, changes since the last run. |
| `qax_scan` | Security scan with deterministic probes; confirmed findings separate from leads. Read-only by default. |
| `qax_bug_preview` | Failed results → ISTQB-shaped Jira bug drafts. |

Safety — the same gates as the web UI:
- every tool that sends a request to an app needs `authorized: true` (the agent must ask the operator);
- scans are read-only unless the agent passes `read_only: false`;
- **nothing is written to Jira or Xray over MCP** — bugs are previews; filing stays a human decision in the web UI or CLI;
- secrets in `.env` are never returned; QAX logs go to stderr, never into the protocol stream.
