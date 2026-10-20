"""
cli/app_cmds.py — `qax map`, `login`, `mcp`, `doctor`: reading the app, sessions, agents, readiness.
Moved verbatim out of main.py (which re-exports every name), so main.py shrinks
toward argument parsing and dispatch.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from core.session_manager import SessionManager
from core.xray_client import XrayClient

logger = logging.getLogger("qax")


def run_map_command() -> None:
    """
    Handle the 'map' subcommand: read a running app from its URL.

    Walks the app in a real browser (same-origin GET links only; logout/delete-style
    links are never followed; no form is submitted), names each page's elements without
    AI, and saves config/projects/<project>/app_map.json + selectors.json. Generation
    for that project is then grounded in the real pages and elements.

    Called when sys.argv[1] == "map".
    """
    import asyncio

    parser = argparse.ArgumentParser(
        prog="qax map",
        description="Read a running app from its URL: pages, elements and selectors for functional QA.",
    )
    parser.add_argument("--url", required=True, help="Root URL of the running app.")
    parser.add_argument("--project", required=True, help="Project name (config/projects/<name>/).")
    parser.add_argument("--start", action="append", default=[], metavar="PATH",
                        help="Extra path to start from (repeatable), e.g. --start /bookmarks")
    parser.add_argument("--max-pages", type=int, default=20, help="Hard cap on pages visited (default 20).")
    parser.add_argument("--user-env", default="", metavar="VAR",
                        help="Name of the environment variable holding the login user (e.g. QAX_VAR_USER).")
    parser.add_argument("--password-env", default="", metavar="VAR",
                        help="Name of the environment variable holding the login password.")
    parser.add_argument("--authorized", action="store_true",
                        help="Confirm you own this app or are authorized to test it (required).")
    parser.add_argument("--insecure", action="store_true",
                        help="Do not verify the TLS certificate (self-signed staging only).")
    parser.add_argument("--headed", action="store_true", help="Show the browser.")
    parser.add_argument("--explore-actions", action="store_true",
                        help="ALSO follow create/edit/delete/logout-style links (a disposable staging app "
                             "only: following them CHANGES DATA). Every write is listed.")
    parser.add_argument("--no-menus", action="store_true",
                        help="Do not open menus/dialogs to record what they reveal.")
    args = parser.parse_args(sys.argv[2:])

    if not args.authorized:
        print("\n  Refusing to map without --authorized (you own this app or may test it).\n")
        sys.exit(2)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.project):
        print("\n  --project: letters, digits, - and _ only.\n")
        sys.exit(2)
    user = os.getenv(args.user_env, "") if args.user_env else ""
    password = os.getenv(args.password_env, "") if args.password_env else ""
    if (args.user_env or args.password_env) and not (user and password):
        print(f"\n  Login variables not set: {args.user_env or '-'} / {args.password_env or '-'}\n")
        sys.exit(2)

    from core.app_mapper import map_app, save_map
    print(f"\n  QAX — reading {args.url} (max {args.max_pages} pages, GET links only)…")
    from core.session_state import state_for
    session = None if user else state_for(args.project)
    if session:
        print("  Using the session recorded with `main.py login` (SSO / 2FA).")
    app_map = asyncio.run(map_app(args.url, start_paths=args.start, max_pages=args.max_pages,
                                  username=user, password=password, headless=not args.headed,
                                  insecure=args.insecure, explore_actions=args.explore_actions,
                                  open_menus=not args.no_menus, storage_state=session))
    if app_map.error:
        print(f"\n  ✗ {app_map.error}\n")
        sys.exit(1)
    if user and app_map.login_ok is False:
        print("  ⚠️  Login did not succeed — only public pages were read.")
    path = save_map(app_map, args.project)
    n_el = sum(len(p.get("elements") or {}) for p in app_map.pages)
    print(f"  Pages read: {len(app_map.pages)} · elements named: {n_el}"
          + (f" · logged in" if app_map.login_ok else ""))
    for p in app_map.pages[:30]:
        print(f"    {p['path']:<40} {len(p.get('elements') or {}):>3} element(s)")
    if app_map.skipped_links:
        print(f"  Not followed (would log out / change data): {', '.join(app_map.skipped_links[:8])}"
              "  — add --explore-actions on a disposable staging app to map them too")
    if app_map.explored_actions:
        print(f"  Action links followed (--explore-actions): {', '.join(app_map.explored_actions[:10])}")
    n_open = sum(len(p.get("opened_by") or {}) for p in app_map.pages)
    if n_open:
        print(f"  Revealed by opening menus/dialogs: {n_open} element(s)")
    data_writes = [w for w in app_map.writes if not w.get("login")]
    if data_writes:
        print(f"  ⚠️  Writes sent while mapping: {len(data_writes)} — "
              + ", ".join(sorted({w['method'] + ' ' + w['path'] for w in data_writes})[:8]))
    print(f"  Saved: {path}")
    print(f"         {path.parent / 'selectors.json'}")
    print(f"  Next:  python main.py pipeline --input story --source \"…\" --project {args.project} "
          f"--exec-mode ui --base-url {args.url} --plan-only\n")


def run_login_command() -> None:
    """
    Handle the 'login' subcommand — log in to an SSO / 2FA app by hand once: a browser
    window opens at --url; log in, then close the window. The session is kept for the
    project (owner-only file, git-ignored) and `map` / browser runs start logged in.
    `--forget` deletes it.
    """
    import argparse
    import asyncio

    from core.session_state import forget, record_session
    parser = argparse.ArgumentParser(prog="main.py login", description=run_login_command.__doc__)
    parser.add_argument("--url", default="", help="The app's login page or home page.")
    parser.add_argument("--project", required=True, help="Project name (config/projects/<name>/).")
    parser.add_argument("--authorized", action="store_true", help="You own or may test this app.")
    parser.add_argument("--insecure", action="store_true", help="Accept a self-signed certificate.")
    parser.add_argument("--forget", action="store_true", help="Delete the saved session.")
    args = parser.parse_args(sys.argv[2:])
    if args.forget:
        print("  Session deleted." if forget(args.project) else "  No saved session.")
        return
    if not args.url or not args.authorized:
        print("  Give --url and --authorized (you own or may test this app).")
        sys.exit(1)
    print("  A browser window opens: log in (SSO, 2FA …), then CLOSE the window.")
    ok, msg = asyncio.run(record_session(args.url, args.project, insecure=args.insecure))
    print(("  ✓ " if ok else "  ✗ ") + msg)
    sys.exit(0 if ok else 1)


def run_mcp_command() -> None:
    """
    Handle the 'mcp' subcommand — serve QAX as an MCP server on stdio, so coding agents
    (Claude Code, Cursor …) can read the app, write and run tests, scan and preview bugs.
    Same gates as the web UI; never writes to Jira/Xray.
    """
    from core.mcp_server import serve
    serve()


def run_doctor_command() -> None:
    """
    Handle the 'doctor' subcommand — check this machine is ready to run QAX (Python,
    packages, browser, git, output folder, configured integrations; never prints secrets).
    Exits 1 when a required piece is missing.
    """
    from core.doctor import print_report, run_checks
    sys.exit(print_report(run_checks()))
