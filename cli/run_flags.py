"""
cli/run_flags.py — Browser-run flags shared by execute and pipeline (seeing, browser, device, workers).
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


def _add_seeing_flags(parser) -> None:
    """--watch / --trace / --screenshots for the browser engine (execute, pipeline)."""
    parser.add_argument("--watch", action="store_true", default=False,
                        help="Open a visible, slowed-down browser so you can watch every step "
                             "(also takes a screenshot after every step).")
    parser.add_argument("--trace", action="store_true", default=False,
                        help="Record a Playwright trace per case (actions, network, DOM): "
                             "open with `playwright show-trace <file>`.")
    parser.add_argument("--screenshots", choices=["failures", "all"], default="failures",
                        help="Screenshot after every step (all) or only at a failure (default).")
    parser.add_argument("--visual", choices=["off", "on", "update"], default="off",
                        help="Compare the screen each case ends on with its baseline (a lead, not a verdict); "
                             "'update' saves new baselines.")
    parser.add_argument("--no-cleanup", action="store_true", default=False,
                        help="Do not run the cases' cleanup_steps (they undo what a case created).")


def _add_browser_flags(parser) -> None:
    """--browser / --device / --workers for the browser engine (execute, pipeline)."""
    parser.add_argument("--browser", choices=["chromium", "firefox", "webkit"], default="",
                        help="Browser engine (default chromium; firefox/webkit need "
                             "`python -m playwright install firefox webkit`).")
    parser.add_argument("--device", choices=["desktop", "mobile", "mobile-ios", "tablet"], default="",
                        help="Screen/device to emulate (default desktop 1440×900).")
    parser.add_argument("--workers", type=int, default=0,
                        help="Run cases on this many browsers at once (default 1).")


def _apply_browser_flags(args) -> None:
    """Turn the browser flags into the settings the runner reads."""
    if getattr(args, "browser", ""):
        os.environ["QAX_BROWSER"] = args.browser
    if getattr(args, "device", ""):
        os.environ["QAX_DEVICE"] = args.device
    if getattr(args, "workers", 0):
        os.environ["QAX_WORKERS"] = str(max(1, args.workers))


def _apply_seeing_flags(args) -> None:
    """Turn the seeing flags into the settings the browser runner reads."""
    if getattr(args, "watch", False):
        os.environ["QAX_SLOW_MO_MS"] = os.getenv("QAX_SLOW_MO_MS") or "400"
        os.environ["QAX_STEP_SCREENSHOTS"] = "true"
    if getattr(args, "screenshots", "failures") == "all":
        os.environ["QAX_STEP_SCREENSHOTS"] = "true"
    if getattr(args, "trace", False):
        os.environ["QAX_TRACE"] = "true"
    if getattr(args, "visual", "off") != "off":
        os.environ["QAX_VISUAL"] = "update" if args.visual == "update" else "true"
    if getattr(args, "no_cleanup", False):
        os.environ["QAX_CLEANUP"] = "false"


def _print_writes_sent(batch: dict) -> None:
    """Say what a browser run sent that can change data on the target (QAX does not undo it)."""
    writes = [w for r in (batch.get("results") or []) for w in (r.get("writes") or [])]
    if not writes:
        return
    seen: dict = {}
    for w in writes:
        k = f"{w.get('method')} {w.get('path')}"
        seen[k] = seen.get(k, 0) + 1
    print(f"  Writes sent to the target: {len(writes)} — QAX does not undo data created "
          "through the UI; remove test data yourself if needed:")
    for k, n in list(seen.items())[:10]:
        print(f"    {n}× {k}")
