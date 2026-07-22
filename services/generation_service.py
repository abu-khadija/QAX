"""
services/generation_service.py — GenerationService (Phase 1)
============================================================
The first extracted service. It runs the exact same generation stack the
existing pipeline uses — SmartChecker → ClaudeClient → Validator — but with
three critical differences that make it usable by every face (CLI, UI, MCP,
SaaS):

  * It returns a typed GenerationResult instead of printing.
  * It emits progress through a ProgressReporter instead of print().
  * It takes a QAXContext instead of reading os.environ / load_dotenv itself.

No behaviour change: given the same input it produces the same test cases as
`QAXPipeline._run_generation`. This service is what `main.py analyze`, the
Streamlit UI, an MCP tool, and a SaaS endpoint will all call.

Usage:
    from services import QAXContext, PrintProgressReporter
    from services.generation_service import GenerationService, GenerationOptions

    svc = GenerationService(QAXContext.from_env(project="example"))
    result = svc.generate(
        "src/Controller.cs",
        options=GenerationOptions(module="Orders", include_git=True),
        progress=PrintProgressReporter(),
    )
    print(result.count, "test cases,", result.avg_confidence, "avg confidence")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from services.context import QAXContext
from services.exceptions import QAXGenerationError
from services.progress import NullProgressReporter, ProgressReporter
from services.results import BatchGenerationResult, GenerationResult

logger = logging.getLogger(__name__)


@dataclass
class GenerationOptions:
    """
    Options for a single generate() call.

    Attributes:
        module (str | None): Module hint (e.g. "Orders"). Auto-detected from
            the file path when None.
        user_story (str | None): User story / acceptance criteria text. When
            set, Claude generates tests that verify it.
        test_type (str | None): One of "regression", "feature", "smoke", "uat",
            "api", "e2e". None means let SmartChecker auto-detect.
        include_git (bool): Analyse git history for risk (needs a real file).
        include_deps (bool): Resolve local dependency files for richer context.
        include_bugs (bool): Inject recurring Jira bug patterns (needs module).
        use_cache (bool): Use the file-level result cache when possible.
        auto_accept (bool): Flag high-confidence cases as auto_accepted.
        content (str | None): Pre-loaded source content. When provided, the
            service skips reading from disk — used for story/jira/virtual
            sources where `source` is not a real file path.
        repo_context (str | None): Whole-source understanding from the
            RepoComprehensionService (app overview, flows, risk areas). When
            set, it is injected into the prompt so tests are informed by the
            whole picture, not just this one file.
    """

    module: str | None = None
    user_story: str | None = None
    test_type: str | None = None
    include_git: bool = False
    include_deps: bool = False
    include_bugs: bool = False
    use_cache: bool = True
    auto_accept: bool = False
    content: str | None = None
    repo_context: str | None = None


class GenerationService:
    """
    Generate validated test cases from a single source.

    Wraps the existing core modules and returns a GenerationResult. Never
    prints, never calls input(), never calls sys.exit(). Runtime failures are
    captured in GenerationResult.error; only a missing API key raises
    (QAXConfigError), because that is a configuration problem the caller must
    fix before anything can run.
    """

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Configuration and credentials. When
                None, one is built from the environment (single-tenant mode),
                preserving the existing behaviour.
        """
        self.context = context or QAXContext.from_env()

    # ── Public API ──────────────────────────────────────────────────────────

    def generate(
        self,
        source: str,
        *,
        options: GenerationOptions | None = None,
        progress: ProgressReporter | None = None,
    ) -> GenerationResult:
        """
        Generate validated test cases from one source.

        Flow (identical to QAXPipeline._run_generation, minus the printing):
          1. Load content — from options.content, or read the file from disk.
          2. SmartChecker.run_pre_checks — cache / risk / test-type / skills.
          3. Cache hit → return the cached cases immediately (no API call).
          4. Optionally fetch bug patterns for the module.
          5. ClaudeClient.analyze_code — the API call.
          6. fix_common_issues + validate_batch — clean and filter.
          7. Return a GenerationResult.

        Args:
            source (str): File path, or a virtual identifier (e.g. "story://input")
                when options.content is supplied.
            options (GenerationOptions | None): Generation options. Defaults are
                used when None.
            progress (ProgressReporter | None): Progress sink. A NullProgressReporter
                is used when None.

        Returns:
            GenerationResult: The produced test cases plus summary/metadata.
                On a runtime failure, .error is set and .test_cases is empty.

        Raises:
            QAXConfigError: If the Anthropic API key is missing.
        """
        opts = options or GenerationOptions()
        prog = progress or NullProgressReporter()

        # Fail fast on missing credentials — this is a config problem, not a
        # runtime hiccup, so it raises rather than returning an error result.
        self.context.require_anthropic_key()

        try:
            content, module_hint, line_count = self._load_content(source, opts)
        except FileNotFoundError as exc:
            prog.emit("load", "error", str(exc))
            return GenerationResult(source=source, module=opts.module, error=str(exc))

        if not content or not content.strip():
            prog.emit("load", "warn", "source is empty — nothing to generate")
            return GenerationResult(
                source=source,
                module=opts.module or module_hint,
                summary={"total": 0, "valid_count": 0, "invalid_count": 0,
                         "needs_review_count": 0, "avg_confidence": 0.0},
            )

        effective_module = opts.module or module_hint

        try:
            return self._run(source, content, line_count, effective_module, opts, prog)
        except Exception as exc:  # never crash a face — capture and return
            logger.error("GenerationService failed for %s: %s", source, exc)
            prog.emit("generate", "error", str(exc))
            return GenerationResult(
                source=source, module=effective_module, error=str(exc)
            )

    def generate_from_source(
        self,
        source: str,
        *,
        input_type: str = "file",
        options: GenerationOptions | None = None,
        ext: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> BatchGenerationResult:
        """
        Generate test cases from any source type via the input adapters.

        This is the multi-source orchestration layer: it resolves the input
        adapter, fetches the source into a normalised list of items (one per
        file / ticket / story), and runs generate() on each — aggregating the
        results. A single file is just the one-item case.

        Supported input_type values: "file", "folder", "jira", "pr", "story".

        Args:
            source (str): The source identifier — a path, folder, Jira key,
                PR reference, or story text, depending on input_type.
            input_type (str): Which input adapter to use.
            options (GenerationOptions | None): Generation options applied to
                every item. Defaults are used when None.
            ext (str | None): File-extension filter for the "folder" adapter
                (e.g. ".py"). Ignored by other adapters.
            progress (ProgressReporter | None): Progress sink.

        Returns:
            BatchGenerationResult: Per-item results plus flattened views. Never
                raises for runtime issues — failures land in .error (source-level)
                or in each item's GenerationResult.error.

        Raises:
            QAXConfigError: If the Anthropic API key is missing.
        """
        opts = options or GenerationOptions()
        prog = progress or NullProgressReporter()

        # Fail fast on missing credentials before doing any fetching.
        self.context.require_anthropic_key()

        # ── Resolve the input adapter and fetch items ─────────────────────────
        prog.emit("input", "start", f"reading source via '{input_type}'")
        try:
            from core.input_adapters import get_input_adapter

            adapter = get_input_adapter(input_type)
            # Input adapters guarantee they never raise — they return [].
            items = adapter.fetch(source, ext=ext, module=opts.module)
        except Exception as exc:  # defensive — should not happen
            logger.error("Input adapter '%s' failed for %s: %s", input_type, source, exc)
            prog.emit("input", "error", str(exc))
            return BatchGenerationResult(
                input_type=input_type, source=source, error=str(exc)
            )

        if not items:
            prog.emit("input", "warn", "no analyzable input found")
            return BatchGenerationResult(
                input_type=input_type,
                source=source,
                error="No analyzable input found for this source",
            )

        prog.emit("input", "ok", f"{len(items)} item(s) to analyze")

        # ── Generate per item ─────────────────────────────────────────────────
        results: list[GenerationResult] = []
        for idx, item in enumerate(items, 1):
            name = item.get("file_name") or item.get("file_path") or f"item {idx}"
            prog.emit("item", "start", f"[{idx}/{len(items)}] {name}")
            results.append(self._generate_from_item(item, opts, prog))

        return BatchGenerationResult(
            input_type=input_type, source=source, items=results
        )

    def _generate_from_item(
        self,
        item: dict,
        opts: GenerationOptions,
        prog: ProgressReporter,
    ) -> GenerationResult:
        """
        Generate test cases for a single normalised input item.

        Uses the content the adapter already loaded (all adapters populate
        "content"), so nothing is re-read from disk — this is what makes
        non-disk sources (Jira, PR, story) work through the same path. Only
        falls back to a disk read when the item carries no content.

        Args:
            item (dict): A normalised input item from an adapter's fetch().
            opts (GenerationOptions): Base options for the run.
            prog (ProgressReporter): Progress sink.

        Returns:
            GenerationResult: The result for this one item.
        """
        source_type = item.get("source_type", "file")
        file_path = item.get("file_path") or f"{source_type}://input"
        content = item.get("content", "")
        metadata = item.get("metadata") or {}

        if content and content.strip():
            is_story = source_type in ("story", "jira")
            item_opts = replace(
                opts,
                content=content,
                user_story=(opts.user_story or content) if is_story else opts.user_story,
                module=opts.module or metadata.get("module") or metadata.get("issue_type"),
                # git history needs a real tracked file; skip it for adapter-loaded content
                include_git=False,
            )
            return self.generate(file_path, options=item_opts, progress=prog)

        # No pre-loaded content → treat file_path as a real disk path.
        return self.generate(file_path, options=opts, progress=prog)

    # ── Internals ───────────────────────────────────────────────────────────

    def _load_content(
        self, source: str, opts: GenerationOptions
    ) -> tuple[str, str | None, int]:
        """
        Resolve the source content, its module hint, and its line count.

        Uses options.content directly when provided (story/jira/virtual
        sources); otherwise reads the file from disk via core.code_reader.

        Args:
            source (str): File path or virtual identifier.
            opts (GenerationOptions): Options (may carry pre-loaded content).

        Returns:
            tuple[str, str | None, int]: (content, module_hint, line_count).

        Raises:
            FileNotFoundError: If a real file path does not exist.
        """
        if opts.content is not None:
            content = opts.content
            return content, opts.module, len(content.splitlines())

        from core.code_reader import read_file

        file_data = read_file(source)
        return (
            file_data.get("content", ""),
            file_data.get("module_hint"),
            file_data.get("size_lines", 0),
        )

    def _run(
        self,
        source: str,
        content: str,
        line_count: int,
        module: str | None,
        opts: GenerationOptions,
        prog: ProgressReporter,
    ) -> GenerationResult:
        """
        Execute the pre-checks + API + validation stack.

        Kept separate from generate() so the public method stays a thin
        try/except wrapper. See generate() for the step-by-step flow.

        Args:
            source (str): Source identifier.
            content (str): Source content.
            line_count (int): Number of lines in the content.
            module (str | None): Effective module hint.
            opts (GenerationOptions): Generation options.
            prog (ProgressReporter): Progress sink.

        Returns:
            GenerationResult: The generation outcome.
        """
        from core.claude_client import ClaudeClient
        from core.smart_checker import SmartChecker
        from core.validator import fix_common_issues, validate_batch

        # ── Optional git history for risk assessment ──────────────────────────
        git_history: dict | None = None
        if opts.include_git and opts.content is None:
            try:
                from core.git_analyzer import GitAnalyzer

                git_history = GitAnalyzer().get_file_history(source)
            except Exception as exc:
                logger.debug("git history unavailable for %s: %s", source, exc)

        # ── Pre-checks (cache / risk / test-type / skills) ────────────────────
        prog.emit("pre_check", "start", "Running pre-checks")
        checker = SmartChecker(project=self.context.project)
        pre = checker.run_pre_checks(
            file_path=source,
            file_content=content,
            module=module,
            git_history=git_history,
            user_story=opts.user_story,
        )
        prog.emit("pre_check", "ok", pre.get("pre_check_summary", ""))

        # ── Cache hit: re-validate before trusting it ─────────────────────────
        # The file-level cache stores the model's raw output, which is NOT
        # schema-validated. A malformed or truncated generation could have been
        # cached, so cached cases are always fixed + validated and only the valid
        # ones returned. If nothing survives validation the cache is treated as a
        # miss and we regenerate — this self-heals a poisoned cache entry.
        if opts.use_cache and pre.get("cache_hit"):
            cached = pre.get("cached_result") or []
            fixed = [fix_common_issues(tc) for tc in cached]
            batch = validate_batch(fixed)
            valid = batch.get("valid", [])
            if valid:
                prog.emit("cache", "ok", f"cache hit — {len(valid)} valid case(s)")
                return GenerationResult(
                    test_cases=valid,
                    source=source,
                    module=module,
                    from_cache=True,
                    model="cache",
                    summary=batch.get("summary", {}),
                    invalid=batch.get("invalid", []),
                )
            prog.emit("cache", "warn", "cache hit but 0 valid — regenerating")
            logger.warning(
                "QAX: cache for %s had 0 valid cases after re-validation — regenerating",
                source,
            )

        # ── Optional recurring bug patterns ───────────────────────────────────
        bug_patterns: str | None = None
        if opts.include_bugs and module:
            try:
                from core.bug_fetcher import BugFetcher

                bug_patterns = BugFetcher().get_patterns_for_module(module)
            except Exception as exc:
                logger.debug("bug patterns unavailable for %s: %s", module, exc)

        # ── The API call ──────────────────────────────────────────────────────
        prog.emit("generate", "start", "Calling Claude")
        client = ClaudeClient(project=self.context.project)
        raw = client.analyze_code(
            code=content,
            file_path=source,
            module_hint=module,
            bug_patterns=bug_patterns,
            user_story=opts.user_story,
            risk_assessment=pre.get("risk"),
            test_type=opts.test_type or pre.get("test_type"),
            skill_context=pre.get("skill_context"),
            risk_level=(pre.get("risk") or {}).get("risk_level", "low"),
            line_count=line_count,
            use_cache=opts.use_cache,
            repo_context=opts.repo_context,
        )

        used_model = pre.get("recommended_model", "") or (
            self.context.model_heavy if line_count > 150 else self.context.model_light
        )

        # ── Clean + validate ──────────────────────────────────────────────────
        fixed = [fix_common_issues(tc) for tc in raw]
        batch = validate_batch(fixed, auto_accept=opts.auto_accept)
        valid = batch.get("valid", [])
        summary = batch.get("summary", {})

        prog.emit(
            "validate",
            "ok",
            f"{len(valid)} valid / {len(batch.get('invalid', []))} invalid "
            f"(avg {summary.get('avg_confidence', 0)}/10)",
        )

        return GenerationResult(
            test_cases=valid,
            source=source,
            module=module,
            from_cache=False,
            model=used_model,
            summary=summary,
            invalid=batch.get("invalid", []),
        )
