"""
core/mcp_server.py — QAX as an MCP server (the proof tool of coding agents)
==========================================================================
`python main.py mcp` speaks the Model Context Protocol over stdio (JSON-RPC 2.0, one
message per line), so Claude Code, Cursor and other agents can ask QAX to PROVE a change:
read the running app, write traced test cases, run them in a real browser, scan for
security bugs, and preview the Jira bugs.

Every tool goes through the same code and gates as the web UI (the Flask routes are
called in-process): the caller must confirm authorization for any request to a target,
scans are read-only unless asked otherwise, and NOTHING is written to Jira or Xray from
here — bugs are dry-run previews only. No extra dependency. Never raises.

Register it (Claude Code):  claude mcp add qax -- /path/to/.venv-exec/bin/python /path/to/main.py mcp
"""

from __future__ import annotations

import contextlib
import json
import sys

from core.public_error import public_error

PROTOCOL = "2025-06-18"
_MAX = 60_000          # characters of JSON per tool answer

_AUTH = {"type": "boolean", "description": "You confirm the operator owns this app or is authorized to test it."}
TOOLS = [
    {"name": "qax_doctor", "description": "Check this machine is ready to run QAX (Python, browser, git, integrations).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "qax_read_app",
     "description": "Read a running web app from its URL: pages, interactive elements and stable selectors "
                    "(saved for the project, used by later test runs). Logs in when credentials are given.",
     "inputSchema": {"type": "object", "required": ["url", "authorized"], "properties": {
         "url": {"type": "string"}, "project": {"type": "string"}, "username": {"type": "string"},
         "password": {"type": "string"}, "authorized": _AUTH}}},
    {"name": "qax_write_tests",
     "description": "Understand the inputs (app URL, repository folder/URL, user story or acceptance criteria, "
                    "code) and write ISTQB test cases traced to the criteria, including security cases. "
                    "Returns the understanding, the cases and requirement coverage.",
     "inputSchema": {"type": "object", "properties": {
         "url": {"type": "string"}, "repo": {"type": "string"}, "story": {"type": "string"},
         "code": {"type": "string"}, "project": {"type": "string"}, "username": {"type": "string"},
         "password": {"type": "string"}, "authorized": _AUTH}}},
    {"name": "qax_run_tests",
     "description": "Run test cases against the running app (engine 'ui' = real browser, 'api' = HTTP) and return "
                    "PASS/FAIL per case with step evidence, page errors, accessibility/performance observations "
                    "and what changed since the last run. A case that did not fully run is never a PASS.",
     "inputSchema": {"type": "object", "required": ["url", "authorized"], "properties": {
         "url": {"type": "string"}, "test_cases": {"type": "array", "items": {"type": "object"}},
         "cases_file": {"type": "string", "description": "A JSON file of test cases (instead of test_cases)."},
         "project": {"type": "string"}, "engine": {"type": "string", "enum": ["ui", "api"]},
         "read_only": {"type": "boolean", "description": "API engine only; default false for runs."},
         "changed_since": {"type": "string", "description": "Run only the cases a change can affect: a git ref (e.g. origin/main)."},
         "repo": {"type": "string", "description": "The app's local git checkout (with changed_since)."},
         "authorized": _AUTH}}},
    {"name": "qax_scan",
     "description": "Security scan of the running app with deterministic probes; reports only what it proves "
                    "(confirmed) separately from leads. Read-only by default.",
     "inputSchema": {"type": "object", "required": ["url", "authorized"], "properties": {
         "url": {"type": "string"}, "read_only": {"type": "boolean"}, "token": {"type": "string"},
         "spec_url": {"type": "string"}, "authorized": _AUTH}}},
    {"name": "qax_bug_preview",
     "description": "Turn failed results into ISTQB-shaped Jira bug drafts (dry run: nothing is filed).",
     "inputSchema": {"type": "object", "required": ["results"], "properties": {
         "results": {"type": "array", "items": {"type": "object"}},
         "test_cases": {"type": "array", "items": {"type": "object"}}, "url": {"type": "string"}}}},
]


def _call_route(path: str, body: dict) -> dict:
    """Call a web route in-process with the UI's own gates; returns its JSON (or an error)."""
    from web.server import app
    with app.test_client() as c:
        r = c.post(path, json=body, headers={"Host": "localhost"})
        try:
            data = r.get_json() or {}
        except Exception:  # noqa: BLE001
            data = {}
        if r.status_code >= 400 and "error" not in data:
            data["error"] = f"HTTP {r.status_code}"
        return data


def call_tool(name: str, args: dict) -> "tuple[dict, bool]":
    """Run one tool. Returns (result, is_error). Never raises."""
    args = args or {}
    try:
        if name == "qax_doctor":
            from core.doctor import run_checks
            return {"checks": run_checks()}, False
        if name == "qax_read_app":
            d = _call_route("/api/map", {**args, "authorized": bool(args.get("authorized"))})
        elif name == "qax_write_tests":
            d = _call_route("/api/functional/analyze", {**args, "authorized": bool(args.get("authorized"))})
        elif name == "qax_run_tests":
            cases = args.get("test_cases")
            if not cases and args.get("cases_file"):
                from pathlib import Path as _P
                f = _P(args["cases_file"]).expanduser().resolve()
                if not (f.suffix == ".json" and (f.is_relative_to(_P.cwd().resolve())
                                                 or f.is_relative_to(_P(__file__).resolve().parent.parent))):
                    return {"error": "cases_file must be a .json file inside the working folder or QAX"}, True
                with open(f, encoding="utf-8") as fh:
                    raw = json.load(fh)
                cases = raw if isinstance(raw, list) else raw.get("test_cases", [])
            d = _call_route("/api/execute", {"tests": cases or [], "url": args.get("url", ""),
                                             "project": args.get("project", ""),
                                             "engine": args.get("engine", "ui"),
                                             "read_only": bool(args.get("read_only", False)),
                                             "changed_since": args.get("changed_since", ""), "repo": args.get("repo", ""),
                                             "authorized": bool(args.get("authorized"))})
        elif name == "qax_scan":
            d = _call_route("/api/scan", {"url": args.get("url", ""), "token": args.get("token", ""),
                                          "spec_url": args.get("spec_url", ""),
                                          "read_only": args.get("read_only", True) is not False,
                                          "authorized": bool(args.get("authorized"))})
        elif name == "qax_bug_preview":
            d = _call_route("/api/functional/bugs", {"results": args.get("results") or [],
                                                     "tests": args.get("test_cases") or [],
                                                     "url": args.get("url", ""), "dry_run": True})
        else:
            return {"error": f"Unknown tool: {name}"}, True
        return d, bool(d.get("error")) and not (d.get("tests") or d.get("results") or d.get("pages"))
    except Exception as exc:  # noqa: BLE001
        return {"error": public_error("QAX tool failed", exc)}, True


def handle(msg: dict) -> "dict | None":
    """Answer one JSON-RPC message (None for notifications). Never raises."""
    mid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params") or {}
    if mid is None:                       # notifications (initialized, cancelled …)
        return None
    try:
        if method == "initialize":
            from core.version import __version__
            result = {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "qax", "version": __version__},
                      "instructions": "QAX proves behaviour on a RUNNING app. Ask the operator before "
                                      "setting authorized=true; never test apps they do not own."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            out, is_err = call_tool(params.get("name", ""), params.get("arguments") or {})
            text = json.dumps(out, default=str)
            if len(text) > _MAX:
                text = text[:_MAX] + '… (truncated)'
            result = {"content": [{"type": "text", "text": text}], "isError": is_err}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as exc:  # noqa: BLE001
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": public_error("QAX internal error", exc)}}


def serve(stdin=None, stdout=None) -> None:
    """Read JSON-RPC lines from stdin, answer on stdout; everything else QAX prints goes to stderr."""
    stdin, out = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:  # noqa: BLE001
            out.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32700, "message": "parse error"}}) + "\n")
            out.flush()
            continue
        with contextlib.redirect_stdout(sys.stderr):      # stdout is the protocol channel
            reply = handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            out.write(json.dumps(reply, default=str) + "\n")
            out.flush()
