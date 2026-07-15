"""
core/pipeline.py — QAX Pipeline Orchestrator
=============================================
Orchestrates the full Generate → Export → Execute workflow in one call.

Design principles:
  - Each phase is independent: failure in export never blocks execution.
  - Input is sourced via swappable input adapters (file, folder, jira, pr, story).
  - Generation uses the existing SmartChecker + ClaudeClient + Validator stack.
  - Export is handled by swappable output adapters (Xray, Zephyr, Excel, none).
  - Execution supports three modes: ui (Playwright), api (HTTP), both, or none.
  - No phase raises — all errors are captured and returned in the result dict.

Usage (generate + export to Excel):
    pipeline = QAXPipeline(export="excel", project="myapp", dry_run=True)
    result   = pipeline.run(file_path="src/Controller.cs", module="Auth")

Usage (generate via Jira ticket + API execution):
    pipeline = QAXPipeline(input_type="jira", exec_mode="api", export="none")
    result   = pipeline.run(source="PROJ-123")

Usage (generate + execute, skip export):
    pipeline = QAXPipeline(export="none", exec_mode="ui", project="myapp")
    result   = pipeline.run(source="src/Controller.cs")

Usage (provide pre-generated test cases, execute only):
    pipeline = QAXPipeline(export="xray", exec_mode="ui")
    result   = pipeline.run(test_cases=already_generated_list)
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.adapters.base import BaseExportAdapter

logger = logging.getLogger(__name__)

# ── Adapter registry: CLI name → dotted import path ──────────────────────────
_ADAPTER_PATHS: dict[str, str] = {
    "xray":   "core.adapters.xray_adapter.XrayAdapter",
    "zephyr": "core.adapters.zephyr_adapter.ZephyrAdapter",
    "excel":  "core.adapters.excel_adapter.ExcelAdapter",
    "none":   "core.adapters.memory_adapter.MemoryAdapter",
}


class QAXPipeline:
    """
    End-to-end QA pipeline: Generate → Export → Execute.

    Each phase is optional and independently fault-tolerant.
    The result dict always contains keys for every phase, populated
    with None if the phase was skipped.

    Attributes:
        export_name (str):  Output adapter key — "xray", "zephyr", "excel", "none".
        input_type (str):   Input adapter key — "file", "folder", "jira", "pr", "story".
        exec_mode (str):    Execution mode — "ui", "api", "both", "none".
        execute (bool):     Convenience bool — True when exec_mode != "none".
        project (str):      Project name passed to adapters and core modules.
        dry_run (bool):     When True, no external writes or API calls are made.
        headless (bool):    When True, Playwright runs without a visible browser.
        adapter:            Instantiated BaseExportAdapter for the export phase.
        input_adapter:      Instantiated BaseInputAdapter for the input phase.
    """

    def __init__(
        self,
        export: str = "none",
        execute: bool = False,
        project: str | None = None,
        dry_run: bool = True,
        headless: bool = True,
        input_type: str = "file",
        exec_mode: str | None = None,
    ) -> None:
        """
        Initialise the pipeline and load the requested adapters.

        Args:
            export (str): Output adapter — "xray", "zephyr", "excel", or "none".
                          Unknown names fall back silently to "none".
            execute (bool): Legacy flag — if True and exec_mode is None,
                            exec_mode defaults to "ui". Ignored when exec_mode
                            is supplied explicitly.
            project (str | None): Project name. Falls back to DEFAULT_PROJECT env.
            dry_run (bool): Simulate all external operations when True.
            headless (bool): Run Playwright browser headless when True.
            input_type (str): Input adapter — "file", "folder", "jira", "pr",
                              or "story". Default: "file".
            exec_mode (str | None): Execution engine — "ui", "api", "both",
                                    or "none". Overrides the legacy execute flag.
        """
        import os
        self.export_name  = (export or "none").lower()
        self.input_type   = (input_type or "file").lower()
        self.project      = project or os.getenv("DEFAULT_PROJECT", "")
        self.dry_run      = dry_run
        self.headless     = headless

        # Resolve exec_mode: explicit flag wins; legacy execute=True → "ui"
        if exec_mode is not None:
            self.exec_mode = exec_mode.lower()
        else:
            self.exec_mode = "ui" if execute else "none"

        self.execute = self.exec_mode != "none"

        # Load output export adapter
        self.adapter: BaseExportAdapter = self._load_adapter(self.export_name)

        # Load input adapter
        self.input_adapter = self._load_input(self.input_type)

        logger.info(
            "QAX Pipeline initialised — input=%s export=%s exec_mode=%s "
            "project=%s dry_run=%s",
            self.input_type, self.export_name, self.exec_mode,
            self.project, self.dry_run,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(
        self,
        file_path: str | None = None,
        test_cases: list[dict] | None = None,
        source: str | None = None,
        **analyze_kwargs,
    ) -> dict:
        """
        Execute all pipeline phases and return a consolidated result dict.

        Input resolution order:
          1. ``test_cases`` provided → skip generation entirely.
          2. ``source`` provided → use the configured input adapter to fetch.
          3. ``file_path`` provided → legacy file-path mode (backward compatible).

        Args:
            file_path (str | None): Legacy: path to source file for generation.
                                     Still honoured for backward compatibility.
            test_cases (list[dict] | None): Pre-generated test cases.
                                            When supplied, generation is skipped.
            source (str | None): Generic source for the input adapter —
                                  file path, folder path, Jira key, PR number,
                                  or story text depending on input_type.
            **analyze_kwargs: Extra keyword args forwarded to ``_run_generation``:
                - module (str | None)
                - user_story (str | None)
                - include_git (bool)
                - include_deps (bool)
                - include_bugs (bool)
                - test_type (str | None)
                - ext (str | None)      — for folder adapter

        Returns:
            dict: {
                "generation": {"count": int, "success": bool} | None,
                "export":     standard result dict | None,
                "execution":  execution result dict | None,
                "success":    bool,
            }
        """
        result: dict = {
            "generation": None,
            "export":     None,
            "execution":  None,
            "success":    False,
        }

        # ── Phase 1: Generation ───────────────────────────────────────────────
        if test_cases is None:
            # Resolve the effective source string
            effective_source = source or file_path
            if not effective_source:
                result["generation"] = {
                    "count": 0, "success": False,
                    "error": "No source, file_path, or test_cases provided",
                }
                logger.error("QAX Pipeline: no source provided — aborting")
                return result

            print("\n  ━━━ Phase 1: Generation ━━━")

            # Fetch input items via the input adapter
            input_items = self.input_adapter.fetch(
                effective_source,
                ext=analyze_kwargs.get("ext"),
                module=analyze_kwargs.get("module"),
            )

            if not input_items:
                print("  [QAX] Input adapter returned 0 items — stopping pipeline")
                logger.warning(
                    "QAX Pipeline: input adapter '%s' returned 0 items for '%s'",
                    self.input_type, effective_source,
                )
                result["generation"] = {"count": 0, "success": False}
                return result

            # Generate test cases from each input item
            all_test_cases: list[dict] = []
            for item in input_items:
                item_cases = self._run_generation_from_item(item, **analyze_kwargs)
                all_test_cases.extend(item_cases)

            test_cases = all_test_cases
            result["generation"] = {
                "count":   len(test_cases),
                "success": len(test_cases) > 0,
            }

            if not test_cases:
                print("  [QAX] Generation produced 0 test cases — stopping pipeline")
                logger.warning("QAX Pipeline: generation produced 0 test cases")
                return result
        else:
            print(
                f"\n  ━━━ Phase 1: Skipped "
                f"({len(test_cases)} pre-generated case(s) provided) ━━━"
            )
            result["generation"] = {"count": len(test_cases), "success": True}

        # ── Phase 2: Export ───────────────────────────────────────────────────
        print(f"\n  ━━━ Phase 2: Export [{self.export_name}] ━━━")
        export_result = self.adapter.export(
            test_cases,
            project=self.project,
            dry_run=self.dry_run,
        )
        result["export"] = export_result

        if not export_result["success"]:
            logger.warning(
                "QAX Pipeline: export [%s] failed — %s — continuing to execution",
                self.export_name, export_result.get("error"),
            )
            print(f"  [QAX] Export warning: {export_result.get('error')}")
            print("  Continuing to execution phase regardless…")

        # ── Phase 3: Execution ────────────────────────────────────────────────
        if self.exec_mode != "none":
            print(f"\n  ━━━ Phase 3: Execution [{self.exec_mode}] ━━━")
            exec_result = self._run_execution(test_cases)
            result["execution"] = exec_result
        else:
            print("\n  ━━━ Phase 3: Skipped (--exec-mode none) ━━━")

        result["success"] = True
        return result

    # ── Private: Input adapter loading ────────────────────────────────────────

    def _load_input(self, name: str):
        """
        Import and instantiate the input adapter for the given name.

        Falls back to FileInputAdapter for unknown names.

        Args:
            name (str): Input adapter key — "file", "folder", "jira", "pr", "story".

        Returns:
            BaseInputAdapter: Ready-to-use input adapter instance.
        """
        try:
            from core.input_adapters import get_input_adapter
            return get_input_adapter(name)
        except Exception as exc:
            logger.error(
                "QAX Pipeline: could not load input adapter '%s' — %s — using 'file'",
                name, exc,
            )
            from core.input_adapters.file_adapter import FileInputAdapter
            return FileInputAdapter()

    # ── Private: Output adapter loading ───────────────────────────────────────

    def _load_adapter(self, name: str) -> "BaseExportAdapter":
        """
        Import and instantiate the adapter class for the given name.

        Falls back to MemoryAdapter for unknown names so the pipeline
        never crashes on a typo in --export.

        Args:
            name (str): Adapter key from _ADAPTER_PATHS.

        Returns:
            BaseExportAdapter: Ready-to-use adapter instance.
        """
        dotted_path = _ADAPTER_PATHS.get(name, _ADAPTER_PATHS["none"])
        if name not in _ADAPTER_PATHS:
            logger.warning(
                "QAX Pipeline: unknown adapter '%s' — falling back to 'none'", name
            )
        try:
            module_path, class_name = dotted_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            cls    = getattr(module, class_name)
            return cls()
        except Exception as exc:
            logger.error(
                "QAX Pipeline: could not load adapter '%s' — %s — using MemoryAdapter",
                name, exc,
            )
            from core.adapters.memory_adapter import MemoryAdapter
            return MemoryAdapter()

    # ── Private: Generation phase ──────────────────────────────────────────────

    def _run_generation(self, file_path: str, **kwargs) -> list[dict]:
        """
        Run the full generation stack for a single source file or virtual source.

        Flow:
          1. If ``content`` kwarg is provided, use it directly (story/jira mode).
          2. Otherwise call CodeReader.read_file() to load source from disk.
          3. SmartChecker.run_pre_checks() — cache hit → return early.
          4. ClaudeClient.analyze_code()   — call Claude API.
          5. Validator.validate_batch()    — filter invalid cases.

        Args:
            file_path (str): Absolute or relative path to the source file,
                             OR a virtual URI (e.g. "story://input", "jira://KEY")
                             when ``content`` is provided directly.
            **kwargs: Forwarded keyword args:
                content (str | None)    — pre-loaded content; skips read_file().
                module (str | None)     — module hint.
                user_story (str | None) — story/requirement text for Claude.
                include_git (bool)
                include_deps (bool)
                include_bugs (bool)
                test_type (str | None)

        Returns:
            list[dict]: Valid test case dicts, or [] on any error.
        """
        module      = kwargs.get("module")
        user_story  = kwargs.get("user_story")
        test_type   = kwargs.get("test_type")
        pre_content = kwargs.get("content")   # pre-loaded content from input adapter

        try:
            from core.smart_checker import SmartChecker
            from core.claude_client import ClaudeClient
            from core.validator     import validate_batch

            # ── Load content ───────────────────────────────────────────────────
            checker = SmartChecker(project=self.project)

            if pre_content is not None:
                # Story/Jira/virtual source — content already loaded by input adapter
                content   = pre_content
                file_data = {
                    "content":     content,
                    "module_hint": module,
                    "size_lines":  len(content.splitlines()),
                }
            else:
                # File/folder/PR — load from disk
                from core.code_reader import read_file
                file_data = read_file(file_path)
                content   = file_data.get("content", "")

            # Build optional git history for risk assessment
            git_history: dict | None = None
            if kwargs.get("include_git"):
                try:
                    from core.git_analyzer import GitAnalyzer
                    git_history = GitAnalyzer().get_file_history(file_path)
                except Exception as exc:
                    logger.debug("QAX Pipeline: git history unavailable — %s", exc)

            pre = checker.run_pre_checks(
                file_path=file_path,
                file_content=content,
                module=module,
                git_history=git_history,
                user_story=user_story,
            )

            # ── Cache hit: return immediately ─────────────────────────────────
            if pre.get("cache_hit"):
                cached = pre.get("cached_result") or []
                logger.info(
                    "QAX Pipeline: cache hit for %s (%d case(s))",
                    file_path, len(cached),
                )
                print(f"  [QAX] Cache hit — returning {len(cached)} cached test case(s)")
                return cached

            # ── Build bug patterns if requested ───────────────────────────────
            bug_patterns: str | None = None
            if kwargs.get("include_bugs") and module:
                try:
                    from core.bug_fetcher import BugFetcher
                    bug_patterns = BugFetcher().get_patterns_for_module(module)
                except Exception as exc:
                    logger.debug("QAX Pipeline: bug patterns unavailable — %s", exc)

            # ── Call Claude ───────────────────────────────────────────────────
            client = ClaudeClient(project=self.project)
            raw    = client.analyze_code(
                code=content,
                file_path=file_path,
                module_hint=module or file_data.get("module_hint"),
                bug_patterns=bug_patterns,
                user_story=user_story,
                risk_assessment=pre.get("risk"),
                test_type=test_type or pre.get("test_type"),
                skill_context=pre.get("skill_context"),
                risk_level=pre.get("risk", {}).get("risk_level", "low"),
                line_count=file_data.get("size_lines", 0),
            )

            # ── Validate ──────────────────────────────────────────────────────
            batch = validate_batch(raw)
            valid = batch.get("valid", [])
            summ  = batch.get("summary", {})
            logger.info(
                "QAX Pipeline: generation complete — %d valid, %d invalid",
                len(valid), len(batch.get("invalid", [])),
            )
            print(f"  Generated: {summ.get('total', len(raw))} | "
                  f"Valid: {len(valid)} | "
                  f"Avg confidence: {summ.get('avg_confidence', 0):.1f}/10")
            return valid

        except FileNotFoundError as exc:
            logger.error("QAX Pipeline: file not found — %s", exc)
            print(f"  [QAX] ERROR: {exc}")
            return []
        except Exception as exc:
            logger.error("QAX Pipeline: generation error — %s", exc)
            print(f"  [QAX] Generation error: {exc}")
            return []

    def _run_generation_from_item(self, item: dict, **kwargs) -> list[dict]:
        """
        Run the generation phase for a single input item fetched by an input adapter.

        This is the normalised entry point used by ``run()`` when processing items
        returned by any input adapter. It maps the item's content and metadata into
        the arguments expected by the generation stack and delegates to
        ``_run_generation()``.

        For story and Jira inputs (no real file path), the content is passed as
        ``user_story`` so Claude receives it as contextual requirement text.

        Args:
            item (dict): Normalised input dict from a BaseInputAdapter.fetch() call.
                         Must have "file_path", "content", "source_type", "metadata".
            **kwargs: Forwarded analyze kwargs (module, include_git, etc.).

        Returns:
            list[dict]: Valid test case dicts, or [] on any error.
        """
        source_type = item.get("source_type", "file")
        file_path   = item.get("file_path", "")
        content     = item.get("content", "")
        metadata    = item.get("metadata", {})

        # For story/Jira inputs: pass content directly so _run_generation skips read_file()
        if source_type in ("story", "jira"):
            story_text = kwargs.get("user_story") or content
            return self._run_generation(
                file_path=file_path,
                content=content,          # pre-loaded — bypass read_file()
                user_story=story_text,
                module=kwargs.get("module") or metadata.get("issue_type"),
                include_git=False,        # no git history for virtual sources
                include_deps=kwargs.get("include_deps", False),
                include_bugs=kwargs.get("include_bugs", False),
                test_type=kwargs.get("test_type"),
            )

        # For file/folder/pr/git_diff: use the real file path (disk read)
        return self._run_generation(file_path=file_path, **kwargs)

    # ── Private: Execution phase ───────────────────────────────────────────────

    def _run_execution(self, test_cases: list[dict]) -> dict:
        """
        Run execution against all test cases using the configured exec_mode.

        Modes:
          "ui"   — Playwright browser automation (existing stack).
          "api"  — HTTP request runner (APIRunner).
          "both" — API first, then UI; returns combined dict with "api" and "ui" keys.
          "none" — Caller should not reach this; returns empty summary.

        Args:
            test_cases (list[dict]): Test cases to execute.

        Returns:
            dict: Execution summary. Shape differs slightly for "both" mode
                  (has "api" and "ui" sub-dicts) vs single-mode runs.
        """
        mode = self.exec_mode

        if mode == "api":
            return self._run_api_execution(test_cases)

        if mode == "both":
            logger.info("QAX Pipeline: running BOTH api and ui execution")
            api_result = self._run_api_execution(test_cases)
            ui_result  = self._run_ui_execution(test_cases)
            return {
                "api":    api_result,
                "ui":     ui_result,
                "total":  len(test_cases),
                "passed": api_result.get("passed", 0) + ui_result.get("passed", 0),
                "failed": api_result.get("failed", 0) + ui_result.get("failed", 0),
                "mode":   "both",
            }

        # Default: "ui" (Playwright)
        return self._run_ui_execution(test_cases)

    def _run_api_execution(self, test_cases: list[dict]) -> dict:
        """
        Execute test cases using the HTTP-based APIRunner.

        Args:
            test_cases (list[dict]): Test cases to execute.

        Returns:
            dict: Standard batch result from APIRunner.execute_batch().
        """
        try:
            from execution.api_runner import APIRunner
            runner = APIRunner()
            runner.setup()
            result = runner.execute_batch(test_cases, stop_on_fail=False)
            runner.teardown()
            return result
        except Exception as exc:
            logger.error("QAX Pipeline: API execution error — %s", exc)
            return {
                "error":   str(exc),
                "total":   len(test_cases),
                "passed":  0,
                "failed":  len(test_cases),
                "results": [],
                "mode":    "api",
            }

    def _run_ui_execution(self, test_cases: list[dict]) -> dict:
        """
        Execute test cases using the Playwright browser (existing UI runner).

        Tears down the browser on completion or error.

        Args:
            test_cases (list[dict]): Test cases to execute.

        Returns:
            dict: Execution summary from TestExecutor.execute_batch().
        """
        import asyncio

        async def _exec() -> dict:
            from execution.playwright_runner import PlaywrightRunner
            from execution.test_executor     import TestExecutor

            runner   = PlaywrightRunner()
            executor = TestExecutor()

            try:
                await runner.setup(headless=self.headless)
                results = await executor.execute_batch(
                    test_cases, runner, stop_on_fail=False
                )
                return results
            finally:
                try:
                    await runner.teardown()
                except Exception:
                    pass

        try:
            return asyncio.run(_exec())
        except Exception as exc:
            logger.error("QAX Pipeline: UI execution error — %s", exc)
            return {
                "error":   str(exc),
                "total":   len(test_cases),
                "passed":  0,
                "failed":  len(test_cases),
                "results": [],
                "mode":    "ui",
            }
