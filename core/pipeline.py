"""
core/pipeline.py — QAX Pipeline Orchestrator
=============================================
Orchestrates the full functional-QA workflow in one call:
Input → Generate → Plan → Export (test definitions) → Execute → Report → Results to Xray.

Design principles:
  - Each phase is independent: failure in export never blocks execution.
  - Input is sourced via swappable input adapters (file, folder, jira, pr, story).
  - Generation uses the existing SmartChecker + ClaudeClient + Validator stack.
  - Export is handled by swappable output adapters (Xray, Zephyr, Excel, none).
  - Execution supports three modes: ui (Playwright), api (HTTP), both, or none.
  - No phase raises — all errors are captured and returned in the result dict.
  - Generated cases are AI proposals until executed; a case that did not run is
    never counted as passed, and `success` is False when any phase errored.
  - Nothing is written outside the machine unless dry_run=False (an explicit opt-in).

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
    "csv":    "core.adapters.csv_adapter.CSVAdapter",
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
        base_url: str | None = None,
        output_dir: str | None = None,
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
            base_url (str | None): The running app to execute against. Default:
                                   APP_BASE_URL from the environment.
            output_dir (str | None): Where cases, results and the HTML report are
                                     written. Default: output/pipeline/<timestamp>.
        """
        import os
        self.export_name  = (export or "none").lower()
        self.input_type   = (input_type or "file").lower()
        self.project      = project or os.getenv("DEFAULT_PROJECT", "")
        self.dry_run      = dry_run
        self.headless     = headless
        self.base_url     = (base_url or os.getenv("APP_BASE_URL", "")).rstrip("/")
        self.output_dir   = output_dir

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
        plan_only: bool = False,
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
            plan_only (bool): Stop after the plan (no export, execution or report).
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
            "plan":       None,
            "export":     None,
            "execution":  None,
            "report":     None,
            "results_upload": None,
            "success":    False,
            "errors":     [],
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

        # ── Plan: what will run, against what, and what may be written ────────
        plan = self.plan(test_cases)
        result["plan"] = plan
        self._print_plan(plan)
        if plan_only:
            result["success"] = bool(result["generation"].get("success"))
            return result

        # ── Phase 2: Export (test definitions) ────────────────────────────────
        print(f"\n  ━━━ Phase 2: Export [{self.export_name}] ━━━")
        export_result = self.adapter.export(
            test_cases,
            project=self.project,
            dry_run=self.dry_run,
        )
        result["export"] = export_result
        key_map = dict(export_result.get("key_map") or {})
        for tc in test_cases:
            key = key_map.get(tc.get("test_case_id", ""))
            if key:
                tc["jira_key"] = key

        if not export_result["success"] and self.export_name != "none":
            result["errors"].append(f"export: {export_result.get('error')}")
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
            for b in self._batches(exec_result):
                if b.get("error"):
                    result["errors"].append(f"execution ({b.get('mode', '?')}): {b['error']}")
        else:
            print("\n  ━━━ Phase 3: Skipped (--exec-mode none) ━━━")

        # ── Phase 4: Report (cases, results, HTML) ────────────────────────────
        result["report"] = self._write_report(test_cases, result.get("execution"))

        # ── Phase 5: Results to Xray (only when the cases went to Xray) ──────
        if self.export_name == "xray" and result.get("execution"):
            print("\n  ━━━ Phase 5: Results → Xray ━━━")
            uploads = self._upload_results(result["execution"], key_map)
            result["results_upload"] = uploads
            for u in uploads:
                if u.get("failed") or (u.get("error") and not u.get("unmapped")):
                    result["errors"].append(f"results upload: {u.get('error')}")

        result["success"] = bool(result["generation"] and result["generation"].get("success")) \
            and not result["errors"]
        return result

    # ── Plan ──────────────────────────────────────────────────────────────────

    def plan(self, test_cases: list[dict]) -> dict:
        """
        Describe what the run will do before anything executes or is written.

        Args:
            test_cases (list[dict]): The (generated or supplied) cases.

        Returns:
            dict: cases, executable counts per engine (a case with no api_steps
                  cannot run over HTTP; one with no steps cannot run at all),
                  target base URL, export target, whether writes are live, and
                  missing prerequisites. Never raises.
        """
        import os

        cases = list(test_cases or [])
        api_ready = sum(1 for tc in cases if tc.get("api_steps"))
        ui_ready = sum(1 for tc in cases if tc.get("automation_steps")
                       or tc.get("manual_steps") or tc.get("steps"))
        missing: list[str] = []
        if self.exec_mode != "none" and not self.base_url:
            missing.append("no target URL (--base-url or APP_BASE_URL)")
        if self.exec_mode in ("api", "both") and cases and not api_ready:
            missing.append("no case has api_steps — API execution will skip them all")
        if self.exec_mode in ("ui", "both"):
            try:
                from execution.playwright_runner import PLAYWRIGHT_AVAILABLE
            except Exception:  # noqa: BLE001
                PLAYWRIGHT_AVAILABLE = False
            if not PLAYWRIGHT_AVAILABLE:
                missing.append("Playwright not installed (pip install playwright && "
                               "playwright install chromium)")
        live = not self.dry_run
        writes: list[str] = []
        if self.export_name in ("xray", "zephyr"):
            writes.append(f"create {len(cases)} test issue(s) in {self.export_name}"
                          + ("" if live else " (dry run — nothing sent)"))
        if self.export_name == "xray" and self.exec_mode != "none":
            writes.append("import results as one Xray Test Execution"
                          + ("" if live else " (dry run — nothing sent)"))
        if self.exec_mode in ("ui", "both"):
            writes.append("auto-file a Jira bug per UI failure when AUTO_BUG_REPORT=true"
                          + ("" if live else " (dry run — drafted only)"))
            if os.getenv("QAX_AI_HEAL", "false").strip().lower() == "true":
                writes.append("AI healing ON: page HTML / screenshots of failing steps are "
                              "sent to the AI provider")
        return {
            "cases": len(cases), "api_executable": api_ready, "ui_executable": ui_ready,
            "exec_mode": self.exec_mode, "base_url": self.base_url or None,
            "export": self.export_name, "live_writes": live, "writes": writes,
            "missing": missing,
        }

    @staticmethod
    def _print_plan(plan: dict) -> None:
        """Print the plan block (see plan())."""
        print("\n  ━━━ Plan ━━━")
        print(f"  Cases:      {plan['cases']} (AI-generated proposals until executed)")
        print(f"  Execute:    {plan['exec_mode']} → {plan['base_url'] or '(no target URL)'}")
        print(f"  Runnable:   api {plan['api_executable']} / ui {plan['ui_executable']}")
        print(f"  Export:     {plan['export']}  |  live writes: {plan['live_writes']}")
        for w in plan["writes"]:
            print(f"  Writes:     {w}")
        for m in plan["missing"]:
            print(f"  MISSING:    {m}")

    # ── Report / results upload ───────────────────────────────────────────────

    @staticmethod
    def _batches(execution: dict | None) -> list[dict]:
        """The single-engine batch dicts inside an execution result ("both" has two)."""
        if not execution:
            return []
        if "api" in execution and "ui" in execution:
            return [dict(execution["api"], mode="api"), dict(execution["ui"], mode="ui")]
        return [execution]

    def _write_report(self, test_cases: list[dict], execution: dict | None) -> dict:
        """
        Save the cases, the raw results and an HTML report for every executed engine.

        Returns:
            dict: {"dir", "cases", "results", "html": [paths], "error"}. Never raises.
        """
        import json
        from datetime import datetime
        from pathlib import Path

        out = {"dir": None, "cases": None, "results": None, "html": [], "error": None}
        try:
            folder = Path(self.output_dir or Path("output") / "pipeline" /
                          datetime.now().strftime("%Y%m%d_%H%M%S"))
            folder.mkdir(parents=True, exist_ok=True)
            out["dir"] = str(folder)
            cases_path = folder / "test_cases.json"
            cases_path.write_text(json.dumps({"test_cases": test_cases}, indent=2,
                                             default=str), encoding="utf-8")
            out["cases"] = str(cases_path)
            if execution:
                results_path = folder / "results.json"
                results_path.write_text(json.dumps(execution, indent=2, default=str),
                                        encoding="utf-8")
                out["results"] = str(results_path)
                from execution.results_reporter import ResultsReporter
                reporter = ResultsReporter(dry_run=self.dry_run)
                for b in self._batches(execution):
                    if b.get("results"):
                        path = reporter.generate_html_report(b, output_folder=str(folder))
                        if path:
                            out["html"].append(path)
            print(f"\n  Report:     {out['dir']}")
        except Exception as exc:  # noqa: BLE001
            out["error"] = str(exc)[:200]
            logger.error("QAX Pipeline: report failed — %s", exc)
        return out

    def _upload_results(self, execution: dict, key_map: dict) -> list[dict]:
        """
        Import execution results into Xray (one Test Execution per engine).

        Uses the same dry_run as the export: nothing is sent unless the operator
        opted in. Results whose case has no real Jira key are counted, not sent.

        Returns:
            list[dict]: One ResultsReporter.report_to_xray summary per engine.
        """
        import asyncio

        from core.xray_client import XrayClient
        from execution.results_reporter import ResultsReporter

        uploads = []
        reporter = ResultsReporter(dry_run=self.dry_run)
        try:
            client = XrayClient(dry_run=self.dry_run)
        except Exception as exc:  # noqa: BLE001 — e.g. live mode without Xray credentials
            return [{"mode": b.get("mode", self.exec_mode), "failed": len(b.get("results") or []),
                     "uploaded": 0, "error": str(exc).splitlines()[0][:200]}
                    for b in self._batches(execution)]
        for b in self._batches(execution):
            try:
                u = asyncio.run(reporter.report_to_xray(
                    b, client, key_map=key_map,
                    summary_text=f"QAX {b.get('mode', self.exec_mode)} execution"))
            except Exception as exc:  # noqa: BLE001
                u = {"failed": 1, "error": str(exc)[:200]}
            u["mode"] = b.get("mode", self.exec_mode)
            if self.dry_run and u.get("unmapped") and not u.get("attempted"):
                u["error"] = (f"dry run: the cases were not created in Xray, so their "
                              f"{u['unmapped']} result(s) have no Jira key yet — nothing sent")
            uploads.append(u)
            print(f"  [{u['mode']}] uploaded {u.get('uploaded', 0)}"
                  f"{' (dry run)' if u.get('dry_run') else ''}, "
                  f"no Jira key {u.get('unmapped', 0)}, failed {u.get('failed', 0)}"
                  + (f" — {u['error']}" if u.get("error") else ""))
        return uploads

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
        # ── Phase 1: delegate to the extracted GenerationService ──────────────
        # This method used to inline the SmartChecker → Claude → Validator stack.
        # That exact logic now lives in services/generation_service.py, so the
        # pipeline (and every other face) share one implementation. Behaviour is
        # unchanged; the pipeline keeps its own printed summary below so the CLI
        # output stays the same. A NullProgressReporter is used so no extra lines
        # are printed here — the UI face supplies a live reporter instead.
        from services.context import QAXContext
        from services.exceptions import QAXConfigError
        from services.generation_service import GenerationOptions, GenerationService
        from services.progress import NullProgressReporter

        options = GenerationOptions(
            module=kwargs.get("module"),
            user_story=kwargs.get("user_story"),
            test_type=kwargs.get("test_type"),
            include_git=bool(kwargs.get("include_git")),
            include_deps=bool(kwargs.get("include_deps")),
            include_bugs=bool(kwargs.get("include_bugs")),
            content=kwargs.get("content"),
        )

        context = QAXContext.from_env(project=self.project, dry_run=self.dry_run)

        try:
            service = GenerationService(context)
            result = service.generate(
                file_path, options=options, progress=NullProgressReporter()
            )
        except QAXConfigError as exc:
            logger.error("QAX Pipeline: configuration error — %s", exc)
            print(f"  [QAX] ERROR: {exc}")
            return []

        # ── Outright failure (missing file, API error) ────────────────────────
        if result.error:
            logger.error("QAX Pipeline: generation error — %s", result.error)
            print(f"  [QAX] Generation error: {result.error}")
            return []

        # ── Cache hit ─────────────────────────────────────────────────────────
        if result.from_cache:
            logger.info(
                "QAX Pipeline: cache hit for %s (%d case(s))",
                file_path, result.count,
            )
            print(f"  [QAX] Cache hit — returning {result.count} cached test case(s)")
            return result.test_cases

        # ── Success ───────────────────────────────────────────────────────────
        logger.info(
            "QAX Pipeline: generation complete — %d valid, %d invalid",
            result.count, len(result.invalid),
        )
        print(
            f"  Generated: {result.summary.get('total', result.count)} | "
            f"Valid: {result.count} | "
            f"Avg confidence: {result.avg_confidence:.1f}/10"
        )
        return result.test_cases

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

        # For file/folder: read from disk. For a PR (or any item whose content the
        # adapter already fetched and that is not on local disk) use that content —
        # re-reading the path would find nothing for a PR that is not checked out.
        import os
        if content and (source_type == "pr" or not os.path.isfile(file_path)):
            return self._run_generation(file_path=file_path, content=content, **kwargs)
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
                "skipped": api_result.get("skipped", 0) + ui_result.get("skipped", 0),
                "errors": api_result.get("errors", 0) + ui_result.get("errors", 0),
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
            runner.setup(base_url=self.base_url or None)
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
        import os

        if self.base_url:
            os.environ["APP_BASE_URL"] = self.base_url   # ActionMapper reads it

        async def _exec() -> dict:
            from execution.playwright_runner import PlaywrightRunner
            from execution.test_executor     import TestExecutor

            runner   = PlaywrightRunner()
            executor = TestExecutor(dry_run=self.dry_run)

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
