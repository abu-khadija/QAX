"""
execution/ — Playwright Test Execution Engine
===============================================
Prompt 3: Execution Layer.

This package provides the full test execution pipeline:
  - PlaywrightRunner   : Browser lifecycle management
  - ActionMapper       : Natural-language step → Playwright action
  - TestExecutor       : Orchestrates test case execution
  - ClaudeHealer       : AI-powered CSS selector self-healing
  - ResultsReporter    : Console + HTML reports + Xray upload

All components require Playwright to be installed:
  pip install playwright==1.44.0
  playwright install chromium

Playwright requires Python 3.11 or 3.12 (not 3.14).
All classes degrade gracefully if Playwright is not available.

Usage:
    import asyncio
    from execution.playwright_runner import PlaywrightRunner
    from execution.test_executor import TestExecutor

    async def main():
        runner = PlaywrightRunner()
        await runner.setup(headless=True)
        executor = TestExecutor()
        result = await executor.execute_test_case(test_case, runner)
        await runner.teardown()

    asyncio.run(main())
"""

# Expose key classes at package level for convenient imports
try:
    from execution.playwright_runner import PlaywrightRunner
    from execution.action_mapper import ActionMapper
    from execution.test_executor import TestExecutor
    from execution.claude_healer import ClaudeHealer
    from execution.results_reporter import ResultsReporter

    __all__ = [
        "PlaywrightRunner",
        "ActionMapper",
        "TestExecutor",
        "ClaudeHealer",
        "ResultsReporter",
    ]
except ImportError:
    # Playwright or other deps not available — package still importable
    __all__ = []
