"""
cli/execute_cmd.py — `qax execute`: run test cases in a real browser (CI-ready: --yes, --junit).
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
from cli.run_flags import _add_browser_flags, _add_seeing_flags, _apply_browser_flags, _apply_seeing_flags, _print_writes_sent


def run_execute_command() -> None:
    """
    Handle the 'execute' subcommand.

    Fetches one or more test cases (by ID, suite tag, or output JSON file),
    shows the list to the user, asks for confirmation, then runs them through
    the Playwright browser execution engine.

    Flow:
      1. Parse CLI args
      2. Collect test cases (--test-case / --suite / --file)
      3. Print list and ask confirmation
      4. PlaywrightRunner.setup()
      5. TestExecutor.execute_batch()
      6. ResultsReporter.generate_console_summary()
      7. ResultsReporter.generate_html_report()
      8. Optional: ResultsReporter.report_to_xray() (if --upload)
      9. Save session
      10. PlaywrightRunner.teardown()

    Called when sys.argv[1] == "execute".
    """
    import asyncio

    parser = argparse.ArgumentParser(
        prog="qax execute",
        description="Run test cases in a Playwright browser.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── Source: what to execute ────────────────────────────────────────────
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--test-case",
        metavar="ID",
        help="Execute a single test case by Jira/Xray ID (e.g. PROJ-501).",
    )
    source_group.add_argument(
        "--suite",
        choices=["smoke", "regression"],
        metavar="NAME",
        help="Execute all tests tagged 'smoke' or 'regression' in Xray.",
    )
    source_group.add_argument(
        "--file",
        metavar="PATH",
        help="Execute test cases from a local JSON output file.",
    )

    # ── Execution options ──────────────────────────────────────────────────
    parser.add_argument(
        "--headless",
        default="true",
        choices=["true", "false"],
        help="Run browser in headless mode. Default: true. "
             "Set 'false' to watch the browser run (useful for debugging).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Override DRY_RUN for this run (never uploads to Xray). "
             "Default: reads DRY_RUN from .env.",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        default=False,
        help="Upload execution results to Xray after completion. "
             "Ignored when DRY_RUN=true.",
    )
    parser.add_argument(
        "--stop-on-fail",
        action="store_true",
        default=False,
        help="Stop execution after the first FAIL result.",
    )
    parser.add_argument(
        "--ai-heal",
        action="store_true",
        default=False,
        help="Let QAX send the page HTML / a screenshot to the AI provider to repair a "
             "failing selector (off by default: nothing leaves the machine).",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=False,
        help="Do not verify the target's TLS certificate (self-signed staging only).",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help="Path for the HTML report. Default: output/reports/execution_report_<ts>.html",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Project name for selectors (e.g. example, example2, example3). "
             "Default: read from DEFAULT_PROJECT in .env."
    )
    parser.add_argument("--base-url", default="", help="The app under test (sets APP_BASE_URL).")
    parser.add_argument("--yes", action="store_true",
                        help="Do not ask before running (CI). Also implied when CI=true.")
    parser.add_argument("--junit", default="", metavar="PATH",
                        help="Also write the results as JUnit XML (CI test dashboards).")
    _add_seeing_flags(parser)
    _add_browser_flags(parser)

    args = parser.parse_args(sys.argv[2:])
    _apply_seeing_flags(args)
    _apply_browser_flags(args)
    if args.base_url:
        os.environ["APP_BASE_URL"] = args.base_url.rstrip("/")
    if args.watch:
        args.headless = "false"
    if args.ai_heal:
        os.environ["QAX_AI_HEAL"] = "true"
    if args.insecure:
        os.environ["QAX_INSECURE"] = "true"

    # ── Set project env so ActionMapper can resolve the correct selectors ──
    if args.project:
        os.environ["DEFAULT_PROJECT"] = args.project

    # ── Enforce DRY_RUN override ───────────────────────────────────────────
    if args.dry_run:
        os.environ["DRY_RUN"] = "true"
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

    # ── Import execution layer ─────────────────────────────────────────────
    try:
        from execution.playwright_runner import PlaywrightRunner, PLAYWRIGHT_AVAILABLE
        from execution.test_executor import TestExecutor
        from execution.results_reporter import ResultsReporter
    except ImportError as e:
        logger.error(
            "Execution layer not available: %s\n"
            "Install with: pip install playwright==1.44.0 && playwright install chromium",
            e
        )
        sys.exit(1)

    if not PLAYWRIGHT_AVAILABLE:
        print("\n  ✗ Playwright is not installed.")
        print("    Install with:")
        print("      pip install playwright==1.44.0")
        print("      playwright install chromium")
        print("    Requires Python 3.11 or 3.12.\n")
        sys.exit(1)

    # ── Collect test cases ─────────────────────────────────────────────────
    test_cases = _collect_test_cases_for_execute(args)
    if not test_cases:
        print("\n  No test cases found. Check your --test-case / --suite / --file args.\n")
        sys.exit(0)

    # ── Show list and confirm ──────────────────────────────────────────────
    print(f"\n  Found {len(test_cases)} test case(s) to execute:")
    for i, tc in enumerate(test_cases, 1):
        tc_id = tc.get("test_case_id", tc.get("id", "?"))
        title = tc.get("title", "Untitled")[:55]
        print(f"    {i:>3}. [{tc_id}] {title}")

    print()
    if dry_run:
        print("  DRY_RUN=true — no results will be uploaded to Xray.")
    if args.yes or os.getenv("CI", "").lower() == "true":
        confirm = "y"                                   # CI: no one to answer the prompt
    else:
        try:
            confirm = input("  Proceed with execution? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            sys.exit(0)

    if confirm != "y":
        print("  Execution cancelled.\n")
        sys.exit(0)

    # ── Run the async execution pipeline ──────────────────────────────────
    headless = args.headless.lower() != "false"

    async def _run_pipeline() -> dict:
        """Async execution pipeline: setup → execute → report → teardown."""
        workers = int(os.getenv("QAX_WORKERS", "1") or 1)
        if workers > 1 and len(test_cases) > 1:
            from execution.test_executor import run_parallel
            return (await run_parallel(test_cases, workers, headless=headless, stop_on_fail=args.stop_on_fail,
                                       dry_run=dry_run or not args.upload)), ResultsReporter()
        runner   = PlaywrightRunner()
        # Auto-filed bugs on FAIL are live only with --upload (the operator's explicit OK).
        executor = TestExecutor(dry_run=dry_run or not args.upload)
        reporter = ResultsReporter()

        await runner.setup(headless=headless)

        try:
            batch_results = await executor.execute_batch(
                test_cases=test_cases,
                runner=runner,
                stop_on_fail=args.stop_on_fail,
            )
        finally:
            await runner.teardown()

        return batch_results, reporter

    print(f"\n  Starting Playwright ({('headless' if headless else 'headed')})...")
    start_ts = time.time()

    try:
        batch_results, reporter = asyncio.run(_run_pipeline())
    except Exception as e:
        logger.error("Execution pipeline failed: %s", e)
        print(f"\n  ✗ Execution failed: {e}\n")
        sys.exit(1)

    # ── Console summary ────────────────────────────────────────────────────
    reporter.generate_console_summary(batch_results)
    _print_writes_sent(batch_results)
    traces = [r.get("trace") for r in (batch_results.get("results") or []) if r.get("trace")]
    if traces:
        print(f"  Traces ({len(traces)}): open one with  playwright show-trace {traces[0]}")
    if batch_results.get("throttled"):
        print(f"  ⚠️  {batch_results['throttled']} case(s) hit the app's rate limit — reported as ERROR, "
              "not FAIL. Wait and re-run, or raise the limit on staging.")

    # ── JUnit XML (CI) ─────────────────────────────────────────────────────
    if args.junit:
        try:
            from core.junit import to_junit
            Path(args.junit).parent.mkdir(parents=True, exist_ok=True)
            Path(args.junit).write_text(to_junit(batch_results), encoding="utf-8")
            print(f"  JUnit XML   : {args.junit}")
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ Could not write JUnit XML: {e}")

    # ── HTML report ────────────────────────────────────────────────────────
    report_folder = str(Path(args.output).parent) if args.output else "output/reports"
    html_path = reporter.generate_html_report(batch_results, output_folder=report_folder)
    if html_path:
        print(f"  HTML report : {html_path}")

    # ── Xray upload ────────────────────────────────────────────────────────
    if args.upload:
        if dry_run:
            print("  DRY_RUN=true — skipping Xray upload (would upload results).")
        else:
            print("  Uploading results to Xray...")
            try:
                xray = XrayClient(dry_run=False)
                key_map = {tc.get("test_case_id", ""): tc.get("jira_key")
                           for tc in test_cases if tc.get("jira_key")}
                upload_summary = asyncio.run(
                    reporter.report_to_xray(batch_results, xray, key_map=key_map)
                )
                print(
                    f"  Xray upload : {upload_summary['uploaded']} uploaded"
                    f" (Test Execution {upload_summary.get('test_execution_key') or '-'}), "
                    f"{upload_summary['failed']} failed, "
                    f"{upload_summary.get('unmapped', 0)} without a Jira key"
                )
                if upload_summary.get("error"):
                    print(f"  ✗ {upload_summary['error']}")
            except Exception as e:
                logger.error("Xray upload failed: %s", e)
                print(f"  ✗ Xray upload failed: {e}")

    # ── Save session ───────────────────────────────────────────────────────
    try:
        sm = SessionManager()
        sm.save_session(
            mode="execute",
            config={
                "suite": args.suite,
                "test_case": args.test_case,
                "headless": headless,
                "upload": args.upload,
            },
            results={
                "total": batch_results.get("total", 0),
                "passed": batch_results.get("passed", 0),
                "failed": batch_results.get("failed", 0),
                "pass_rate": batch_results.get("pass_rate", 0.0),
                "html_report": html_path,
                "duration_seconds": round(time.time() - start_ts, 1),
            },
        )
    except Exception as e:
        logger.debug("Could not save session: %s", e)

    # ── Exit code ──────────────────────────────────────────────────────────
    if batch_results.get("failed", 0) > 0 or batch_results.get("errors", 0) > 0:
        sys.exit(1)


def _collect_test_cases_for_execute(args) -> list[dict]:
    """
    Resolve the test cases to execute from --test-case, --suite, or --file.

    For --test-case / --suite: fetches from Xray via XrayClient.
    For --file: loads from a local JSON output file.

    Falls back to an empty list on any error.

    Args:
        args: Parsed argparse namespace with attributes:
                test_case (str | None), suite (str | None), file (str | None).

    Returns:
        list[dict]: Test case dicts ready for TestExecutor.execute_batch().
    """
    # ── Load from local file ───────────────────────────────────────────────
    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            logger.error("File not found: %s", args.file)
            return []
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "test_cases" in data:
                return data["test_cases"]
            logger.error("Unexpected JSON structure in %s", args.file)
            return []
        except (json.JSONDecodeError, IOError) as e:
            logger.error("Cannot read %s: %s", args.file, e)
            return []

    # ── Fetch from Xray ────────────────────────────────────────────────────
    try:
        # Reads only: fetching tests and their steps never writes, so it is not
        # gated by DRY_RUN (the upload of results is, separately).
        xray = XrayClient(dry_run=False)

        if args.test_case:
            tc = xray.fetch_test_by_id(args.test_case)
            issues = [tc] if tc else []
        elif args.suite:
            issues = xray.fetch_tests_by_tag(args.suite)
        else:
            return []
        return _issues_to_executable_cases(issues, xray.fetch_test_steps(
            [i.get("key", "") for i in issues]))

    except Exception as e:
        logger.error("Could not fetch test cases from Xray: %s", e)
        return []


def _issues_to_executable_cases(issues: list[dict], steps_by_key: dict) -> list[dict]:
    """Jira Test issues + Xray steps → executable cases (see core.xray_client.issues_to_cases)."""
    from core.xray_client import issues_to_cases
    return issues_to_cases(issues, steps_by_key)
