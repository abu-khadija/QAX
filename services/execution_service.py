"""
services/execution_service.py — ExecutionService (Phase 2)
===========================================================
Runs generated test cases against a live target and returns a typed
ExecutionResult. Two engines are supported (matching the existing stack):

    "ui"   — Playwright browser automation (drives a real browser)
    "api"  — HTTP request runner (calls endpoints directly)
    "both" — API first, then UI
    "none" — run nothing (returns an empty result)

Reflecting the product's reality: execution needs a *running application*.
The base_url parameter is the live target (staging/production). Test cases are
generated from source, but they are executed against this URL.

Wraps the existing execution/ modules. Never prints business errors, never
calls sys.exit(); failures are captured in ExecutionResult.error.

Usage:
    from services.execution_service import ExecutionService

    svc = ExecutionService()
    result = svc.execute(test_cases, mode="api", base_url="https://staging.example.com")
    print(result.passed, "/", result.total)
"""

from __future__ import annotations

import logging

from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import ExecutionResult

logger = logging.getLogger(__name__)


class ExecutionService:
    """
    Execute test cases with the UI (Playwright) or API (HTTP) engine.

    headless and the target URL are parameters, not globals, so the same
    service can drive a local debug run, a CI run, or a per-tenant SaaS run.
    """

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Configuration. Built from the
                environment when None (single-tenant mode).
        """
        self.context = context or QAXContext.from_env()

    def execute(
        self,
        test_cases: list[dict],
        *,
        mode: str = "ui",
        base_url: str | None = None,
        headless: bool = True,
        stop_on_fail: bool = False,
        auth: dict | None = None,
        progress: ProgressReporter | None = None,
    ) -> ExecutionResult:
        """
        Execute test cases against a live target.

        Args:
            test_cases (list[dict]): Test cases to run.
            mode (str): "ui", "api", "both", or "none".
            base_url (str | None): Live application URL. Required in practice
                for meaningful "api" runs; the UI runner resolves URLs from the
                project config / test steps when not given here.
            headless (bool): Run the browser without a window (UI mode).
            stop_on_fail (bool): Stop after the first FAIL/ERROR.
            auth (dict | None): Supplied auth for api runs — {token, token_b,
                header, scheme}. Pre-seeds token/token_a/token_b into every
                test's variable store and sets the runner's auth scheme/header,
                so coverage templates run against apps QAX can't self-register into.
            progress (ProgressReporter | None): Progress sink.

        Returns:
            ExecutionResult: Wrapped batch result. Never raises — failures are
                captured in .error.
        """
        prog = progress or NullProgressReporter()
        run_mode = (mode or "none").lower()

        if run_mode == "none":
            prog.emit("execute", "info", "execution skipped (mode=none)")
            return ExecutionResult(mode="none", raw=self._empty_batch())

        if not test_cases:
            prog.emit("execute", "warn", "no test cases to execute")
            return ExecutionResult(mode=run_mode, raw=self._empty_batch())

        try:
            if run_mode == "api":
                return self._run_api(test_cases, base_url, stop_on_fail, prog, auth)
            if run_mode == "both":
                return self._run_both(test_cases, base_url, headless, stop_on_fail, prog, auth)
            return self._run_ui(test_cases, headless, stop_on_fail, prog)
        except Exception as exc:  # never crash a face
            logger.error("ExecutionService failed (mode=%s): %s", run_mode, exc)
            prog.emit("execute", "error", str(exc))
            return ExecutionResult(mode=run_mode, raw=self._empty_batch(), error=str(exc))

    # ── Engines ─────────────────────────────────────────────────────────────

    def _run_api(
        self, test_cases: list[dict], base_url: str | None,
        stop_on_fail: bool, prog: ProgressReporter, auth: dict | None = None,
    ) -> ExecutionResult:
        """Run the HTTP APIRunner against base_url. See execute() for args."""
        from execution.api_runner import APIRunner

        prog.emit("execute", "start", f"api run ({len(test_cases)} case(s))")
        runner = APIRunner()
        scheme = header = None
        seed: dict = {}
        if auth:
            scheme = auth.get("scheme")
            header = auth.get("header")
            # One supplied identity seeds `token` and `token_a`; a second seeds
            # `token_b` (the userB side of an IDOR/ownership check).
            tok = auth.get("token")
            if tok:
                seed["token"] = tok
                seed["token_a"] = tok
            if auth.get("token_b"):
                seed["token_b"] = auth["token_b"]
        runner.setup(base_url=base_url, auth_scheme=scheme, auth_header=header,
                     seed_variables=seed)
        try:
            batch = runner.execute_batch(test_cases, stop_on_fail=stop_on_fail)
        finally:
            runner.teardown()

        prog.emit("execute", "ok",
                  f"api: {batch.get('passed', 0)} passed / {batch.get('failed', 0)} failed")
        return ExecutionResult(mode="api", raw=batch)

    def _run_ui(
        self, test_cases: list[dict], headless: bool,
        stop_on_fail: bool, prog: ProgressReporter,
    ) -> ExecutionResult:
        """Run the Playwright UI engine. See execute() for args."""
        import asyncio

        try:
            from execution.playwright_runner import PLAYWRIGHT_AVAILABLE
        except ImportError:
            PLAYWRIGHT_AVAILABLE = False

        if not PLAYWRIGHT_AVAILABLE:
            msg = ("Playwright is not installed — run: "
                   "pip install playwright && playwright install chromium "
                   "(requires Python 3.11/3.12)")
            prog.emit("execute", "error", msg)
            return ExecutionResult(mode="ui", raw=self._empty_batch(), error=msg)

        prog.emit("execute", "start", f"ui run ({len(test_cases)} case(s))")

        async def _exec() -> dict:
            from execution.playwright_runner import PlaywrightRunner
            from execution.test_executor import TestExecutor

            runner = PlaywrightRunner()
            executor = TestExecutor()
            try:
                await runner.setup(headless=headless)
                return await executor.execute_batch(
                    test_cases, runner, stop_on_fail=stop_on_fail
                )
            finally:
                try:
                    await runner.teardown()
                except Exception:
                    pass

        batch = asyncio.run(_exec())
        prog.emit("execute", "ok",
                  f"ui: {batch.get('passed', 0)} passed / {batch.get('failed', 0)} failed")
        return ExecutionResult(mode="ui", raw=batch)

    def _run_both(
        self, test_cases: list[dict], base_url: str | None, headless: bool,
        stop_on_fail: bool, prog: ProgressReporter, auth: dict | None = None,
    ) -> ExecutionResult:
        """Run API then UI and combine the two batch results."""
        api = self._run_api(test_cases, base_url, stop_on_fail, prog, auth)
        ui = self._run_ui(test_cases, headless, stop_on_fail, prog)
        combined = {
            "api": api.raw,
            "ui": ui.raw,
            "total": api.total + ui.total,
            "passed": api.passed + ui.passed,
            "failed": api.failed + ui.failed,
            "errors": api.errors + ui.errors,
            "pass_rate": round(
                ((api.passed + ui.passed) / (api.total + ui.total) * 100)
                if (api.total + ui.total) else 0.0, 1
            ),
            "results": list(api.results) + list(ui.results),
        }
        error = ui.error or api.error
        return ExecutionResult(mode="both", raw=combined, error=error)

    @staticmethod
    def _empty_batch() -> dict:
        """Return a zeroed batch-result dict (no tests ran)."""
        return {
            "total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
            "pass_rate": 0.0, "results": [],
        }
