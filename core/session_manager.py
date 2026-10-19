"""
session_manager.py — Run Session Manager
=========================================
Enhancement 6: Session Manager.

Manages configuration loading, CLI flag merging, mode resolution,
and session file persistence for the `run` subcommand.

Every execution of `python main.py run ...` creates a session file
in output/sessions/ with full context: mode, config used, results,
and timing — giving you a complete audit trail of every agent run.

Session files are named: session_{YYYYMMDD_HHMMSS}.json

Supported modes:
    smoke       — Quick sanity checks on critical paths (uses configs/smoke.json)
    regression  — Full regression suite on configured source files
    bugs        — Analyze source files related to specific Jira bug IDs
    tickets     — Analyze files related to specific Jira ticket IDs
    module      — Analyze all source files for a specific EXAMPLE module
    generate    — Generate test cases for a specific file or directory
    release     — Analyze files changed in a specific release version

Usage:
    from core.session_manager import SessionManager
    sm = SessionManager()
    config = sm.load_config("configs/smoke.json")
    config = sm.merge_with_flags(config, {"dry_run": True, "module": "Orders"})
    mode = sm.resolve_mode(config)
    # ... run analysis pipeline ...
    session_path = sm.save_session(mode, config, results)
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Load environment variables ────────────────────────────────────────────────
load_dotenv()

# ── Session output directory ──────────────────────────────────────────────────
SESSIONS_DIR = Path("output") / "sessions"

# ── Default mode if none is specified ────────────────────────────────────────
DEFAULT_MODE = "generate"

# ── Valid modes ───────────────────────────────────────────────────────────────
VALID_MODES = frozenset(["smoke", "regression", "bugs", "tickets", "module", "generate", "release"])

# ── Default config file paths per mode ───────────────────────────────────────
DEFAULT_CONFIGS = {
    "smoke": "configs/smoke.json",
    "regression": "configs/regression.json",
    "release": "configs/smoke.json",   # fallback to smoke config for release mode
}


class SessionManager:
    """
    Manages run sessions for the QAX.

    Handles:
      - Loading a JSON config file (e.g., configs/smoke.json)
      - Merging CLI flags on top of config (CLI flags always win)
      - Resolving which run mode to use
      - Saving a complete session record to output/sessions/

    Each public method has safe defaults — none will crash the main pipeline.
    """

    def __init__(self):
        """
        Initialize the SessionManager.

        Creates the sessions output directory if it doesn't exist.
        """
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        logger.debug("SessionManager initialized. Sessions dir: %s", SESSIONS_DIR)

    # ─────────────────────────────────────────────────────────────────────────
    # Config Loading
    # ─────────────────────────────────────────────────────────────────────────

    def load_config(self, config_path: str) -> dict:
        """
        Load a run configuration from a JSON file.

        Config files are stored in configs/ and define source files,
        modules, run mode, dry_run flag, chunk size, and more.

        Args:
            config_path (str): Path to the JSON config file (e.g., "configs/smoke.json").

        Returns:
            dict: Parsed configuration dictionary.
                  Returns empty dict if file does not exist or cannot be parsed.

        Note:
            Keys starting with "_" in the JSON (like "_comment") are stripped
            from the returned config — they are documentation only.
        """
        path = Path(config_path)

        if not path.exists():
            logger.warning(
                "Config file not found: %s — using empty config (CLI flags only)",
                config_path
            )
            return {}

        try:
            with open(path, encoding="utf-8") as f:
                raw_config = json.load(f)

            # Strip comment keys (keys starting with _) — documentation only
            config = {
                k: v for k, v in raw_config.items()
                if not k.startswith("_")
            }

            logger.info(
                "Loaded config from: %s (mode=%s, dry_run=%s)",
                config_path,
                config.get("mode", "not set"),
                config.get("dry_run", "not set"),
            )
            return config

        except json.JSONDecodeError as e:
            logger.error(
                "Config file is not valid JSON: %s — error: %s",
                config_path, e
            )
            return {}
        except IOError as e:
            logger.error("Cannot read config file %s: %s", config_path, e)
            return {}

    # ─────────────────────────────────────────────────────────────────────────
    # CLI Flag Merging
    # ─────────────────────────────────────────────────────────────────────────

    def merge_with_flags(self, config: dict, cli_flags: dict) -> dict:
        """
        Merge CLI flags on top of a loaded config.

        CLI flags always override config file values. This lets users
        override a config (e.g., override dry_run=false to true) without
        editing the file.

        Only non-None CLI flag values are applied. This means flags that
        were not passed by the user (and defaulted to None) do NOT override
        config values.

        Args:
            config (dict): Base config loaded from file (or empty dict).
            cli_flags (dict): CLI argument values. Only non-None values are merged.
                              Example: {"dry_run": True, "module": "Orders", "version": None}
                              → Only dry_run and module are applied; version is ignored.

        Returns:
            dict: Merged config dict with CLI flags applied on top.
        """
        merged = dict(config)  # Copy to avoid mutating the original

        applied = []
        for key, value in cli_flags.items():
            # Skip None values — these represent flags not passed by the user
            if value is None:
                continue
            # Skip empty lists — argparse default for list flags
            if isinstance(value, list) and len(value) == 0:
                continue

            merged[key] = value
            applied.append(f"{key}={value!r}")

        if applied:
            logger.debug(
                "CLI flags merged onto config: %s",
                ", ".join(applied)
            )

        return merged

    # ─────────────────────────────────────────────────────────────────────────
    # Mode Resolution
    # ─────────────────────────────────────────────────────────────────────────

    def resolve_mode(self, config: dict) -> str:
        """
        Determine which run mode to use based on the merged config.

        Mode resolution order:
          1. config["mode"] — from config file or CLI --mode override
          2. Infer from config keys: if "bugs" key present → bugs mode,
             if "version" key present → release mode, etc.
          3. Default to "generate"

        Args:
            config (dict): Merged configuration dict.

        Returns:
            str: Resolved mode name. Always one of VALID_MODES.
                 Returns "generate" as safe fallback if mode is unrecognized.
        """
        # ── Priority 1: Explicitly set mode ───────────────────────────────
        mode = config.get("mode")
        if mode:
            mode = mode.lower().strip()
            if mode in VALID_MODES:
                logger.debug("Mode explicitly set: %s", mode)
                return mode
            else:
                logger.warning(
                    "Unknown mode '%s' in config. Valid modes: %s. Falling back to '%s'.",
                    mode, ", ".join(sorted(VALID_MODES)), DEFAULT_MODE
                )
                return DEFAULT_MODE

        # ── Priority 2: Infer from config keys ────────────────────────────
        if config.get("bugs"):
            logger.debug("Mode inferred as 'bugs' (bugs key present in config)")
            return "bugs"
        if config.get("tickets"):
            logger.debug("Mode inferred as 'tickets' (tickets key present in config)")
            return "tickets"
        if config.get("version"):
            logger.debug("Mode inferred as 'release' (version key present in config)")
            return "release"
        if config.get("source_files") and config.get("test_type_filter") == "smoke":
            logger.debug("Mode inferred as 'smoke' (source_files + test_type_filter=smoke)")
            return "smoke"
        if config.get("source_files"):
            logger.debug("Mode inferred as 'regression' (source_files present)")
            return "regression"

        # ── Priority 3: Default ───────────────────────────────────────────
        logger.debug("Mode defaulted to '%s'", DEFAULT_MODE)
        return DEFAULT_MODE

    # ─────────────────────────────────────────────────────────────────────────
    # Session Persistence
    # ─────────────────────────────────────────────────────────────────────────

    def save_session(
        self,
        mode: str,
        config: dict,
        results: dict,
    ) -> str:
        """
        Save a complete run session to a JSON file in output/sessions/.

        The session file captures full context for auditability:
        what mode was run, what config was used, what results were produced,
        and how long it took.

        Args:
            mode (str): The run mode that was executed (e.g., "smoke").
            config (dict): The merged config that was used for this run.
            results (dict): Run results summary. Expected keys:
                - files_processed (int): Number of source files analyzed
                - total_generated (int): Total raw test cases generated
                - total_valid (int): Number of test cases that passed validation
                - pushed_count (int): Number of test cases pushed to Xray
                - failed_count (int): Number of push failures
                - duration_seconds (float): Total run duration
                - output_file (str): Path to the generated test cases JSON
                - session_id (str, optional): Override the auto-generated session ID

        Returns:
            str: Path to the saved session file (e.g., "output/sessions/session_20250320_143022.json").
                 Returns empty string if saving fails (non-fatal error).
        """
        # ── Generate session ID and timestamp ─────────────────────────────
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        session_id = results.get("session_id") or f"session_{timestamp}"

        # ── Build session record ───────────────────────────────────────────
        session = {
            "session_id": session_id,
            "mode": mode,
            "timestamp": now.isoformat(),
            "config": {
                # Strip large fields that shouldn't be in session records
                k: v for k, v in config.items()
                if k not in ("source_code", "screenshot_base64")
            },
            "results": {
                "files_processed": results.get("files_processed", 0),
                "total_generated": results.get("total_generated", 0),
                "total_valid": results.get("total_valid", 0),
                "pushed_count": results.get("pushed_count", 0),
                "failed_count": results.get("failed_count", 0),
                "duration_seconds": round(results.get("duration_seconds", 0.0), 2),
                "output_file": results.get("output_file", ""),
            },
        }

        # ── Write session file ─────────────────────────────────────────────
        session_file = SESSIONS_DIR / f"{session_id}.json"

        try:
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session, f, indent=2, ensure_ascii=False)

            logger.info("Session saved: %s", session_file)
            return str(session_file)

        except IOError as e:
            logger.warning("Could not save session file: %s", e)
            return ""

    # ─────────────────────────────────────────────────────────────────────────
    # Convenience: Get Default Config for Mode
    # ─────────────────────────────────────────────────────────────────────────

    def get_default_config_path(self, mode: str) -> str | None:
        """
        Return the default config file path for a given mode.

        Args:
            mode (str): Run mode name (e.g., "smoke").

        Returns:
            str | None: Default config path, or None if there is no default for this mode.
        """
        return DEFAULT_CONFIGS.get(mode)

    # ─────────────────────────────────────────────────────────────────────────
    # Convenience: List Past Sessions
    # ─────────────────────────────────────────────────────────────────────────

    def get_last_session(
        self,
        mode: str | None = None,
        project: str | None = None,
    ) -> dict | None:
        """
        Find the most recent session matching the given mode and/or project.

        Scans output/sessions/ sorted by filename (most recent first) and
        returns the first session that matches all specified filters.
        If no filters are given, returns the absolute most recent session.

        Args:
            mode (str | None): Filter by run mode (e.g., "smoke", "generate").
                               None means any mode.
            project (str | None): Filter by project name stored in the session
                                  config. None means any project.

        Returns:
            dict | None: Full session dict from the JSON file, or None if no
                         matching session exists.
        """
        session_files = sorted(
            SESSIONS_DIR.glob("session_*.json"),
            reverse=True,  # Most recent first
        )

        for sf in session_files:
            try:
                with open(sf, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue

            # Apply mode filter
            if mode and data.get("mode", "") != mode:
                continue

            # Apply project filter (project may be in config)
            if project:
                session_project = data.get("config", {}).get("project", "")
                if session_project and session_project != project:
                    continue

            logger.debug(
                "QAX SessionManager: Found matching session: %s (mode=%s)",
                sf.name, data.get("mode", "?"),
            )
            return data

        logger.debug(
            "QAX SessionManager: No session found matching mode=%s, project=%s",
            mode, project,
        )
        return None

    def resume_from_session(self, session: dict) -> dict:
        """
        Build a resume context dict from a past session.

        Extracts key information about what was processed in the last session
        and suggests what to do next. Useful for the --resume flag in the
        run command.

        Args:
            session (dict): Full session dict returned by get_last_session()
                            or list_sessions(). Expected keys: mode, config,
                            results, timestamp, session_id.

        Returns:
            dict: {
                "last_files_processed": list,    # source files from last run
                "last_module": str,              # module processed (if any)
                "last_confidence_avg": float,    # avg confidence (placeholder)
                "suggested_next": str,           # human-readable suggestion
            }
        """
        config = session.get("config", {})
        results = session.get("results", {})
        mode = session.get("mode", "unknown")
        timestamp = session.get("timestamp", "")[:19].replace("T", " ")

        # ── Extract last files ─────────────────────────────────────────────
        last_files: list[str] = []
        if config.get("file"):
            last_files = [config["file"]]
        elif isinstance(config.get("source_files"), list):
            last_files = [
                e.get("file", str(e)) if isinstance(e, dict) else str(e)
                for e in config["source_files"][:5]
            ]

        last_module = config.get("module", "")
        files_processed = results.get("files_processed", 0)
        total_valid = results.get("total_valid", 0)

        # ── Build suggested_next string ────────────────────────────────────
        if mode in ("smoke", "regression"):
            suggested_next = (
                f"Last {mode} run: {timestamp} — {total_valid} valid test(s).\n"
                f"  → Run again: python main.py run --mode {mode}"
            )
        elif mode == "generate" and last_module:
            suggested_next = (
                f"Last generate run: {timestamp} — {files_processed} file(s) "
                f"processed, {total_valid} valid test(s) in module '{last_module}'.\n"
                f"  → Continue with: python main.py run --mode generate "
                f"--module {last_module}"
            )
        elif last_files:
            file_hint = last_files[0] if len(last_files) == 1 else f"{len(last_files)} files"
            suggested_next = (
                f"Last {mode} run: {timestamp} — analyzed {file_hint}, "
                f"{total_valid} valid test(s).\n"
                f"  → Re-run or continue with a different file."
            )
        else:
            suggested_next = (
                f"Last {mode} run: {timestamp} — {total_valid} valid test(s).\n"
                f"  → Run again to continue from where you left off."
            )

        return {
            "last_files_processed": last_files,
            "last_module": last_module,
            "last_confidence_avg": 0.0,   # placeholder — future: read from output JSON
            "suggested_next": suggested_next,
        }

    def list_sessions(self, limit: int = 10) -> list[dict]:
        """
        List the most recent saved sessions (for display in --stats or debugging).

        Args:
            limit (int): Maximum number of sessions to return. Default: 10.

        Returns:
            list[dict]: List of session summary dicts (most recent first), each with:
                - session_id, mode, timestamp, files_processed, total_valid, output_file
        """
        session_files = sorted(
            SESSIONS_DIR.glob("session_*.json"),
            reverse=True   # Most recent first (lexicographic = time-sorted with our naming)
        )

        sessions = []
        for sf in session_files[:limit]:
            try:
                with open(sf, encoding="utf-8") as f:
                    data = json.load(f)
                sessions.append({
                    "session_id": data.get("session_id", ""),
                    "mode": data.get("mode", ""),
                    "timestamp": data.get("timestamp", ""),
                    "files_processed": data.get("results", {}).get("files_processed", 0),
                    "total_valid": data.get("results", {}).get("total_valid", 0),
                    "output_file": data.get("results", {}).get("output_file", ""),
                })
            except (json.JSONDecodeError, IOError):
                continue

        return sessions

    # ─────────────────────────────────────────────────────────────────────────
    # Private Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _analyze_file(
        self,
        file_path: str,
        module_hint: str | None,
        config: dict,
        claude,  # ClaudeClient instance (typed as Any to avoid import at top-level)
    ) -> list[dict]:
        """
        Run the full analysis pipeline for a single source file.

        Reads the file, checks testability, chunks if large, runs optional
        enhancements (screenshot, deps, git, bugs), calls Claude, and
        returns the raw generated test cases.

        Args:
            file_path (str): Absolute or relative path to the source file.
            module_hint (str | None): EXAMPLE module name for context.
            config (dict): Merged run config (controls which enhancements to enable).
            claude: An initialized ClaudeClient instance.

        Returns:
            list[dict]: Raw test case dicts from Claude. Empty list if file is
                        not found, not testable, or on any error.
        """
        from core.code_reader import (
            chunk_large_file,
            extract_dependencies,
            is_testable,
            read_file,
        )

        if not Path(file_path).exists():
            logger.warning("File not found, skipping: %s", file_path)
            return []

        # Read file
        try:
            file_data = read_file(file_path)
        except (FileNotFoundError, ValueError) as e:
            logger.error("Cannot read file %s: %s", file_path, e)
            return []

        effective_module = module_hint or file_data.get("module_hint")

        # Skip non-testable files (config files, type definitions, etc.)
        if not is_testable(file_data["content"]):
            logger.info("Skipping %s — no testable logic detected", file_data["file_name"])
            return []

        # ── Optional: screenshot ───────────────────────────────────────────
        screenshot_b64 = None
        if config.get("screenshot") and os.getenv("ENABLE_SCREENSHOT", "false").lower() == "true":
            try:
                import asyncio
                from core.screenshot_capture import ScreenshotCapture
                capture = ScreenshotCapture()
                screenshot_b64 = asyncio.run(capture.capture_module(effective_module or "General"))
                logger.info("Screenshot captured for module: %s", effective_module)
            except Exception as e:  # noqa: BLE001
                logger.warning("Screenshot capture failed: %s", e)

        # ── Optional: dependency files ─────────────────────────────────────
        dependencies = None
        if config.get("include_deps"):
            dependencies = extract_dependencies(
                file_content=file_data["content"],
                file_path=file_path,
            )
            if dependencies:
                logger.info(
                    "Found %d dependency file(s) for %s",
                    len(dependencies), file_data["file_name"]
                )

        # ── Optional: git history ──────────────────────────────────────────
        git_history = None
        if config.get("include_git"):
            try:
                from core.git_analyzer import GitAnalyzer
                git_history = GitAnalyzer().get_file_history(file_path)
                risk = git_history.get("risk_level", "low")
                if risk in ("high", "medium"):
                    logger.info(
                        "Git risk: %s (%d bug-fix commits) for %s",
                        risk.upper(), git_history.get("bug_fix_count", 0),
                        file_data["file_name"]
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("Git analysis failed: %s", e)

        # ── Optional: bug patterns ─────────────────────────────────────────
        bug_patterns_text = None
        if config.get("include_bugs") and effective_module:
            try:
                from core.bug_fetcher import BugFetcher
                bug_patterns_text = BugFetcher().get_patterns_for_module(effective_module)
                if bug_patterns_text:
                    logger.info(
                        "Bug patterns fetched for module '%s'", effective_module
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("Bug pattern fetch failed: %s", e)

        # ── Chunk the file and analyze each chunk ──────────────────────────
        chunk_size = config.get("chunk_size", 300)
        chunks = chunk_large_file(
            file_data["content"],
            max_lines=chunk_size,
            file_name=file_data["file_name"],
        )
        total_chunks = len(chunks)
        if total_chunks > 1:
            logger.info(
                "File split into %d chunk(s) for %s",
                total_chunks, file_data["file_name"]
            )

        all_raw = []
        for chunk_dict in chunks:
            try:
                cases = claude.analyze_code(
                    code=chunk_dict["content"],
                    file_path=file_path,
                    module_hint=effective_module,
                    screenshot_base64=screenshot_b64,
                    dependencies=dependencies,
                    bug_patterns=bug_patterns_text,
                )
                all_raw.extend(cases)
            except Exception as e:  # noqa: BLE001
                error_str = str(e)
                if "credit balance is too low" in error_str:
                    logger.error(
                        "Insufficient API credits. "
                        "Add credits at: https://console.anthropic.com/settings/billing"
                    )
                else:
                    logger.error("Error analyzing chunk: %s", e)

        logger.info(
            "Generated %d raw test case(s) from %s",
            len(all_raw), file_data["file_name"]
        )
        return all_raw

    def _save_test_cases(
        self,
        test_cases: list[dict],
        output_path: str | None = None,
    ) -> str:
        """
        Save validated test cases to a JSON file in the output/ directory.

        Args:
            test_cases (list[dict]): Test cases to save.
            output_path (str | None): Custom path, or None to auto-generate timestamp path.

        Returns:
            str: Path to the saved file, or empty string on failure.
        """
        if not test_cases:
            return ""

        if output_path is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            output_path = f"output/test_cases_{timestamp}.json"

        # Replace {{DATE}} placeholder if present
        if "{{DATE}}" in output_path:
            output_path = output_path.replace(
                "{{DATE}}", datetime.now(timezone.utc).strftime("%Y%m%d")
            )

        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(test_cases, f, indent=2, ensure_ascii=False)
            logger.info("Test cases saved to: %s", output_path)
            return output_path
        except IOError as e:
            logger.warning("Could not save test cases: %s", e)
            return ""

    def _fetch_jira_bugs_by_ids(self, bug_ids: list[str]) -> list[dict]:
        """
        Fetch specific Jira issues by their keys using the Jira REST API.

        Used by run_bugs() to get full context on specific bug IDs before
        generating test cases.

        Args:
            bug_ids (list[str]): List of Jira issue keys (e.g., ["EXAMPLE-101", "EXAMPLE-205"]).

        Returns:
            list[dict]: List of bug dicts with key, summary, description, resolution_date.
                        Returns empty list on any error.
        """
        import requests as _req

        jira_base = os.getenv("JIRA_BASE_URL", "").rstrip("/")
        jira_email = os.getenv("JIRA_EMAIL", "")
        jira_token = os.getenv("JIRA_API_TOKEN", "")

        if not jira_base or not jira_email or not jira_token:
            logger.warning("Jira credentials not configured — cannot fetch bug details")
            return [{"key": bid, "summary": bid, "description": "", "resolution_date": ""} for bid in bug_ids]

        bugs = []
        for bug_id in bug_ids:
            url = f"{jira_base}/rest/api/3/issue/{bug_id}"
            try:
                resp = _req.get(
                    url,
                    auth=(jira_email, jira_token),
                    params={"fields": "summary,description,resolutiondate"},
                    timeout=10,
                )
                if resp.status_code == 404:
                    logger.warning("Bug '%s' not found in Jira", bug_id)
                    continue
                resp.raise_for_status()
                data = resp.json()
                fields = data.get("fields", {})
                raw_desc = fields.get("description") or ""
                if isinstance(raw_desc, dict):
                    # ADF — flatten to text
                    raw_desc = " ".join(
                        n.get("text", "") for n in self._flatten_adf(raw_desc)
                    )
                bugs.append({
                    "key": data.get("key", bug_id),
                    "summary": fields.get("summary", ""),
                    "description": str(raw_desc)[:500],
                    "resolution_date": (fields.get("resolutiondate") or "")[:10],
                    "labels": fields.get("labels", []),
                })
            except Exception as e:  # noqa: BLE001
                logger.warning("Could not fetch bug '%s': %s — using ID as placeholder", bug_id, e)
                bugs.append({"key": bug_id, "summary": bug_id, "description": "", "resolution_date": ""})

        return bugs

    def _flatten_adf(self, node: dict) -> list[dict]:
        """
        Recursively flatten an ADF document into a list of text leaf nodes.

        Args:
            node (dict): ADF node to flatten.

        Returns:
            list[dict]: All text leaf nodes found in the document tree.
        """
        results = []
        if isinstance(node, dict):
            if "text" in node:
                results.append(node)
            for child in node.get("content", []):
                results.extend(self._flatten_adf(child))
        return results

    # ─────────────────────────────────────────────────────────────────────────
    # Run Methods — Integration Layer
    # ─────────────────────────────────────────────────────────────────────────

    def run_generate(self, config: dict) -> dict:
        """
        Generate test cases for a specific source file (generate mode).

        Full pipeline:
          1. Read file from config["file"]
          2. Call Claude with all enabled enhancements
          3. Auto-fix and validate against Xray schema
          4. Show quality report
          5. Ask user: "Push N valid cases? (y/n)"
          6. If y and not dry_run: push to Xray
          7. Save session to output/sessions/
          8. Return results dict

        Args:
            config (dict): Merged run config. Expected keys:
                - file (str): Path to the source file (required)
                - module (str, optional): EXAMPLE module name
                - chunk_size (int): Lines per chunk (default 300)
                - dry_run (bool): If True, skip Xray push
                - include_deps, include_git, include_bugs, screenshot (bool flags)
                - output_file (str, optional): Custom output path

        Returns:
            dict: Results with keys: files_processed, total_generated, total_valid,
                  pushed_count, failed_count, duration_seconds, output_file.
                  Also contains "error" key if a fatal error occurred.
        """
        import time
        from core.claude_client import ClaudeClient
        from core.validator import fix_common_issues, generate_quality_report, validate_batch

        start_time = time.time()

        # ── Validate input ─────────────────────────────────────────────────
        file_path = config.get("file")
        if not file_path:
            logger.error("run_generate requires 'file' in config. Use --file PATH")
            return {"error": "No file specified", "total_valid": 0}

        if not Path(file_path).exists():
            logger.error("File not found: %s", file_path)
            return {"error": f"File not found: {file_path}", "total_valid": 0}

        module_hint = config.get("module")
        dry_run = config.get("dry_run", True)

        # ── Initialize Claude ──────────────────────────────────────────────
        try:
            claude = ClaudeClient()
        except (ValueError, FileNotFoundError) as e:
            logger.error("Failed to initialize Claude client: %s", e)
            return {"error": str(e), "total_valid": 0}

        # ── Analyze the file ───────────────────────────────────────────────
        all_raw = self._analyze_file(file_path, module_hint, config, claude)

        if not all_raw:
            logger.warning("No test cases generated for: %s", file_path)
            results = {
                "files_processed": 1,
                "total_generated": 0,
                "total_valid": 0,
                "pushed_count": 0,
                "failed_count": 0,
                "duration_seconds": round(time.time() - start_time, 2),
                "output_file": "",
            }
            self.save_session("generate", config, results)
            return results

        # ── Fix common issues and validate ────────────────────────────────
        fixed = [fix_common_issues(tc) for tc in all_raw]
        validation_result = validate_batch(fixed)
        generate_quality_report(validation_result)

        valid_cases = validation_result["valid"]
        pushed_count = 0
        failed_count = 0
        saved_path = ""

        if valid_cases:
            # Save to JSON output file
            saved_path = self._save_test_cases(valid_cases, config.get("output_file"))

            if dry_run:
                logger.info(
                    "DRY RUN — %d valid test case(s) ready. "
                    "Set dry_run=false to push to Xray.", len(valid_cases)
                )
            else:
                # ── Ask user for confirmation before pushing ───────────────
                try:
                    answer = input(
                        f"\n  Push {len(valid_cases)} valid test case(s) to Xray? (y/n): "
                    ).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = "n"

                if answer == "y":
                    from core.xray_client import XrayClient
                    try:
                        xray = XrayClient()
                        push_result = xray.push_batch(valid_cases)
                        pushed_count = push_result["summary"]["pushed_count"]
                        failed_count = push_result["summary"]["failed_count"]
                        logger.info(
                            "Pushed %d test case(s) to Xray (%d failed)",
                            pushed_count, failed_count
                        )
                        if push_result["pushed"]:
                            print("\n  Jira keys created:")
                            for item in push_result["pushed"]:
                                print(f"    {item['test_case_id']} → {item['jira_key']}")
                    except ValueError as e:
                        logger.error("Cannot connect to Xray: %s", e)
                else:
                    logger.info("Push skipped by user.")

        # ── Save session and return ────────────────────────────────────────
        results = {
            "files_processed": 1,
            "total_generated": len(all_raw),
            "total_valid": len(valid_cases),
            "pushed_count": pushed_count,
            "failed_count": failed_count,
            "duration_seconds": round(time.time() - start_time, 2),
            "output_file": saved_path,
        }
        self.save_session("generate", config, results)
        return results

    def run_smoke(self, config: dict) -> dict:
        """
        Fetch all smoke-tagged tests from Xray and show them to the user.

        Retrieves existing smoke tests from Jira (tagged with label "smoke"),
        displays the list, and asks the user for confirmation. The actual
        test execution will be handled by the Playwright runner in Enhancement 3.

        Args:
            config (dict): Merged run config. Uses:
                - dry_run (bool): If True, returns mock test list.

        Returns:
            dict: Results with keys: test_list, total, executed, message.
        """
        import time
        from core.xray_client import XrayClient

        start_time = time.time()
        logger.info("Fetching smoke tests from Xray...")

        # ── Fetch tests tagged 'smoke' ─────────────────────────────────────
        try:
            xray = XrayClient(dry_run=False)   # read-only fetch: real tests, not mocks
            tests = xray.fetch_tests_by_tag("smoke")
        except ValueError as e:
            logger.warning("Xray not configured: %s — cannot fetch smoke tests", e)
            tests = []
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not fetch smoke tests: %s", e)
            tests = []

        if not tests:
            print("\n  No smoke tests found in Xray.")
            print("  Run 'python main.py run --mode generate' to create test cases first,")
            print("  then label them 'smoke' in Jira.")
            results = {
                "test_list": [],
                "total": 0,
                "executed": 0,
                "duration_seconds": round(time.time() - start_time, 2),
                "message": "No smoke tests found in Xray",
            }
            self.save_session("smoke", config, {
                "files_processed": 0, "total_generated": 0,
                "total_valid": 0, "pushed_count": 0, "failed_count": 0,
                "duration_seconds": results["duration_seconds"], "output_file": "",
            })
            return results

        # ── Display the test list ──────────────────────────────────────────
        print(f"\n  Found {len(tests)} smoke test(s) in Xray:")
        print("-" * 55)
        for i, t in enumerate(tests[:20], 1):
            priority_tag = f"[{t.get('priority', 'Medium')[:1]}]"
            print(f"  {i:>3}. {priority_tag} [{t.get('key', '?')}] {t.get('summary', 'No title')}")
        if len(tests) > 20:
            print(f"       ... and {len(tests) - 20} more tests")
        print("-" * 55)

        # ── Ask user for confirmation ──────────────────────────────────────
        try:
            answer = input(f"\n  Confirm running {len(tests)} smoke test(s)? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer != "y":
            logger.info("Smoke run cancelled by user.")
            results = {
                "test_list": tests, "total": len(tests),
                "executed": 0, "message": "Cancelled by user",
            }
            self.save_session("smoke", config, {
                "files_processed": 0, "total_generated": len(tests),
                "total_valid": len(tests), "pushed_count": 0, "failed_count": 0,
                "duration_seconds": round(time.time() - start_time, 2), "output_file": "",
            })
            return results

        print("\n  ✓ Smoke test list confirmed — executing.")
        results = self._execute_fetched_tests(tests, config, start_time)
        self.save_session("smoke", config, {
            "files_processed": 0, "total_generated": len(tests),
            "total_valid": len(tests), "pushed_count": 0, "failed_count": 0,
            "duration_seconds": results["duration_seconds"], "output_file": "",
        })
        return results

    def _execute_fetched_tests(self, tests: list[dict], config: dict, start_time: float) -> dict:
        """
        Execute Xray tests fetched by label (smoke/regression) in the browser.

        Reads each test's steps from Xray, runs them with the UI engine, prints the
        console summary and writes the HTML report. Results go back to Xray as one
        Test Execution only when the config asks (push_to_xray) AND DRY_RUN is off.

        Args:
            tests (list[dict]): Issues from XrayClient.fetch_tests_by_tag().
            config (dict): Merged run config (headless, push_to_xray, …).
            start_time (float): time.time() when the run started.

        Returns:
            dict: test_list, total, executed, passed, failed, skipped, errors,
                  report, upload, duration_seconds, message. Never raises.
        """
        import asyncio
        import time

        out = {"test_list": tests, "total": len(tests), "executed": 0, "passed": 0,
               "failed": 0, "skipped": 0, "errors": 0, "report": "", "upload": None,
               "message": ""}
        try:
            from core.xray_client import XrayClient, issues_to_cases
            from execution.results_reporter import ResultsReporter
            from services.execution_service import ExecutionService

            try:
                reader = XrayClient(dry_run=False)      # reads only
                steps = reader.fetch_test_steps([t.get("key", "") for t in tests])
            except ValueError as e:                     # no Xray credentials
                logger.warning("Cannot read test steps from Xray: %s", e)
                steps = {}                              # → each case reported as not run
            cases = issues_to_cases(tests, steps)
            res = ExecutionService().execute(
                cases, mode="ui", headless=str(config.get("headless", "true")).lower() != "false")
            batch = res.raw or {}
            out.update(executed=int(batch.get("total", 0)), passed=res.passed,
                       failed=int(batch.get("failed", 0)), skipped=int(batch.get("skipped", 0)),
                       errors=int(batch.get("errors", 0)))
            if res.error:
                out["message"] = f"Execution failed: {res.error}"
                print(f"  ✗ {res.error}")
            reporter = ResultsReporter()
            if batch.get("results"):
                reporter.generate_console_summary(batch)
                out["report"] = reporter.generate_html_report(batch) or ""
            dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
            if config.get("push_to_xray") and not dry_run and batch.get("results"):
                key_map = {c["test_case_id"]: c["jira_key"] for c in cases if c.get("jira_key")}
                out["upload"] = asyncio.run(reporter.report_to_xray(
                    batch, XrayClient(dry_run=False), key_map=key_map))
            out["message"] = out["message"] or (
                f"{out['passed']} passed, {out['failed']} failed, "
                f"{out['skipped'] + out['errors']} not run")
        except Exception as e:  # noqa: BLE001
            logger.error("Executing fetched tests failed: %s", e)
            out["message"] = f"Execution failed: {e}"
        out["duration_seconds"] = round(time.time() - start_time, 2)
        return out

    def run_regression(self, config: dict) -> dict:
        """
        Fetch all regression-tagged tests from Xray and show them to the user.

        Identical to run_smoke() but uses the "regression" label. Intended for
        full suite execution before releases. Playwright execution in Enhancement 3.

        Args:
            config (dict): Merged run config.

        Returns:
            dict: Results with keys: test_list, total, executed, message.
        """
        import time
        from core.xray_client import XrayClient

        start_time = time.time()
        logger.info("Fetching regression tests from Xray...")

        try:
            xray = XrayClient(dry_run=False)   # read-only fetch: real tests, not mocks
            tests = xray.fetch_tests_by_tag("regression")
        except ValueError as e:
            logger.warning("Xray not configured: %s", e)
            tests = []
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not fetch regression tests: %s", e)
            tests = []

        if not tests:
            print("\n  No regression tests found in Xray.")
            print("  Generate test cases first with: python main.py run --mode generate")
            self.save_session("regression", config, {
                "files_processed": 0, "total_generated": 0,
                "total_valid": 0, "pushed_count": 0, "failed_count": 0,
                "duration_seconds": round(time.time() - start_time, 2), "output_file": "",
            })
            return {"test_list": [], "total": 0, "executed": 0, "message": "No regression tests found"}

        print(f"\n  Found {len(tests)} regression test(s) in Xray:")
        print("-" * 55)
        for i, t in enumerate(tests[:30], 1):
            print(f"  {i:>3}. [{t.get('key', '?')}] {t.get('summary', 'No title')}")
        if len(tests) > 30:
            print(f"       ... and {len(tests) - 30} more tests")
        print("-" * 55)

        try:
            answer = input(f"\n  Confirm running {len(tests)} regression test(s)? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer != "y":
            self.save_session("regression", config, {
                "files_processed": 0, "total_generated": len(tests),
                "total_valid": len(tests), "pushed_count": 0, "failed_count": 0,
                "duration_seconds": round(time.time() - start_time, 2), "output_file": "",
            })
            return {"test_list": tests, "total": len(tests), "executed": 0, "message": "Cancelled"}

        print("\n  ✓ Regression test list confirmed — executing.")
        results = self._execute_fetched_tests(tests, config, start_time)
        self.save_session("regression", config, {
            "files_processed": 0, "total_generated": len(tests),
            "total_valid": len(tests), "pushed_count": 0, "failed_count": 0,
            "duration_seconds": results["duration_seconds"], "output_file": "",
        })
        return results

    def run_bugs(self, config: dict) -> dict:
        """
        Generate targeted test cases for specific Jira bug IDs.

        Fetches each bug's details from Jira, extracts failure context,
        then generates test cases specifically designed to prevent those
        bugs from regressing. Validates and offers to push results to Xray.

        Args:
            config (dict): Merged run config. Expected keys:
                - bugs (str): Comma-separated Jira bug IDs (e.g., "EXAMPLE-101,EXAMPLE-205")
                - source_files (list): Files to analyze with bug context
                - dry_run (bool): If True, skip push
                - module (str, optional): EXAMPLE module hint

        Returns:
            dict: Results with files_processed, total_generated, total_valid,
                  pushed_count, failed_count, duration_seconds, output_file.
        """
        import time
        from core.bug_fetcher import BugFetcher
        from core.claude_client import ClaudeClient
        from core.validator import fix_common_issues, generate_quality_report, validate_batch

        start_time = time.time()

        # ── Parse bug IDs ──────────────────────────────────────────────────
        bugs_str = config.get("bugs", "")
        bug_ids = [b.strip() for b in bugs_str.split(",") if b.strip()] if bugs_str else []

        if not bug_ids:
            logger.error("run_bugs requires bug IDs. Use --bugs EXAMPLE-101,EXAMPLE-205")
            return {"error": "No bug IDs provided", "total_valid": 0}

        logger.info("Processing %d bug ID(s): %s", len(bug_ids), ", ".join(bug_ids))

        # ── Fetch bug details from Jira ────────────────────────────────────
        bugs_data = self._fetch_jira_bugs_by_ids(bug_ids)
        if bugs_data:
            logger.info("Fetched details for %d bug(s)", len(bugs_data))
        else:
            logger.warning("Could not fetch bug details — using IDs as context only")
            bugs_data = [{"key": b, "summary": b, "description": "", "resolution_date": ""} for b in bug_ids]

        # ── Extract patterns from bugs using Claude ────────────────────────
        bug_patterns_text = ""
        try:
            fetcher = BugFetcher()
            bug_patterns_text = fetcher.extract_patterns(bugs_data)
            if bug_patterns_text:
                logger.info("Extracted failure patterns from %d bug(s)", len(bugs_data))
        except Exception as e:  # noqa: BLE001
            logger.warning("Pattern extraction failed: %s — proceeding without patterns", e)

        # ── Get source files to analyze ────────────────────────────────────
        source_files = config.get("source_files", [])
        if not source_files:
            logger.error(
                "run_bugs requires source_files in config. "
                "Add the relevant source files to your config."
            )
            return {"error": "No source_files in config", "total_valid": 0}

        # ── Initialize Claude ──────────────────────────────────────────────
        try:
            claude = ClaudeClient()
        except (ValueError, FileNotFoundError) as e:
            logger.error("Failed to initialize Claude client: %s", e)
            return {"error": str(e), "total_valid": 0}

        # ── Enable bug patterns for this run ──────────────────────────────
        bug_config = dict(config)
        bug_config["include_bugs"] = True  # Force bug pattern injection

        # ── Analyze each source file ───────────────────────────────────────
        all_raw = []
        files_processed = 0
        for entry in source_files:
            file_path = entry.get("file", "") if isinstance(entry, dict) else str(entry)
            module_hint = entry.get("module") if isinstance(entry, dict) else config.get("module")

            if not Path(file_path).exists():
                logger.warning("Source file not found: %s", file_path)
                continue

            # Temporarily inject bug patterns into config for _analyze_file
            local_config = dict(bug_config)
            local_config["_bug_patterns_override"] = bug_patterns_text

            raw = self._analyze_file(file_path, module_hint, local_config, claude)
            all_raw.extend(raw)
            files_processed += 1

        if not all_raw:
            logger.warning("No test cases generated for the specified bug IDs")
            self.save_session("bugs", config, {
                "files_processed": files_processed, "total_generated": 0,
                "total_valid": 0, "pushed_count": 0, "failed_count": 0,
                "duration_seconds": round(time.time() - start_time, 2), "output_file": "",
            })
            return {"total_valid": 0, "message": "No test cases generated"}

        # ── Fix, validate, report ──────────────────────────────────────────
        fixed = [fix_common_issues(tc) for tc in all_raw]
        validation_result = validate_batch(fixed)
        generate_quality_report(validation_result)

        valid_cases = validation_result["valid"]
        pushed_count = 0
        failed_count = 0
        saved_path = self._save_test_cases(valid_cases, config.get("output_file"))

        if valid_cases and not config.get("dry_run", True):
            try:
                answer = input(
                    f"\n  Push {len(valid_cases)} bug-targeted test case(s) to Xray? (y/n): "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"

            if answer == "y":
                from core.xray_client import XrayClient
                try:
                    xray = XrayClient()
                    push_result = xray.push_batch(valid_cases)
                    pushed_count = push_result["summary"]["pushed_count"]
                    failed_count = push_result["summary"]["failed_count"]
                except ValueError as e:
                    logger.error("Cannot connect to Xray: %s", e)

        results = {
            "files_processed": files_processed,
            "total_generated": len(all_raw),
            "total_valid": len(valid_cases),
            "pushed_count": pushed_count,
            "failed_count": failed_count,
            "duration_seconds": round(time.time() - start_time, 2),
            "output_file": saved_path,
        }
        self.save_session("bugs", config, results)
        return results

    def run_tickets(self, config: dict) -> dict:
        """
        Fetch test cases linked to specific Jira ticket IDs from Xray.

        Retrieves each ticket's associated test cases from Xray and returns
        the full list for review or execution. This mode is useful for
        verifying that a specific feature ticket has adequate test coverage.

        Args:
            config (dict): Merged run config. Expected keys:
                - tickets (str): Comma-separated Jira ticket IDs (e.g., "EXAMPLE-500,EXAMPLE-501")

        Returns:
            dict: Results with test_list, total, and message.
        """
        import time
        from core.xray_client import XrayClient

        start_time = time.time()

        # ── Parse ticket IDs ───────────────────────────────────────────────
        tickets_str = config.get("tickets", "")
        ticket_ids = [t.strip() for t in tickets_str.split(",") if t.strip()] if tickets_str else []

        if not ticket_ids:
            logger.error("run_tickets requires ticket IDs. Use --tickets EXAMPLE-500,EXAMPLE-501")
            return {"error": "No ticket IDs provided", "total": 0}

        logger.info("Fetching test cases for %d ticket(s): %s", len(ticket_ids), ", ".join(ticket_ids))

        # ── Fetch each ticket's test case from Xray ─────────────────────
        all_tests = []
        try:
            xray = XrayClient()
            for ticket_id in ticket_ids:
                test = xray.fetch_test_by_id(ticket_id)
                if test:
                    all_tests.append(test)
                    logger.debug("Fetched test '%s': %s", ticket_id, test.get("summary", ""))
                else:
                    logger.warning("Ticket '%s' not found in Xray", ticket_id)
        except ValueError as e:
            logger.warning("Xray not configured: %s", e)
        except Exception as e:  # noqa: BLE001
            logger.warning("Error fetching tickets: %s", e)

        if not all_tests:
            print(f"\n  No test cases found for tickets: {', '.join(ticket_ids)}")
            print("  Make sure these tickets exist as Test issues in Jira.")
            self.save_session("tickets", config, {
                "files_processed": 0, "total_generated": 0,
                "total_valid": 0, "pushed_count": 0, "failed_count": 0,
                "duration_seconds": round(time.time() - start_time, 2), "output_file": "",
            })
            return {"test_list": [], "total": 0, "message": "No tests found for specified tickets"}

        # ── Display results ────────────────────────────────────────────────
        print(f"\n  Found {len(all_tests)} test case(s) for specified ticket(s):")
        print("-" * 55)
        for t in all_tests:
            print(f"  [{t.get('key', '?')}] {t.get('summary', 'No title')} — {t.get('status', 'Unknown')}")
        print("-" * 55)
        print("  → Use 'run --mode regression' to confirm and execute these tests")

        results = {
            "test_list": all_tests,
            "total": len(all_tests),
            "executed": 0,
            "duration_seconds": round(time.time() - start_time, 2),
            "message": "Ticket test cases retrieved successfully",
        }
        self.save_session("tickets", config, {
            "files_processed": 0, "total_generated": len(all_tests),
            "total_valid": len(all_tests), "pushed_count": 0, "failed_count": 0,
            "duration_seconds": results["duration_seconds"], "output_file": "",
        })
        return results

    def run_release(self, config: dict) -> dict:
        """
        Run a full release validation: smoke tests first, then regression if smoke passes.

        Release workflow:
          1. Run smoke tests — display list, ask user to confirm they passed
          2. If user confirms smoke pass rate >= 90%: proceed to regression
          3. Otherwise: stop and generate a failure report
          4. Generate and save a release summary report

        The pass/fail determination is manual (user confirms) until Enhancement 3
        implements the Playwright execution engine with automatic result tracking.

        Args:
            config (dict): Merged run config. Expected keys:
                - version (str, optional): Release version for the report
                - dry_run (bool): Passed through to smoke/regression runs

        Returns:
            dict: Release results with smoke_results, regression_results,
                  release_report_path, and overall_status.
        """
        import time

        start_time = time.time()
        version = config.get("version", "unknown")

        print("\n" + "=" * 60)
        print(f"  RELEASE VALIDATION — Version {version}")
        print("=" * 60)

        # ── Step 1: Run smoke tests ────────────────────────────────────────
        print("\n  STEP 1 OF 2: Smoke Tests")
        print("-" * 60)
        smoke_results = self.run_smoke(config)
        smoke_total = smoke_results.get("total", 0)

        if smoke_total == 0:
            print("\n  ✗ No smoke tests available. Cannot validate release.")
            print("  → Generate smoke tests first: python main.py run --mode generate")
            return {
                "overall_status": "BLOCKED",
                "smoke_results": smoke_results,
                "regression_results": None,
                "message": "No smoke tests found — release validation blocked",
            }

        # Ask user for smoke pass rate
        print(f"\n  {smoke_total} smoke test(s) reviewed.")
        try:
            pass_count_str = input(
                f"  How many smoke tests PASSED? (Enter number, max {smoke_total}): "
            ).strip()
            passed = int(pass_count_str) if pass_count_str.isdigit() else 0
        except (EOFError, KeyboardInterrupt, ValueError):
            passed = 0

        pass_rate = (passed / smoke_total * 100) if smoke_total > 0 else 0
        print(f"\n  Smoke pass rate: {passed}/{smoke_total} = {pass_rate:.1f}%")

        # ── Step 2: Gate on 90% pass rate ─────────────────────────────────
        if pass_rate < 90:
            print(f"\n  ✗ Smoke pass rate {pass_rate:.1f}% is below 90% threshold.")
            print("  → Regression skipped. Fix failing smoke tests before releasing.")
            overall_status = "FAIL"
            regression_results = None
        else:
            print(f"\n  ✓ Smoke pass rate {pass_rate:.1f}% meets threshold (≥90%).")
            print("\n  STEP 2 OF 2: Regression Tests")
            print("-" * 60)
            regression_results = self.run_regression(config)
            overall_status = "PASS"

        # ── Step 3: Generate release report ───────────────────────────────
        report_path = self._generate_release_report(
            version=version,
            smoke_results=smoke_results,
            smoke_pass_rate=pass_rate,
            regression_results=regression_results,
            overall_status=overall_status,
        )

        total_duration = round(time.time() - start_time, 2)
        print("\n" + "=" * 60)
        print(f"  Release Validation Complete — Status: {overall_status}")
        print(f"  Duration: {total_duration:.1f}s")
        if report_path:
            print(f"  Report saved: {report_path}")
        print("=" * 60)

        self.save_session("release", config, {
            "files_processed": 0,
            "total_generated": smoke_total + (regression_results or {}).get("total", 0),
            "total_valid": passed,
            "pushed_count": 0,
            "failed_count": smoke_total - passed,
            "duration_seconds": total_duration,
            "output_file": report_path,
        })

        return {
            "overall_status": overall_status,
            "smoke_results": smoke_results,
            "smoke_pass_rate": pass_rate,
            "regression_results": regression_results,
            "release_report_path": report_path,
            "duration_seconds": total_duration,
        }

    def _generate_release_report(
        self,
        version: str,
        smoke_results: dict,
        smoke_pass_rate: float,
        regression_results: dict | None,
        overall_status: str,
    ) -> str:
        """
        Generate and save a release validation report as a JSON file.

        Args:
            version (str): Release version string.
            smoke_results (dict): Results from run_smoke().
            smoke_pass_rate (float): Percentage of smoke tests that passed.
            regression_results (dict | None): Results from run_regression(), or None.
            overall_status (str): "PASS" or "FAIL" or "BLOCKED".

        Returns:
            str: Path to the saved report file, or empty string on failure.
        """
        from datetime import datetime, timezone

        report = {
            "report_type": "release_validation",
            "version": version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "overall_status": overall_status,
            "smoke": {
                "total_tests": smoke_results.get("total", 0),
                "pass_rate_percent": round(smoke_pass_rate, 1),
                "threshold_percent": 90,
                "passed_threshold": smoke_pass_rate >= 90,
            },
            "regression": {
                "total_tests": (regression_results or {}).get("total", 0),
                "executed": (regression_results or {}).get("executed", 0),
                "message": (regression_results or {}).get("message", "Not run"),
            },
            "recommendation": (
                f"Version {version} is READY for release."
                if overall_status == "PASS"
                else f"Version {version} is NOT ready — fix failing tests first."
            ),
        }

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_version = version.replace(".", "_")
        report_path = f"output/release_report_{safe_version}_{timestamp}.json"

        try:
            Path(report_path).parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            return report_path
        except IOError as e:
            logger.warning("Could not save release report: %s", e)
            return ""
