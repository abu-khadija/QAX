"""
main.py — QAX — Command Line Interface
==================================================
Multi-project QA automation
Single entry point for all agent commands.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ANALYZE — Generate test cases from source code
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py analyze --file PATH
  python main.py analyze --file PATH --module Orders --dry-run
  python main.py analyze --folder PATH --module Orders --ext .cs
  python main.py analyze --file PATH --include-deps --include-git
  python main.py analyze --file PATH --include-bugs --module Orders
  python main.py analyze --file PATH --screenshot --dry-run

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RUN — Config-driven pipeline with session tracking
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py run --config configs/smoke.json
  python main.py run --mode smoke
  python main.py run --mode regression
  python main.py run --mode generate --file PATH
  python main.py run --mode module --module Orders --include-bugs
  python main.py run --mode bugs --bugs EXAMPLE-101,EXAMPLE-205
  python main.py run --mode tickets --tickets EXAMPLE-500,EXAMPLE-501
  python main.py run --mode release --version 12.28.5
  python main.py run --config configs/smoke.json --dry-run
  python main.py run --sessions

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXECUTE — Run test cases with Playwright browser
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py execute --test-case EXAMPLE-501
  python main.py execute --suite smoke
  python main.py execute --suite regression
  python main.py execute --file output/test_cases_20250101.json
  python main.py execute --suite smoke --headless false
  python main.py execute --suite smoke --upload
  python main.py execute --suite smoke --dry-run

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FEEDBACK — Review and improve generated test cases
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py feedback --accept TC-TRK-0001
  python main.py feedback --reject TC-TRK-0001
  python main.py feedback --stats
  python main.py feedback --optimize

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DASHBOARD — Real-time web dashboard
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py dashboard
  python main.py dashboard --port 8080

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DISCOVER — Auto-discover CSS selectors from live app
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py discover --url URL --page PAGE_NAME
  python main.py discover --url URL --page PAGE_NAME --headless
  python main.py discover --sitemap --urls-file pages.txt --project example

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK-CONNECTION — Test all API connections
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py check-connection

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UI — Launch Streamlit web interface
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py ui

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE — Generate → Export → Execute in one step
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py pipeline --file src/Controller.cs --export xray --execute --dry-run
  python main.py pipeline --file src/Controller.cs --export excel
  python main.py pipeline --file src/Controller.cs --export none --execute
  python main.py pipeline --file src/Controller.cs --export zephyr --dry-run
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# ── Force UTF-8 output on Windows so Unicode symbols (✓ ✗) print correctly ──
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ── Load environment variables before importing project modules ───────────────
load_dotenv(override=True)

# ── Project module imports ─────────────────────────────────────────────────────
from core.bug_fetcher import BugFetcher
from core.claude_client import ClaudeClient
from core.code_reader import chunk_large_file, extract_dependencies, is_testable, read_file
from core.feedback_store import FeedbackStore
from core.git_analyzer import GitAnalyzer
from core.prompt_optimizer import optimize_prompt
from core.session_manager import SessionManager
from core.validator import fix_common_issues, generate_quality_report, validate_batch
from core.xray_client import XrayClient

# ── Logging configuration ──────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("qax")


# ══════════════════════════════════════════════════════════════════════════════
# ANALYZE COMMAND
# ══════════════════════════════════════════════════════════════════════════════

def run_analyze_command() -> None:
    """
    Handle the 'analyze' subcommand.

    Reads one file or a folder of files, sends them to Claude for test
    case generation, validates the output, shows a quality report, and
    optionally pushes to Jira/Xray.

    Called when sys.argv[1] == "analyze".
    """
    parser = argparse.ArgumentParser(
        prog="qax analyze",
        description="Generate test cases from source code files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py analyze --file src/Orders/ChecklistController.cs
  python main.py analyze --file IncidentForm.cs --module Orders --dry-run
  python main.py analyze --folder src/Orders/ --ext .cs --module Orders
  python main.py analyze --file IncidentForm.cs --include-deps --include-git
  python main.py analyze --file IncidentForm.cs --include-bugs --module Orders
        """
    )

    # ── Source (mutually exclusive) ────────────────────────────────────────
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--file", "-f",
        type=str,
        help="Path to a single source file to analyze."
    )
    source_group.add_argument(
        "--folder",
        type=str,
        help="Path to a directory. All matching files will be analyzed."
    )

    # ── Optional arguments ─────────────────────────────────────────────────
    parser.add_argument(
        "--module", "-m",
        type=str,
        default=None,
        help="Module hint (e.g., 'Orders'). Auto-detected from path if not set."
    )
    parser.add_argument(
        "--ext",
        type=str,
        default=None,
        help="File extension filter when using --folder. "
             "Auto-detected (the folder's most common source language) if not set."
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output path for test cases JSON. Default: output/test_cases_TIMESTAMP.json"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=300,
        help="Max lines per chunk when splitting large files. Default: 300"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Generate and validate but do NOT push to Jira/Xray."
    )
    parser.add_argument(
        "--skip-push",
        action="store_true",
        default=False,
        help="Skip Xray push entirely (still saves JSON locally)."
    )
    parser.add_argument(
        "--skip-low-confidence",
        type=int,
        default=0,
        metavar="THRESHOLD",
        help="Skip pushing test cases below this confidence score. Example: 5"
    )
    parser.add_argument(
        "--include-deps",
        action="store_true",
        default=False,
        help="Resolve and include local dependency files (imports) for richer context."
    )
    parser.add_argument(
        "--include-git",
        action="store_true",
        default=False,
        help="Analyze git commit history for risk level. High-risk files get extra tests."
    )
    parser.add_argument(
        "--include-bugs",
        action="store_true",
        default=False,
        help="Fetch resolved Jira bugs for this module and inject patterns into the prompt. "
             "Requires --module and Jira credentials in .env."
    )
    parser.add_argument(
        "--screenshot",
        action="store_true",
        default=False,
        help="Capture app UI screenshot for Claude visual context. "
             "Requires ENABLE_SCREENSHOT=true in .env."
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Project name for loading context/selectors (e.g. example, example2, example3). "
             "Default: read from DEFAULT_PROJECT in .env."
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        help="Disable file-level cache. Always calls the API even if the file is unchanged."
    )
    parser.add_argument(
        "--auto-accept",
        action="store_true",
        default=False,
        help="Automatically accept test cases with confidence >= 8.5 and needs_review=False."
    )
    parser.add_argument(
        "--natural",
        action="store_true",
        default=False,
        help="Print a plain-language Markdown summary after the technical report."
    )
    parser.add_argument(
        "--story",
        type=str,
        default=None,
        metavar="TEXT",
        help="User story or acceptance criteria. QAX uses it as extra context for test generation."
    )
    parser.add_argument(
        "--test-type",
        type=str,
        default="auto",
        choices=["regression", "feature", "smoke", "uat", "api", "e2e", "auto"],
        help="Test type to generate. 'auto' detects automatically from git/story context. "
             "Default: auto"
    )
    parser.add_argument(
        "--gap-check",
        action="store_true",
        default=False,
        help="Before pushing, check Xray for existing tests and skip duplicates. "
             "Requires --module and Jira credentials in .env."
    )

    args = parser.parse_args(sys.argv[2:])

    if args.dry_run:
        os.environ["DRY_RUN"] = "true"

    project = args.project or os.getenv("DEFAULT_PROJECT", "")

    # ── Collect files ──────────────────────────────────────────────────────
    file_paths = _collect_files_from_args(
        file=args.file,
        folder=args.folder,
        ext=args.ext,
    )
    logger.info("Files to analyze: %d", len(file_paths))

    # ── Initialize Claude ──────────────────────────────────────────────────
    try:
        claude = ClaudeClient(project=project)
    except (ValueError, FileNotFoundError) as e:
        logger.error("Failed to initialize Claude client: %s", e)
        sys.exit(1)

    # ── Initialize Xray (if pushing) ──────────────────────────────────────
    xray = None
    if not args.skip_push and not args.dry_run:
        try:
            xray = XrayClient()
        except ValueError as e:
            logger.warning("Xray not configured (%s) — results will be saved locally only", e)

    # ── Initialize optional clients ────────────────────────────────────────
    git_analyzer = GitAnalyzer() if args.include_git else None
    bug_fetcher = BugFetcher() if args.include_bugs else None

    # ── Initialize feedback store for auto-accept gating ──────────────────
    fb_store = None
    if args.auto_accept:
        try:
            fb_store = FeedbackStore()
        except Exception:
            pass

    # ── Process each file ──────────────────────────────────────────────────
    run_start = time.time()
    all_raw_cases = []
    for file_path in file_paths:
        raw = _process_single_file(
            file_path=file_path,
            claude=claude,
            module_hint=args.module,
            chunk_size=args.chunk_size,
            include_deps=args.include_deps,
            git_analyzer=git_analyzer,
            bug_fetcher=bug_fetcher,
            screenshot=args.screenshot,
            use_cache=not args.no_cache,
            user_story=args.story,
            test_type_override=args.test_type if args.test_type != "auto" else None,
            project=project,
        )
        all_raw_cases.extend(raw)

    if not all_raw_cases:
        logger.warning("No test cases generated. Check that your source files contain testable logic.")
        sys.exit(0)

    logger.info("Total raw test cases: %d", len(all_raw_cases))

    # ── Fix, validate, report ──────────────────────────────────────────────
    fixed = [fix_common_issues(tc) for tc in all_raw_cases]
    validation_result = validate_batch(fixed,
                                       auto_accept=args.auto_accept,
                                       feedback_store=fb_store)
    generate_quality_report(validation_result)

    valid_cases = validation_result["valid"]

    # ── Save output ────────────────────────────────────────────────────────
    saved_path = ""
    if valid_cases:
        # ── Markdown output: --output pr_comment.md ────────────────────────
        if args.output and args.output.lower().endswith(".md"):
            saved_path = _save_results_as_markdown(
                valid_cases, file_paths, args.output
            )
        else:
            saved_path = _save_results(valid_cases, args.output)

    # ── B1: Gap check — filter out duplicates before pushing ──────────────
    cases_to_push = valid_cases
    if args.gap_check and args.module and valid_cases:
        try:
            from core.jira_gap_analyzer import JiraGapAnalyzer
            gap_analyzer = JiraGapAnalyzer()
            if gap_analyzer._configured:
                logger.info("Running gap analysis for module '%s'...", args.module)
                existing_tests = gap_analyzer.fetch_existing_tests(args.module)
                gap_result = gap_analyzer.analyze_gaps(valid_cases, existing_tests)
                print("\n  Gap Analysis:")
                print(f"    Already in Xray : {gap_result['duplicate_count']} test(s)")
                print(f"    New tests needed: {gap_result['gap_count']} test(s)")
                print(f"    {gap_result['recommendation']}")
                if gap_result["duplicates"]:
                    print("    Skipping duplicates from push.")
                cases_to_push = gap_result["new"]
            else:
                logger.warning(
                    "--gap-check requires Jira credentials. "
                    "Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN in .env."
                )
        except Exception as _ge:
            logger.warning("Gap check failed (non-fatal): %s", _ge)
    elif args.gap_check and not args.module:
        logger.warning("--gap-check requires --module. Skipping gap analysis.")

    # ── Apply confidence threshold filter ─────────────────────────────────
    if args.skip_low_confidence > 0:
        before = len(cases_to_push)
        cases_to_push = [
            tc for tc in cases_to_push
            if tc.get("confidence_score", 0) >= args.skip_low_confidence
        ]
        skipped = before - len(cases_to_push)
        if skipped > 0:
            logger.info("Skipped %d cases below confidence threshold %d", skipped, args.skip_low_confidence)

    # ── Push to Xray ───────────────────────────────────────────────────────
    if xray and cases_to_push:
        push_result = xray.push_batch(cases_to_push)
        _print_push_summary(push_result)
    elif not xray and not args.dry_run and not args.skip_push:
        logger.info("Xray not available — test cases saved locally only: %s", saved_path)

    # ── Natural language summary (A5) ─────────────────────────────────────
    if args.natural and valid_cases:
        try:
            from core.response_formatter import to_natural_language
            run_duration = time.time() - run_start
            source_name = os.path.basename(args.file or args.folder or "")
            print(to_natural_language(valid_cases, source_name, run_duration))
        except Exception as _nle:
            logger.debug("Natural language formatting failed (non-fatal): %s", _nle)

    logger.info("Done.")


# ══════════════════════════════════════════════════════════════════════════════
# RUN COMMAND
# ══════════════════════════════════════════════════════════════════════════════

def run_run_command() -> None:
    """
    Handle the 'run' subcommand.

    Config-driven pipeline that loads a JSON config file, merges CLI flags,
    resolves the run mode, and delegates to the appropriate SessionManager
    run method. Saves a session record to output/sessions/ after every run.

    Called when sys.argv[1] == "run".
    """
    parser = argparse.ArgumentParser(
        prog="qax run",
        description="Config-driven run pipeline with session tracking.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py run --config configs/smoke.json
  python main.py run --mode smoke
  python main.py run --mode generate --file path/to/Controller.cs
  python main.py run --mode module --module Orders --include-bugs
  python main.py run --mode bugs --bugs EXAMPLE-101,EXAMPLE-205
  python main.py run --mode tickets --tickets EXAMPLE-500,EXAMPLE-501
  python main.py run --mode release --version 12.28.5
  python main.py run --config configs/smoke.json --dry-run
  python main.py run --sessions
        """
    )

    # ── Session listing ────────────────────────────────────────────────────
    parser.add_argument(
        "--sessions",
        action="store_true",
        default=False,
        help="List the 10 most recent run sessions and exit."
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Resume from the last session matching the current mode and project."
    )

    # ── Config and mode ────────────────────────────────────────────────────
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to a JSON config file (e.g., configs/smoke.json)."
    )
    parser.add_argument(
        "--mode",
        type=str,
        default=None,
        choices=["generate", "smoke", "regression", "bugs", "tickets", "module", "release"],
        help="Run mode. Overrides the mode set in the config file."
    )

    # ── Source inputs ──────────────────────────────────────────────────────
    parser.add_argument("--file", "-f", type=str, default=None,
                        help="Source file path (generate mode).")
    parser.add_argument("--folder", type=str, default=None,
                        help="Source folder path (generate mode).")
    parser.add_argument("--module", "-m", type=str, default=None,
                        help="Module name (e.g., 'Orders').")
    parser.add_argument("--bugs", type=str, default=None, metavar="IDS",
                        help="Comma-separated bug IDs for bugs mode. E.g., EXAMPLE-101,EXAMPLE-205")
    parser.add_argument("--tickets", type=str, default=None, metavar="IDS",
                        help="Comma-separated ticket IDs for tickets mode.")
    parser.add_argument("--version", type=str, default=None,
                        help="Release version for release mode. E.g., 12.28.5")
    parser.add_argument("--ext", type=str, default=None,
                        help="File extension filter for folder analysis. Default: auto-detected (the folder's most common source language).")
    parser.add_argument("--chunk-size", type=int, default=None,
                        help="Max lines per chunk. Default: 300")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output path for test cases JSON.")

    # ── Behavior flags ─────────────────────────────────────────────────────
    parser.add_argument("--dry-run", action="store_true", default=None,
                        help="Generate but do NOT push to Jira/Xray.")
    parser.add_argument("--upload", action="store_true", default=False,
                        help="Upload results to Xray after generation (overrides dry-run).")
    parser.add_argument("--headless", action="store_true", default=False,
                        help="Run Playwright in headless mode (for screenshot capture).")
    parser.add_argument("--include-deps", action="store_true", default=False,
                        help="Include local dependency files in analysis.")
    parser.add_argument("--include-git", action="store_true", default=False,
                        help="Include git history risk analysis.")
    parser.add_argument("--include-bugs", action="store_true", default=False,
                        help="Fetch Jira bug patterns for the module.")
    parser.add_argument("--screenshot", action="store_true", default=False,
                        help="Capture app UI screenshot for Claude visual context.")
    parser.add_argument("--skip-low-confidence", type=int, default=None, metavar="N",
                        help="Skip pushing test cases below this confidence score.")
    parser.add_argument("--project", type=str, default=None,
                        help="Project name (e.g. example, example2, example3). Default: DEFAULT_PROJECT env.")

    args = parser.parse_args(sys.argv[2:])

    # ── --sessions: list recent sessions and exit ──────────────────────────
    if args.sessions:
        sm = SessionManager()
        _print_sessions(sm.list_sessions(limit=10))
        return

    # ── --resume: show last session info and continue ──────────────────────
    if args.resume:
        sm = SessionManager()
        last_session = sm.get_last_session(
            mode=args.mode or None,
            project=args.project or None,
        )
        if last_session:
            resume_info = sm.resume_from_session(last_session)
            print("\n  Resuming from last session:")
            print(f"  {resume_info['suggested_next']}")
            if resume_info.get("last_module"):
                print(f"  Last module : {resume_info['last_module']}")
            print()
        else:
            mode_hint = args.mode or "any"
            project_hint = args.project or "any"
            print(
                f"\n  No previous session found for mode={mode_hint}, "
                f"project={project_hint}.\n"
                "  Starting fresh.\n"
            )

    # ── Build CLI flags dict (None = not set → won't override config) ──────
    cli_flags = {
        "mode":                 args.mode,
        "file":                 args.file,
        "folder":               args.folder,
        "module":               args.module,
        "bugs":                 args.bugs,
        "tickets":              args.tickets,
        "version":              args.version,
        "ext":                  args.ext,
        "chunk_size":           args.chunk_size,
        "output_file":          args.output,
        "dry_run":              True if args.dry_run else (False if args.upload else None),
        "include_deps":         args.include_deps or None,
        "include_git":          args.include_git or None,
        "include_bugs":         args.include_bugs or None,
        "screenshot":           args.screenshot or None,
        "headless":             args.headless or None,
        "skip_low_confidence":  args.skip_low_confidence,
    }

    # ── Load config, merge flags, resolve mode ─────────────────────────────
    sm = SessionManager()

    config_path = args.config
    if not config_path and args.mode:
        config_path = sm.get_default_config_path(args.mode)

    config = sm.load_config(config_path) if config_path else {}
    config = sm.merge_with_flags(config, cli_flags)
    mode = sm.resolve_mode(config)
    config["mode"] = mode

    # Apply dry_run env override
    if config.get("dry_run"):
        os.environ["DRY_RUN"] = "true"

    logger.info("=" * 60)
    logger.info("  QAX — run --mode %s", mode.upper())
    logger.info("=" * 60)

    # ── Dispatch to the correct run method ────────────────────────────────
    if mode == "generate":
        # generate mode: use session_manager.run_generate()
        # If --file not in config but --folder is, expand to individual files
        if not config.get("file") and config.get("folder"):
            ext = config.get("ext", ".py")
            ext = ext if ext.startswith(".") else f".{ext}"
            folder = Path(config["folder"])
            if not folder.is_dir():
                logger.error("Folder not found: %s", config["folder"])
                sys.exit(1)
            files = sorted(folder.rglob(f"*{ext}"))
            if not files:
                logger.warning("No %s files found in %s", ext, config["folder"])
                sys.exit(0)
            # Process each file sequentially via run_generate
            all_results = []
            for f in files:
                file_config = dict(config)
                file_config["file"] = str(f)
                result = sm.run_generate(file_config)
                all_results.append(result)
            total_valid = sum(r.get("total_valid", 0) for r in all_results)
            print(f"\n  ✓ Folder analyze complete — {len(files)} files, {total_valid} valid test cases total")
        else:
            results = sm.run_generate(config)
            _print_run_summary("generate", results)

    elif mode == "smoke":
        results = sm.run_smoke(config)
        _print_run_summary("smoke", results)

    elif mode == "regression":
        results = sm.run_regression(config)
        _print_run_summary("regression", results)

    elif mode == "bugs":
        results = sm.run_bugs(config)
        _print_run_summary("bugs", results)

    elif mode == "tickets":
        results = sm.run_tickets(config)
        _print_run_summary("tickets", results)

    elif mode == "module":
        # module mode: same as generate but for all files of a specific module
        module_name = config.get("module")
        if not module_name:
            logger.error("Mode 'module' requires --module <ModuleName>")
            sys.exit(1)
        source_files = config.get("source_files", [])
        module_files = [
            e for e in source_files
            if (e.get("module", "").lower() == module_name.lower() if isinstance(e, dict) else False)
        ]
        if not module_files:
            logger.warning(
                "No source files found for module '%s' in config. "
                "Add them under 'source_files' in your config file.", module_name
            )
            sys.exit(0)
        all_results = []
        for entry in module_files:
            file_config = dict(config)
            file_config["file"] = entry.get("file")
            result = sm.run_generate(file_config)
            all_results.append(result)
        total_valid = sum(r.get("total_valid", 0) for r in all_results)
        print(f"\n  ✓ Module '{module_name}' complete — {len(module_files)} files, {total_valid} valid test cases")

    elif mode == "release":
        results = sm.run_release(config)
        # Release prints its own summary

    else:
        logger.error("Unknown mode: %s", mode)
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# FEEDBACK COMMAND
# ══════════════════════════════════════════════════════════════════════════════

def run_feedback_command() -> None:
    """
    Handle the 'feedback' subcommand.

    Allows QA analysts to mark generated test cases as accepted or rejected,
    view acceptance statistics, and trigger AI-powered prompt optimization.

    Called when sys.argv[1] == "feedback".
    """
    parser = argparse.ArgumentParser(
        prog="qax feedback",
        description="Record QA decisions and optimize the system prompt.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py feedback --accept TC-TRK-0001
  python main.py feedback --reject TC-TRK-0001
  python main.py feedback --stats
  python main.py feedback --optimize
        """
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--accept", metavar="TC_ID",
                       help="Mark a test case as accepted (good quality).")
    group.add_argument("--reject", metavar="TC_ID",
                       help="Mark a test case as rejected (poor quality).")
    group.add_argument("--stats", action="store_true",
                       help="Show acceptance rate statistics by module.")
    group.add_argument("--optimize", action="store_true",
                       help="Use Claude to suggest system prompt improvements from feedback data.")

    args = parser.parse_args(sys.argv[2:])
    store = FeedbackStore()

    # ── --stats ────────────────────────────────────────────────────────────
    if args.stats:
        stats = store.get_stats()
        print("\n" + "=" * 55)
        print("  QAX — Feedback Statistics")
        print("=" * 55)
        print(f"  Total reviewed       : {stats['total_reviewed']}")
        print(f"  Accepted             : {stats['accepted']}")
        print(f"  Rejected             : {stats['rejected']}")
        print(f"  Modified             : {stats['modified']}")
        print(f"  Acceptance rate      : {stats['acceptance_rate']}%")
        print(f"  Avg confidence (acc) : {stats['avg_confidence_accepted']}/10")
        if stats.get("by_module"):
            print("-" * 55)
            print("  By Module:")
            for mod, data in stats["by_module"].items():
                print(f"    {mod:<25} {data['rate']}%  ({data['accepted']}/{data['count']})")
        print("=" * 55)
        return

    # ── --optimize ─────────────────────────────────────────────────────────
    if args.optimize:
        print("\n  Analyzing feedback patterns...")
        summary = store.get_patterns_summary()
        suggestions = optimize_prompt(summary)
        print("\n" + "=" * 55)
        print("  PROMPT OPTIMIZATION SUGGESTIONS")
        print("=" * 55)
        print(suggestions)
        print("=" * 55)

        # ── A4: ask to apply ───────────────────────────────────────────────
        try:
            answer = input("\n  Apply these suggestions to config/system_prompt.md? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer == "y":
            try:
                prompt_path = Path(__file__).parent / "config" / "system_prompt.md"
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                appendix = (
                    f"\n\n---\n## Auto-Optimization Suggestions ({timestamp})\n\n"
                    f"{suggestions}\n"
                )
                with open(prompt_path, "a", encoding="utf-8") as f:
                    f.write(appendix)
                store.mark_optimization_done()
                print(f"\n  ✓ Suggestions appended to {prompt_path}")
                print("  ✓ Optimization timestamp recorded.\n")
            except Exception as e:
                logger.error("Could not apply suggestions: %s", e)
        else:
            print("\n  → Suggestions not applied. Run --optimize again when ready.\n")
        return

    # ── --accept / --reject ────────────────────────────────────────────────
    tc_id = args.accept or args.reject
    action = "accepted" if args.accept else "rejected"

    found_tc = _find_test_case_in_output(tc_id)

    if not found_tc:
        logger.error(
            "Test case '%s' not found in any output JSON file.\n"
            "Run the agent first to generate test cases, then accept/reject by ID.", tc_id
        )
        sys.exit(1)

    store.record_decision(found_tc, action=action)
    print(f"\n  ✓ Recorded: {tc_id} → {action.upper()}")
    print(f"    Title   : {found_tc.get('title', 'N/A')}")
    print(f"    Module  : {found_tc.get('module', 'N/A')}")
    print(f"    Score   : {found_tc.get('confidence_score', '?')}/10\n")

    # ── B4: Offer to save accepted test case as a reusable skill ──────────
    if action == "accepted":
        try:
            answer_skill = input(
                "  Add this as a reusable pattern? (y/n): "
            ).strip().lower()
            if answer_skill == "y":
                pattern_desc = input(
                    "  Pattern description (e.g. 'permission check'): "
                ).strip()
                solution_note = input(
                    "  Solution note (e.g. 'always test 403 response'): "
                ).strip()
                if pattern_desc and solution_note:
                    from core.skill_library import SkillLibrary
                    module_name = found_tc.get("module", "General")
                    SkillLibrary().add_skill(module_name, pattern_desc, solution_note)
                    print(f"\n  ✓ Skill saved to config/skills.json (module: {module_name})\n")
                else:
                    print("  → Skipped (empty input).\n")
        except (EOFError, KeyboardInterrupt):
            print()  # newline after Ctrl+C

    # ── A4: prompt optimization tip ────────────────────────────────────────
    try:
        if store.should_trigger_optimization():
            print("\n  Tip: You have 50+ reviews.")
            print("  Run: python main.py feedback --optimize")
            print("  This will improve future generations.\n")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# EXECUTE COMMAND
# ══════════════════════════════════════════════════════════════════════════════

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

    args = parser.parse_args(sys.argv[2:])

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
        runner   = PlaywrightRunner()
        executor = TestExecutor()
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
                xray = XrayClient()
                upload_summary = asyncio.run(
                    reporter.report_to_xray(batch_results, xray)
                )
                print(
                    f"  Xray upload : {upload_summary['uploaded']} uploaded, "
                    f"{upload_summary['failed']} failed"
                )
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
        xray = XrayClient()

        if args.test_case:
            tc = xray.fetch_test_by_id(args.test_case)
            return [tc] if tc else []

        if args.suite:
            return xray.fetch_tests_by_tag(args.suite)

    except Exception as e:
        logger.error("Could not fetch test cases from Xray: %s", e)
        return []

    return []


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD COMMAND
# ══════════════════════════════════════════════════════════════════════════════

def run_dashboard_command() -> None:
    """
    Handle the 'dashboard' subcommand.

    Starts the Flask web dashboard on localhost, opens it in the user's
    browser automatically, and keeps it running until Ctrl+C.

    The dashboard is read-only — it reads from:
      - output/feedback.db   (FeedbackStore SQLite database)
      - output/sessions/     (session JSON files)
      - output/test_cases_*.json (generated test case files)

    No API calls to Jira, Xray, or Claude are made.
    DRY_RUN has no effect on the dashboard.

    Called when sys.argv[1] == "dashboard".
    """
    parser = argparse.ArgumentParser(
        prog="qax dashboard",
        description="Start the QAX web dashboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py dashboard
  python main.py dashboard --port 8080
        """
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Port to run the dashboard on. Default: 5000"
    )
    args = parser.parse_args(sys.argv[2:])

    try:
        from dashboard.app import DashboardApp, FLASK_AVAILABLE
    except ImportError:
        print("\n  ✗ Dashboard module not found.")
        print("    Make sure dashboard/ folder is present.")
        sys.exit(1)

    if not FLASK_AVAILABLE:
        print("\n  ✗ Flask is not installed.")
        print("    Install with: pip install flask")
        print("    Then re-run: python main.py dashboard\n")
        sys.exit(1)

    app = DashboardApp()
    app.start(port=args.port)


# ══════════════════════════════════════════════════════════════════════════════
# CHECK-CONNECTION COMMAND
# ══════════════════════════════════════════════════════════════════════════════

def run_check_connection_command() -> None:
    """
    Handle the 'check-connection' subcommand.

    Tests connectivity to all three external services:
      1. Claude API (Anthropic)
      2. Jira REST API
      3. Xray Cloud API

    Prints a status line for each service — shows the user exactly
    what's configured and what's reachable before running the agent.

    Called when sys.argv[1] == "check-connection".
    """
    import requests

    print("\n" + "=" * 55)
    print("  QAX — Connection Check")
    print("=" * 55)

    all_ok = True

    # ── 1. Claude API ──────────────────────────────────────────────────────
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  ✗ Claude API    — NOT CONFIGURED")
        print("    → Set ANTHROPIC_API_KEY in .env")
        print("    → Get key at: https://console.anthropic.com/account/keys")
        all_ok = False
    else:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            # Minimal call to verify credentials
            resp = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=5,
                messages=[{"role": "user", "content": "Hi"}],
            )
            print(f"  ✓ Claude API    — CONNECTED  (model: claude-sonnet-4-5)")
        except anthropic.AuthenticationError:
            print("  ✗ Claude API    — AUTHENTICATION FAILED")
            print("    → Check your ANTHROPIC_API_KEY in .env")
            all_ok = False
        except Exception as e:
            err = str(e)
            if "credit balance" in err.lower():
                print("  ✗ Claude API    — INSUFFICIENT CREDITS")
                print("    → Add credits at: https://console.anthropic.com/settings/billing")
            else:
                print(f"  ✗ Claude API    — FAILED: {err[:80]}")
            all_ok = False

    # ── 2. Jira REST API ───────────────────────────────────────────────────
    jira_url  = os.getenv("JIRA_BASE_URL", "")
    jira_mail = os.getenv("JIRA_EMAIL", "")
    jira_tok  = os.getenv("JIRA_API_TOKEN", "")

    if not jira_url or not jira_mail or not jira_tok:
        missing = [
            k for k, v in [("JIRA_BASE_URL", jira_url), ("JIRA_EMAIL", jira_mail),
                            ("JIRA_API_TOKEN", jira_tok)] if not v
        ]
        print(f"  ✗ Jira API      — NOT CONFIGURED (missing: {', '.join(missing)})")
        all_ok = False
    else:
        try:
            resp = requests.get(
                f"{jira_url.rstrip('/')}/rest/api/3/myself",
                auth=(jira_mail, jira_tok),
                timeout=10,
            )
            if resp.status_code == 200:
                user_data = resp.json()
                display_name = user_data.get("displayName", "Unknown User")
                account_id = user_data.get("accountId", "")[:8]
                print(f"  ✓ Jira API      — CONNECTED  (as {display_name}, id: {account_id}...)")
            elif resp.status_code == 401:
                print("  ✗ Jira API      — AUTHENTICATION FAILED (401)")
                print("    → Check JIRA_EMAIL and JIRA_API_TOKEN in .env")
                all_ok = False
            elif resp.status_code == 403:
                print("  ✗ Jira API      — ACCESS DENIED (403)")
                print("    → Your account may lack API access permissions")
                all_ok = False
            else:
                print(f"  ✗ Jira API      — HTTP {resp.status_code}")
                all_ok = False
        except requests.exceptions.ConnectionError:
            print(f"  ✗ Jira API      — CANNOT CONNECT to {jira_url}")
            print("    → Check JIRA_BASE_URL in .env (include https://)")
            all_ok = False
        except requests.exceptions.Timeout:
            print("  ✗ Jira API      — TIMEOUT (>10s)")
            all_ok = False
        except Exception as e:
            print(f"  ✗ Jira API      — ERROR: {str(e)[:80]}")
            all_ok = False

    # ── 3. Xray Cloud API ──────────────────────────────────────────────────
    xray_id     = os.getenv("XRAY_CLIENT_ID", "")
    xray_secret = os.getenv("XRAY_CLIENT_SECRET", "")

    if not xray_id or not xray_secret:
        missing = [k for k, v in [("XRAY_CLIENT_ID", xray_id), ("XRAY_CLIENT_SECRET", xray_secret)] if not v]
        print(f"  ✗ Xray API      — NOT CONFIGURED (missing: {', '.join(missing)})")
        print("    → Generate credentials: Jira → Apps → Xray → API Keys")
        all_ok = False
    else:
        try:
            resp = requests.post(
                "https://xray.cloud.getxray.app/api/v2/authenticate",
                json={"client_id": xray_id, "client_secret": xray_secret},
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code == 200:
                print("  ✓ Xray API      — CONNECTED  (authentication successful)")
            elif resp.status_code == 401:
                print("  ✗ Xray API      — AUTHENTICATION FAILED (401)")
                print("    → Check XRAY_CLIENT_ID and XRAY_CLIENT_SECRET in .env")
                all_ok = False
            else:
                print(f"  ✗ Xray API      — HTTP {resp.status_code}")
                all_ok = False
        except requests.exceptions.ConnectionError:
            print("  ✗ Xray API      — CANNOT CONNECT to xray.cloud.getxray.app")
            all_ok = False
        except requests.exceptions.Timeout:
            print("  ✗ Xray API      — TIMEOUT (>15s)")
            all_ok = False
        except Exception as e:
            print(f"  ✗ Xray API      — ERROR: {str(e)[:80]}")
            all_ok = False

    # ── Summary ────────────────────────────────────────────────────────────
    print("-" * 55)
    if all_ok:
        print("  ✓ All connections OK — agent is ready to run.")
    else:
        print("  ✗ One or more connections failed.")
        print("    → Fix the issues above, then re-run check-connection.")
    print("=" * 55 + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# CACHE COMMAND
# ══════════════════════════════════════════════════════════════════════════════

def run_cache_command() -> None:
    """
    Handle the 'cache' subcommand.

    Supports two operations:
      --clear  Delete all cached API results from output/cache/
      --stats  Show how many files are cached and total size

    Called when sys.argv[1] == "cache".
    """
    parser = argparse.ArgumentParser(
        prog="qax cache",
        description="Manage the QAX file-level result cache.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py cache --stats
  python main.py cache --clear
        """
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--clear",
        action="store_true",
        help="Delete all cached results from output/cache/."
    )
    group.add_argument(
        "--stats",
        action="store_true",
        help="Show cache file count and total size."
    )
    args = parser.parse_args(sys.argv[2:])

    from core.cache_manager import CacheManager
    cache = CacheManager()

    if args.clear:
        deleted = cache.clear()
        print(f"\n  ✓ Cleared {deleted} cached file(s) from {cache.cache_dir}\n")

    elif args.stats:
        info = cache.stats()
        print(f"\n  Cache: {info['count']} file(s), {info['size_mb']} MB")
        print(f"  Location: {cache.cache_dir}\n")


# ══════════════════════════════════════════════════════════════════════════════
# INIT-PROJECT COMMAND
# ══════════════════════════════════════════════════════════════════════════════

def run_init_project_command() -> None:
    """
    Handle the 'init-project' subcommand.

    Creates a new project folder under config/projects/ with a context.md
    template and a selectors.json scaffold. Prints next-step instructions.

    Called when sys.argv[1] == "init-project".
    """
    parser = argparse.ArgumentParser(
        prog="qax init-project",
        description="Initialize a new project config folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py init-project --name myapp
  python main.py init-project --name example2
        """
    )
    parser.add_argument(
        "--name",
        type=str,
        required=True,
        help="Project name (lowercase, no spaces). Creates config/projects/<name>/."
    )

    args = parser.parse_args(sys.argv[2:])
    project_name = args.name.strip().lower()

    if not project_name:
        logger.error("Project name cannot be empty.")
        sys.exit(1)

    projects_root = Path(__file__).parent / "config" / "projects"
    project_dir = projects_root / project_name

    if project_dir.exists():
        print(f"\n  ✗ Project '{project_name}' already exists at: {project_dir}")
        print("    Edit the existing files directly.\n")
        sys.exit(1)

    project_dir.mkdir(parents=True, exist_ok=True)

    # ── context.md: copy template from example2 ───────────────────────────────
    template_path = projects_root / "example2" / "context.md"
    context_dest = project_dir / "context.md"
    if template_path.exists():
        context_dest.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        context_dest.write_text(
            f"# {project_name.upper()} — QAX Project Context\n\n"
            "## Project Overview\n- Name: [fill in]\n- Type: [fill in]\n",
            encoding="utf-8",
        )

    # ── selectors.json: scaffold from any existing project's selectors.json ─
    # Use DEFAULT_PROJECT as template source; fall back to first project found.
    _template_proj = os.getenv("DEFAULT_PROJECT", "")
    _template_path: Path | None = None
    if _template_proj:
        _candidate = projects_root / _template_proj / "selectors.json"
        if _candidate.exists():
            _template_path = _candidate
    if _template_path is None:
        # find the first project that has a selectors.json (exclude the new one)
        for _p in sorted(projects_root.iterdir()):
            if _p.is_dir() and _p.name != project_name:
                _c = _p / "selectors.json"
                if _c.exists():
                    _template_path = _c
                    break
    selectors_dest = project_dir / "selectors.json"
    if _template_path is not None:
        try:
            with open(_template_path, encoding="utf-8") as f:
                example_data = json.load(f)

            def _replace_with_todo(obj):
                """Recursively replace selector values with TODO placeholders."""
                if isinstance(obj, dict):
                    return {k: _replace_with_todo(v) for k, v in obj.items()}
                if isinstance(obj, list):
                    return [_replace_with_todo(i) for i in obj]
                if isinstance(obj, str) and not obj.startswith("_") and "TODO" not in obj:
                    return "TODO: add selector"
                return obj

            scaffold = _replace_with_todo(example_data)
            with open(selectors_dest, "w", encoding="utf-8") as f:
                json.dump(scaffold, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("Could not scaffold selectors.json: %s", e)
            selectors_dest.write_text('{"global": {}, "_comment": "Add your CSS selectors here"}', encoding="utf-8")
    else:
        selectors_dest.write_text('{"global": {}, "_comment": "Add your CSS selectors here"}', encoding="utf-8")
    # clean up temp vars
    del _template_proj, _template_path

    # ── Print instructions ─────────────────────────────────────────────────
    print(f"\n  ✓ Project '{project_name}' initialized.")
    print(f"    Location : {project_dir}")
    print(f"    Files    : context.md, selectors.json\n")
    print("  Next steps:")
    print(f"    1. Edit config/projects/{project_name}/context.md")
    print("       with your project details")
    print(f"    2. Fill config/projects/{project_name}/selectors.json")
    print("       with real CSS selectors from your app")
    print("    3. Run:")
    print(f"         python main.py analyze \\")
    print(f"           --file your_file.py \\")
    print(f"           --project {project_name} \\")
    print(f"           --dry-run\n")


# ══════════════════════════════════════════════════════════════════════════════
# DISCOVER COMMAND
# ══════════════════════════════════════════════════════════════════════════════

def run_discover_command() -> None:
    """
    Handle the 'discover' subcommand.

    Launches a Playwright browser, navigates to one or more pages, and uses
    DOMAnalyzer to automatically extract CSS selectors from the live DOM.
    Results are displayed in the terminal and can be saved to
    config/projects/{project}/selectors.json.

    Single-page mode:
      python main.py discover --url URL --page PAGE_NAME [--project example] [--headless]

    Batch mode (reads page_name|url pairs from a file):
      python main.py discover --sitemap --urls-file pages.txt --project example
      pages.txt format (one entry per line):
        checkout|https://app.com/checkout
        login|https://app.com/login
        dashboard|https://app.com/dashboard

    Called when sys.argv[1] == "discover".
    """
    import asyncio

    parser = argparse.ArgumentParser(
        prog="qax discover",
        description="Auto-discover CSS selectors from a live web application.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py discover --url https://app.com/checkout --page checkout
  python main.py discover --url https://app.com/login --page login --headless
  python main.py discover --sitemap --urls-file pages.txt --project example
        """
    )

    # ── Mode: single URL or batch sitemap ─────────────────────────────────
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--url",
        type=str,
        metavar="URL",
        help="URL of the page to discover selectors from."
    )
    mode_group.add_argument(
        "--sitemap",
        action="store_true",
        default=False,
        help="Batch discovery mode — reads page_name|url pairs from --urls-file."
    )

    # ── Required for single mode ───────────────────────────────────────────
    parser.add_argument(
        "--page",
        type=str,
        metavar="NAME",
        default=None,
        help="Page name / selector section (e.g. 'checkout'). Required with --url."
    )

    # ── Batch mode file ────────────────────────────────────────────────────
    parser.add_argument(
        "--urls-file",
        type=str,
        metavar="PATH",
        default=None,
        help="Path to file with page_name|url entries. Required with --sitemap."
    )

    # ── Common options ─────────────────────────────────────────────────────
    parser.add_argument(
        "--project",
        type=str,
        default=os.getenv("DEFAULT_PROJECT", "example"),
        help="Project name for selector storage. Default: DEFAULT_PROJECT env (example)."
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run browser in headless mode (no visible window)."
    )

    args = parser.parse_args(sys.argv[2:])

    # ── Validate combinations ──────────────────────────────────────────────
    if args.url and not args.page:
        parser.error("--page is required when using --url")
    if args.sitemap and not args.urls_file:
        parser.error("--urls-file is required when using --sitemap")

    # ── Import DOMAnalyzer ─────────────────────────────────────────────────
    try:
        from core.dom_analyzer import DOMAnalyzer
    except ImportError as e:
        logger.error("DOMAnalyzer import failed: %s", e)
        sys.exit(1)

    # ── Check Playwright ───────────────────────────────────────────────────
    try:
        from execution.playwright_runner import PLAYWRIGHT_AVAILABLE
    except ImportError:
        PLAYWRIGHT_AVAILABLE = False

    if not PLAYWRIGHT_AVAILABLE:
        print("\n  ✗ Playwright is not installed.")
        print("    Install with:")
        print("      pip install playwright==1.44.0")
        print("      playwright install chromium\n")
        sys.exit(1)

    # ── Build page list ────────────────────────────────────────────────────
    pages_to_discover: list[tuple[str, str]] = []  # [(page_name, url), ...]

    if args.url:
        pages_to_discover = [(args.page, args.url)]
    else:
        # Batch: read from pages.txt
        urls_path = Path(args.urls_file)
        if not urls_path.exists():
            logger.error("URLs file not found: %s", args.urls_file)
            sys.exit(1)
        try:
            with open(urls_path, encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("|", 1)
                    if len(parts) != 2:
                        logger.warning("Skipping malformed line (expected page_name|url): %s", line)
                        continue
                    page_name, url = parts[0].strip(), parts[1].strip()
                    if page_name and url:
                        pages_to_discover.append((page_name, url))
        except IOError as e:
            logger.error("Cannot read URLs file: %s", e)
            sys.exit(1)

    if not pages_to_discover:
        logger.error("No pages to discover. Check --url or --urls-file entries.")
        sys.exit(1)

    # ── Async discovery pipeline ───────────────────────────────────────────
    async def _run_discovery() -> list[tuple[str, dict]]:
        """
        Navigate to each page with Playwright and run DOMAnalyzer.

        Returns:
            list[tuple[str, dict]]: [(page_name, selectors_dict), ...] for each page.
        """
        from playwright.async_api import async_playwright

        analyzer = DOMAnalyzer()
        all_results: list[tuple[str, dict]] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=args.headless)
            context = await browser.new_context()
            page = await context.new_page()

            for page_name, url in pages_to_discover:
                print(f"\n  → Discovering: [{page_name}] {url}")
                try:
                    selectors = await analyzer.discover_selectors(url, page_name, page)
                    all_results.append((page_name, selectors))
                    found = len(selectors)
                    print(f"    Found {found} selector(s).")
                    for key, value in selectors.items():
                        print(f"      {key:<40} {value}")
                except Exception as _de:
                    logger.error("Discovery failed for '%s': %s", page_name, _de)
                    all_results.append((page_name, {}))

            await browser.close()

        return all_results

    # ── Run ────────────────────────────────────────────────────────────────
    print(f"\n  QAX — Selector Discovery")
    print(f"  Project  : {args.project}")
    print(f"  Pages    : {len(pages_to_discover)}")
    print(f"  Headless : {args.headless}")

    try:
        discovered = asyncio.run(_run_discovery())
    except Exception as e:
        logger.error("Discovery pipeline failed: %s", e)
        sys.exit(1)

    total_found = sum(len(s) for _, s in discovered)

    if total_found == 0:
        print("\n  No selectors discovered — nothing to save.")
        sys.exit(0)

    print(f"\n  Total selectors discovered: {total_found}")

    # ── Ask to save ────────────────────────────────────────────────────────
    try:
        answer = input("\n  Save these selectors? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    if answer != "y":
        print("  → Selectors not saved.\n")
        sys.exit(0)

    # ── Persist to selectors.json ──────────────────────────────────────────
    try:
        from core.dom_analyzer import DOMAnalyzer  # already imported, but safe
        saver = DOMAnalyzer()
        saved_count = 0
        for page_name, selectors in discovered:
            if selectors:
                saver.save_selectors(page_name, selectors, project=args.project)
                saved_count += len(selectors)

        selectors_path = (
            Path(__file__).parent / "config" / "projects" / args.project / "selectors.json"
        )
        print(f"\n  ✓ Saved {saved_count} selector(s) to:")
        print(f"    {selectors_path}\n")
    except Exception as e:
        logger.error("Failed to save selectors: %s", e)
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE — Generate → Export → Execute in one step
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline_command() -> None:
    """
    Run the full QAX pipeline: Generate → Export → Execute.

    Supports flexible input sources (file, folder, Jira ticket, GitHub PR,
    plain story text), swappable export adapters, and three execution modes
    (UI via Playwright, API via HTTP, or both).

    Usage:
      python main.py pipeline --input INPUT_TYPE --source SOURCE [options]
      python main.py pipeline --file PATH --export ADAPTER [options]  ← legacy

    Examples:
      python main.py pipeline --input file --source src/Controller.cs --export xray --dry-run
      python main.py pipeline --input jira --source PROJ-123 --export excel
      python main.py pipeline --input pr --source 142 --export none --exec-mode ui
      python main.py pipeline --input story --source "As a user I want to login" --exec-mode api
      python main.py pipeline --file src/Controller.cs --export excel  ← still works
    """
    parser = argparse.ArgumentParser(
        prog="main.py pipeline",
        description="QAX Pipeline — Generate → Export → Execute in one step",
    )

    # ── Input source ───────────────────────────────────────────────────────────
    input_group = parser.add_argument_group("Input")
    input_group.add_argument(
        "--input",
        choices=["file", "folder", "jira", "pr", "story"],
        default="file",
        help="Input adapter — where to read source material from (default: file).",
    )
    input_group.add_argument(
        "--source",
        default=None,
        help=(
            "Source value for the input adapter: file path, folder path, "
            "Jira ticket key (e.g. PROJ-123), PR number (e.g. 142), "
            "or plain story text."
        ),
    )
    # Legacy --file flag kept for backward compatibility
    input_group.add_argument(
        "--file",
        default=None,
        dest="legacy_file",
        help="[Legacy] Path to a single source file. Equivalent to --input file --source PATH.",
    )

    # ── Export ─────────────────────────────────────────────────────────────────
    parser.add_argument(
        "--export",
        choices=["xray", "zephyr", "excel", "csv", "none"],
        default="none",
        help="Output adapter to use (default: none — keep in memory only).",
    )

    # ── Execution ──────────────────────────────────────────────────────────────
    parser.add_argument(
        "--exec-mode",
        choices=["ui", "api", "both", "none"],
        default="none",
        help=(
            "Execution engine: ui=Playwright, api=HTTP requests, "
            "both=run both, none=skip execution (default: none)."
        ),
    )

    # ── Project / module ───────────────────────────────────────────────────────
    parser.add_argument(
        "--project",
        default=os.getenv("DEFAULT_PROJECT", ""),
        help="Project name for context/selectors. Default: DEFAULT_PROJECT env.",
    )
    parser.add_argument(
        "--module",
        default=None,
        help="Module hint for the analyzer (e.g. 'Auth', 'Payments').",
    )

    # ── Analysis options ───────────────────────────────────────────────────────
    parser.add_argument(
        "--include-git", action="store_true", default=False,
        help="Include git history in analysis for risk assessment.",
    )
    parser.add_argument(
        "--include-deps", action="store_true", default=False,
        help="Include dependency analysis.",
    )
    parser.add_argument(
        "--include-bugs", action="store_true", default=False,
        help="Fetch recurring bug patterns from Jira for this module.",
    )
    parser.add_argument(
        "--story", default=None,
        help="User story text to focus generation (when --input is not 'story').",
    )
    parser.add_argument(
        "--ext",
        default=None,
        help="File extension filter for folder input (e.g. .py, .ts). Default: auto-detected (the folder's most common source language).",
    )
    parser.add_argument(
        "--test-type",
        choices=["auto", "regression", "feature", "smoke", "uat", "api", "e2e"],
        default="auto",
        help="Override test type detection (default: auto).",
    )

    # ── Execution options ──────────────────────────────────────────────────────
    parser.add_argument(
        "--headless", action="store_true", default=True,
        help="Run Playwright browser headless when using UI execution (default: True).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        default=os.getenv("DRY_RUN", "true").lower() == "true",
        help="Simulate export/push without making external API calls.",
    )

    args = parser.parse_args(sys.argv[2:])

    # Resolve source: --source wins over legacy --file
    source = args.source or args.legacy_file
    if not source:
        print("\n  ERROR: Provide --source PATH or --file PATH\n")
        parser.print_help()
        sys.exit(1)

    # If legacy --file used without --input, force input=file
    if args.legacy_file and not args.source:
        args.input = "file"

    from core.pipeline import QAXPipeline

    pipeline = QAXPipeline(
        export=args.export,
        project=args.project,
        dry_run=args.dry_run,
        headless=args.headless,
        input_type=args.input,
        exec_mode=args.exec_mode,
    )

    src_display = source[:37] if source else ""
    print(f"\n  ┌─────────────────────────────────────────┐")
    print(f"  │  QAX Pipeline                           │")
    print(f"  ├─────────────────────────────────────────┤")
    print(f"  │  Input:   [{args.input:<6}] {src_display:<29} │")
    print(f"  │  Export:  {args.export:<37} │")
    print(f"  │  Exec:    {args.exec_mode:<37} │")
    print(f"  │  Project: {(pipeline.project or '(default)'):<37} │")
    print(f"  │  DRY RUN: {str(args.dry_run):<37} │")
    print(f"  └─────────────────────────────────────────┘\n")

    result = pipeline.run(
        source=source,
        module=args.module,
        user_story=args.story,
        include_git=args.include_git,
        include_deps=args.include_deps,
        include_bugs=args.include_bugs,
        test_type=None if args.test_type == "auto" else args.test_type,
        ext=args.ext,
    )

    # ── Summary ────────────────────────────────────────────────────────────────
    gen = result.get("generation") or {}
    exp = result.get("export")     or {}
    exc = result.get("execution")

    print("\n  ━━━ Pipeline Summary ━━━")
    print(f"  Input:      [{args.input}] {source[:40]}")
    print(f"  Generated:  {gen.get('count', 0)} test case(s)")
    print(f"  Exported:   {exp.get('exported_count', 0)} [{args.export}]")
    if exp.get("references"):
        for ref in exp["references"][:5]:
            print(f"              → {ref}")
    if exc:
        # Handle "both" mode (nested api/ui dicts)
        if "api" in exc and "ui" in exc:
            print(f"  API exec:   {exc['api'].get('passed',0)} passed / {exc['api'].get('failed',0)} failed")
            print(f"  UI exec:    {exc['ui'].get('passed',0)} passed / {exc['ui'].get('failed',0)} failed")
        else:
            print(f"  Executed:   {exc.get('total', 0)} total")
            print(f"  Passed:     {exc.get('passed', 0)}")
            print(f"  Failed:     {exc.get('failed', 0)}")
            if exc.get("error"):
                print(f"  Exec error: {exc['error']}")
    if exp.get("error"):
        print(f"  Export err: {exp['error']}")
    print()

    if not result.get("success"):
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# UI — Streamlit web interface
# ══════════════════════════════════════════════════════════════════════════════

def run_ui_command() -> None:
    """
    Launch the QAX Streamlit UI on localhost:8501.

    Runs: streamlit run ui/app.py with dark-theme flags and --server.port 8501.
    The process inherits the current terminal so Ctrl-C stops it cleanly.
    """
    import subprocess

    ui_path = Path(__file__).parent / "ui" / "app.py"
    if not ui_path.exists():
        print(f"\n  [QAX] ERROR: UI file not found at {ui_path}")
        print("  Make sure ui/app.py exists.\n")
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "streamlit", "run", str(ui_path),
        "--theme.base",                    "dark",
        "--theme.primaryColor",            "#7c3aed",
        "--theme.backgroundColor",         "#0a0e1a",
        "--theme.secondaryBackgroundColor","#111827",
        "--theme.textColor",               "#f1f5f9",
        "--server.port",                   "8501",
        "--server.headless",               "true",
        "--browser.gatherUsageStats",      "false",
    ]

    print("\n  ┌─────────────────────────────────────────┐")
    print("  │  QAX  ·  Streamlit UI                   │")
    print("  │  http://localhost:8501                  │")
    print("  │  Press Ctrl-C to stop                  │")
    print("  └─────────────────────────────────────────┘\n")

    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        print("\n  [QAX] UI stopped.\n")
    except FileNotFoundError:
        print("\n  [QAX] ERROR: streamlit not found.")
        print("  Install it with: pip install streamlit\n")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# HELP
# ══════════════════════════════════════════════════════════════════════════════

def show_help() -> None:
    """
    Print the full command reference and usage examples.
    """
    print(__doc__)
    print("Use 'python main.py <command> --help' for details on any command.")
    print()
    print("Commands:")
    print("  analyze          Generate test cases from source code")
    print("  run              Config-driven pipeline with session tracking")
    print("  execute          Run test cases with Playwright browser")
    print("  feedback         Record QA decisions and improve output quality")
    print("  dashboard        Real-time web dashboard (localhost:5000)")
    print("  discover         Auto-discover CSS selectors from a live app")
    print("  check-connection Test all API connections (Claude + Jira + Xray)")
    print("  init-project     Create a new project config folder")
    print("  cache            Manage the file-level result cache (--stats / --clear)")
    print("  ui               Launch the Streamlit web interface (localhost:8501)")
    print("  pipeline         Generate → Export → Execute in one step")
    print()
    print("Examples:")
    print("  python main.py pipeline --input file --source src/Controller.cs --export xray --dry-run")
    print("  python main.py pipeline --input jira --source PROJ-123 --export excel")
    print("  python main.py pipeline --input pr --source 142 --export none --exec-mode ui")
    print("  python main.py pipeline --input story --source 'As a user I want to login' --exec-mode api")
    print("  python main.py pipeline --file src/Controller.cs --export excel  # legacy")
    print("  python main.py analyze --file src/Orders/ChecklistController.cs --dry-run")
    print("  python main.py analyze --file src/Orders/Ctrl.cs --output pr_comment.md")
    print("  python main.py run --mode smoke")
    print("  python main.py run --mode generate --file path/to/file.cs")
    print("  python main.py execute --suite smoke --headless false")
    print("  python main.py execute --file output/test_cases_20250101.json")
    print("  python main.py feedback --stats")
    print("  python main.py dashboard")
    print("  python main.py check-connection")
    print("  python main.py init-project --name PROJECT_NAME")
    print()


# ══════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _collect_files_from_args(
    file: str | None,
    folder: str | None,
    ext: str | None = None,
) -> list[str]:
    """Files to analyze from --file/--folder (logic in cli/files, S8); exits on bad input."""
    from cli.files import collect_source_files
    try:
        files = collect_source_files(file, folder, ext)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    if folder and not files:
        logger.warning("No matching files found in %s", folder)
        sys.exit(0)
    return files


def _process_single_file(
    file_path: str,
    claude: ClaudeClient,
    module_hint: str | None,
    chunk_size: int,
    include_deps: bool = False,
    git_analyzer: GitAnalyzer | None = None,
    bug_fetcher: BugFetcher | None = None,
    screenshot: bool = False,
    use_cache: bool = True,
    user_story: str | None = None,
    test_type_override: str | None = None,
    project: str = "",
) -> list[dict]:
    """
    Run the full Claude analysis pipeline for one source file.

    Handles: testability check → SmartChecker pre-checks (B5) →
    optional enhancements (screenshot, deps, git history, bug patterns)
    → chunk → analyze → return raw test cases.

    Args:
        file_path (str): Path to the source file.
        claude (ClaudeClient): Initialized Claude client.
        module_hint (str | None): Module name override.
        chunk_size (int): Max lines per chunk.
        include_deps (bool): Resolve and include local import files.
        git_analyzer (GitAnalyzer | None): If set, run git history analysis.
        bug_fetcher (BugFetcher | None): If set, fetch Jira bug patterns.
        screenshot (bool): If True, attempt screenshot capture.
        use_cache (bool): If True, check file-level cache before calling API.
        user_story (str | None): User story text injected into the prompt.
        test_type_override (str | None): Force a specific test type instead of
                                          auto-detecting (from --test-type flag).
        project (str): Project name for SmartChecker context.

    Returns:
        list[dict]: Raw test case dicts from Claude. Empty list on skip/error.
    """
    logger.info("Processing: %s", file_path)

    # ── Read the file ──────────────────────────────────────────────────────
    try:
        file_data = read_file(file_path)
    except (FileNotFoundError, ValueError) as e:
        logger.error("Cannot read %s: %s", file_path, e)
        return []

    effective_module = module_hint or file_data.get("module_hint")

    # ── Skip non-testable files ────────────────────────────────────────────
    if not is_testable(file_data["content"]):
        logger.info("Skipping %s — no testable logic", file_data["file_name"])
        return []

    # ── Optional: screenshot ───────────────────────────────────────────────
    screenshot_b64 = None
    if screenshot:
        if os.getenv("ENABLE_SCREENSHOT", "false").lower() == "true":
            try:
                import asyncio
                from core.screenshot_capture import ScreenshotCapture
                capture = ScreenshotCapture()
                screenshot_b64 = asyncio.run(capture.capture_module(effective_module or "General"))
            except Exception as e:
                logger.warning("Screenshot capture failed: %s", e)
        else:
            logger.info("--screenshot set but ENABLE_SCREENSHOT=false — skipping")

    # ── Optional: dependency files ─────────────────────────────────────────
    dependencies = None
    if include_deps:
        dependencies = extract_dependencies(
            file_content=file_data["content"],
            file_path=file_path,
        )
        if dependencies:
            logger.info(
                "  → %d dependency file(s): %s",
                len(dependencies),
                ", ".join(d["file_name"] for d in dependencies)
            )

    # ── Optional: git history ──────────────────────────────────────────────
    git_history = None
    if git_analyzer:
        git_history = git_analyzer.get_file_history(file_path)
        risk = git_history.get("risk_level", "low")
        if risk in ("high", "medium"):
            logger.info(
                "  → Git risk: %s (%d bug-fix commits)",
                risk.upper(), git_history.get("bug_fix_count", 0)
            )

    # ── B5: SmartChecker pre-checks ───────────────────────────────────────
    smart_checks: dict = {}
    try:
        from core.smart_checker import SmartChecker
        checker = SmartChecker(project=project)
        smart_checks = checker.run_pre_checks(
            file_path=file_path,
            file_content=file_data["content"],
            module=effective_module,
            git_history=git_history,
            user_story=user_story,
        )
        print(f"  ┌─ Pre-check: {smart_checks['pre_check_summary']}")
        # Honour test_type_override from --test-type flag
        if test_type_override:
            smart_checks["test_type"] = test_type_override
            logger.info("  → Test type overridden: %s", test_type_override)
        else:
            logger.info("  → Detected test type: %s", smart_checks.get("test_type", "feature"))

        # Log risk and model
        risk_info = smart_checks.get("risk", {})
        model_name = smart_checks.get("recommended_model", "")
        logger.info(
            "  → Risk: %s (%s) → using %s",
            risk_info.get("risk_level", "?").upper(),
            risk_info.get("test_priority", "?"),
            model_name,
        )

        # Early return on cache hit
        if smart_checks.get("cache_hit") and use_cache:
            cached = smart_checks.get("cached_result", [])
            logger.info(
                "  → Cache hit — skipping API call (%d cached test case(s))",
                len(cached),
            )
            return cached

    except Exception as _sce:
        logger.debug("SmartChecker failed (non-fatal): %s", _sce)

    # ── Optional: bug patterns ─────────────────────────────────────────────
    bug_patterns_text = None
    if bug_fetcher and effective_module:
        bug_patterns_text = bug_fetcher.get_patterns_for_module(effective_module)
        if bug_patterns_text:
            logger.info("  → Bug patterns fetched for module '%s'", effective_module)

    # ── Chunk + analyze ────────────────────────────────────────────────────
    chunks = chunk_large_file(
        file_data["content"],
        max_lines=chunk_size,
        file_name=file_data["file_name"],
    )
    total_chunks = len(chunks)
    if total_chunks > 1:
        logger.info(
            "  → Split into %d chunks (size=%d, overlap=30 lines)", total_chunks, chunk_size
        )

    all_raw = []
    for chunk_dict in chunks:
        if total_chunks > 1:
            logger.info(
                "  → Chunk %d/%d (lines %d–%d)",
                chunk_dict["chunk_index"], total_chunks,
                chunk_dict["start_line"], chunk_dict["end_line"],
            )
        # Use model from SmartChecker (B5/B2), fallback to legacy logic
        line_count = file_data.get("line_count", 0)
        risk_level = "low"
        if git_history:
            risk_level = git_history.get("risk_level", "low")

        # SmartChecker may have promoted risk to "critical" (B2)
        smart_risk = smart_checks.get("risk", {})
        if smart_risk.get("risk_level"):
            risk_level = smart_risk["risk_level"]

        model_override = smart_checks.get("recommended_model") or None

        try:
            cases = claude.analyze_code(
                code=chunk_dict["content"],
                file_path=file_path,
                module_hint=effective_module,
                screenshot_base64=screenshot_b64,
                dependencies=dependencies,
                bug_patterns=bug_patterns_text,
                line_count=line_count,
                risk_level=risk_level,
                use_cache=use_cache,
                user_story=user_story,
                skill_context=smart_checks.get("skill_context"),
                risk_assessment=smart_checks.get("risk"),
                test_type=smart_checks.get("test_type"),
                model_override=model_override,
            )
            all_raw.extend(cases)
        except Exception as e:
            err_str = str(e)
            if "credit balance is too low" in err_str:
                logger.error(
                    "Insufficient API credits. "
                    "Add credits at: https://console.anthropic.com/settings/billing"
                )
                sys.exit(1)
            logger.error("Error analyzing chunk %d: %s", chunk_dict["chunk_index"], e)

    # ── Deduplicate by title across chunks ─────────────────────────────────
    if total_chunks > 1 and all_raw:
        before = len(all_raw)
        all_raw = ClaudeClient.deduplicate_by_title(all_raw)
        if len(all_raw) < before:
            logger.info(
                "  → Removed %d duplicate(s) after chunk merge", before - len(all_raw)
            )

    logger.info(
        "Generated %d raw test case(s) from %s",
        len(all_raw), file_data["file_name"]
    )
    return all_raw


def _save_results_as_markdown(
    test_cases: list[dict],
    source_files: list[str],
    output_path: str,
) -> str:
    """Save test cases as a Markdown PR comment (logic in cli/output, S8)."""
    from cli.output import save_results_markdown
    return save_results_markdown(test_cases, source_files, output_path)


def _save_results(test_cases: list[dict], output_path: str | None) -> str:
    """Save test cases to JSON in output/ (logic in cli/output, S8)."""
    from cli.output import save_results_json
    return save_results_json(test_cases, output_path)


def _print_push_summary(push_result: dict) -> None:
    """Print a formatted Xray push summary (logic in cli/summaries, S8)."""
    from cli.summaries import render_push_summary
    print(render_push_summary(push_result))


def _print_run_summary(mode: str, results: dict) -> None:
    """Print a short summary of a run_* result (logic in cli/summaries, S8)."""
    from cli.summaries import render_run_summary
    print(render_run_summary(mode, results))


def _print_sessions(sessions: list[dict]) -> None:
    """Print recent run sessions (logic in cli/summaries, S8)."""
    from cli.summaries import render_sessions
    print(render_sessions(sessions))


def _find_test_case_in_output(tc_id: str) -> dict | None:
    """Find a saved test case by ID in output/ (logic in cli/output, S8)."""
    from cli.output import find_test_case_in_output
    return find_test_case_in_output(tc_id)


# ══════════════════════════════════════════════════════════════════════════════
# SCAN COMMAND — the full autonomous agent team (Orchestrator)
# ══════════════════════════════════════════════════════════════════════════════

def run_scan_command() -> None:
    """
    Handle the 'scan' subcommand — a deep, autonomous security + QA scan.

    Runs the whole agent team over a repository against a live app in one call:
    whole-system model → detectors → active probes → generation + red-team →
    coverage loop → execute → critic → investigator. Prints the confirmed bugs
    (from the Investigator and the probes) and the coverage it guaranteed.

    Called when sys.argv[1] == "scan".
    """
    parser = argparse.ArgumentParser(
        prog="qax scan",
        description="Deep autonomous scan: model the whole app, hunt and PROVE bugs.")
    parser.add_argument("--repo", default=None,
                        help="Path to the repository to scan. Optional — omit for a UI-only / "
                             "no-source target; QAX then scans the live app only (grounding + "
                             "probes + --tour).")
    parser.add_argument("--url", required=True,
                        help="Base URL of the running app (e.g. http://localhost:8000).")
    parser.add_argument("--project", default=None, help="QAX project name (optional).")
    parser.add_argument("--max-router-files", type=int, default=4,
                        help="Hard ceiling on router files for functional generation.")
    parser.add_argument("--max-endpoints", type=int, default=40,
                        help="Functional-generation budget in endpoints (routers chosen "
                             "highest-risk-first until spent, bounded by --max-router-files).")
    parser.add_argument("--quick", action="store_true",
                        help="Quick scan — skip the LLM-heavy passes (knowledge, functional "
                             "generation, red-team). Uses probes + deterministic coverage only; "
                             "near-free, still proves JWT forgery/crashes and the security classes.")
    parser.add_argument("--refresh", action="store_true",
                        help="Ignore the cached app model and rebuild it.")
    parser.add_argument("--out", default=None, help="Write the full result to this JSON file.")
    parser.add_argument("--truth", default=None,
                        help="Ground-truth JSON for the target (recall + false-positive scoring).")
    parser.add_argument("--token", default=None,
                        help="A valid auth token for apps QAX can't self-register into "
                             "(external IdP, offline-signed JWT, API key). Enables auth-gated probes.")
    parser.add_argument("--token-b", default=None,
                        help="A second user's token, for IDOR/BOLA (userB reaching userA's data).")
    parser.add_argument("--auth-header", default="Authorization",
                        help="Header name for the token (default: Authorization).")
    parser.add_argument("--auth-scheme", default="Bearer",
                        help="Scheme prefix for the token (default: Bearer; use '' for a raw value / API key).")
    parser.add_argument("--tour", action="store_true",
                        help="Also tour the live UI in a real browser and fold what it sees "
                             "(uncaught JS errors, failed API calls) into the findings. Needs "
                             "Playwright — run this scan under the exec venv (.venv-exec, Python 3.12).")
    parser.add_argument("--visual-baseline", default=None,
                        help="Directory of baseline screenshots for visual regression (with --tour). "
                             "First run seeds it; later runs flag screens whose rendering changed.")
    parser.add_argument("--dry-run", action="store_true",
                        help="No external side effects (exports); the scan still runs.")
    parser.add_argument("--authorized", action="store_true",
                        help="Confirm you OWN or are explicitly permitted to actively test this "
                             "target. A scan runs active, data-MUTATING probes (it creates test "
                             "records and sends adversarial input). Required — a scan refuses "
                             "without it. Never run against a target you are not authorized to test.")
    args = parser.parse_args(sys.argv[2:])

    # S4 — authorized-target gate: active probing mutates the target, so require an
    # explicit ownership/permission confirmation before anything runs.
    if not args.authorized:
        from execution.probes import MUTATING_PROBES
        print("\n  Refusing to scan: an active scan runs data-MUTATING probes "
              f"({', '.join(sorted(MUTATING_PROBES))})\n  — it creates test records and sends "
              "adversarial input to the target.\n  Re-run with --authorized to confirm you own "
              "or are permitted to test this target.\n")
        sys.exit(2)

    from services import scorecard as _scorecard
    from services import usage as _usage
    from services.context import QAXContext
    from services.orchestrator_service import OrchestratorService
    from services.progress import PrintProgressReporter

    if args.repo:
        if not Path(args.repo).exists():
            print(f"\n  Repo path not found: {args.repo}\n")
            sys.exit(1)
        repo_path = args.repo
    else:
        # No repo — UI-only / live-app-only scan. Use an empty dir so the model
        # degrades cleanly to live-app grounding + probes + tour.
        import tempfile
        repo_path = tempfile.mkdtemp(prefix="qax_nosrc_")

    try:
        ctx = QAXContext.from_env(project=args.project or "", dry_run=args.dry_run)
        ctx.require_anthropic_key()
    except Exception as exc:
        print(f"\n  {exc}\n")
        sys.exit(1)

    mode = "quick" if args.quick else "deep"
    print(f"\n  QAX {mode} scan — repo: {args.repo or '(none — live app only)'}  ·  app: {args.url}\n")

    # Instrument this run: count every LLM call (for $/run) and time it.
    _usage.track()
    _usage.reset()
    _auth = None
    if args.token or args.token_b:
        _auth = {"token": args.token, "token_b": args.token_b,
                 "header": args.auth_header, "scheme": args.auth_scheme}
        print(f"  (using a supplied auth token via '{args.auth_header}')\n")
    _t0 = time.time()
    result = OrchestratorService(ctx).run(
        repo_path, args.url,
        deep=not args.quick,
        max_router_files=args.max_router_files,
        max_endpoints=args.max_endpoints,
        use_cache=not args.refresh,
        auth=_auth,
        tour=args.tour,
        visual_baseline=args.visual_baseline,
        progress=PrintProgressReporter())
    _elapsed = time.time() - _t0

    if result.error:
        print(f"\n  Scan failed: {result.error}\n")
        sys.exit(1)

    from cli.scan_render import render_scan_result
    print(render_scan_result(result))   # S8: extracted, B1-aligned (deterministic vs AI leads)

    # Scorecard — the honest, trackable number for this run.
    _truth = None
    if args.truth:
        try:
            _truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read --truth file: %s", exc)
    try:
        _rec = _scorecard.score_run(
            result, repo=(args.repo or args.url), url=args.url, mode=mode,
            duration_sec=_elapsed, usage=_usage.snapshot(), truth=_truth)
        _path = _scorecard.write_run(_rec)
        print(_scorecard.render(_rec))
        if _path:
            print(f"     saved: {_path}")
        print()
    except Exception as exc:
        logger.warning("scorecard failed (scan result is unaffected): %s", exc)

    if args.out:
        try:
            Path(args.out).write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
                                      encoding="utf-8")
            print(f"  Full result written to {args.out}\n")
        except Exception as exc:
            logger.warning("Could not write --out file: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """
    Main entry point. Routes to the correct subcommand handler.

    Supported subcommands:
      analyze          — Generate test cases from source code
      run              — Config-driven pipeline with session tracking
      execute          — Run test cases with Playwright browser
      feedback         — Record QA decisions, view stats, optimize prompts
      dashboard        — Start the real-time web dashboard
      discover         — Auto-discover CSS selectors from a live app
      check-connection — Verify Claude + Jira + Xray connectivity
      init-project     — Create a new project config folder
      cache            — Manage file-level result cache
      --help / -h      — Show full command reference
    """
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        show_help()
        return

    command = sys.argv[1]

    dispatch = {
        "analyze":          run_analyze_command,
        "run":              run_run_command,
        "execute":          run_execute_command,
        "feedback":         run_feedback_command,
        "dashboard":        run_dashboard_command,
        "discover":         run_discover_command,
        "check-connection": run_check_connection_command,
        "init-project":     run_init_project_command,
        "cache":            run_cache_command,
        "ui":               run_ui_command,
        "pipeline":         run_pipeline_command,
        "scan":             run_scan_command,
    }

    if command in dispatch:
        dispatch[command]()
    else:
        print(f"\n  Unknown command: '{command}'")
        print("  Valid commands: analyze, run, execute, feedback, dashboard, discover, check-connection, init-project, cache, ui, pipeline, scan")
        print("  Run 'python main.py --help' for usage.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
