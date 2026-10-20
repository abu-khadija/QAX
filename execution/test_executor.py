"""
test_executor.py — Test Case Execution Orchestrator
=====================================================
Drives execution of one or more test cases through the full Playwright
pipeline with integrated AI self-healing for failed steps.

Execution flow per test case:
  1. Validate the test case structure
  2. For each step:
       a. ActionMapper.map_and_execute() → runs the Playwright action
       b. On FAIL: take screenshot, call ClaudeHealer.heal_step()
       c. If healed: retry once with suggested selector
       d. Record step result (PASS / FAIL / SKIP / HEALED)
  3. Determine overall test result (PASS if all non-skip steps pass)
  4. Return structured result dict

All methods are async and wrapped in try/except.
Never crashes — returns error result dicts on unexpected failures.
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Playwright availability guard ─────────────────────────────────────────────
try:
    from playwright.async_api import Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Page = None  # type: ignore[assignment,misc]

# ── Local execution imports ────────────────────────────────────────────────────
try:
    from execution.action_mapper import ActionMapper
    from execution.claude_healer import ClaudeHealer
    from execution.playwright_runner import PlaywrightRunner
    _EXECUTION_AVAILABLE = True
except ImportError:
    _EXECUTION_AVAILABLE = False
    ActionMapper = None   # type: ignore[assignment,misc]
    ClaudeHealer = None   # type: ignore[assignment,misc]
    PlaywrightRunner = None  # type: ignore[assignment,misc]


def ai_assist_enabled() -> bool:
    """
    True only when the operator opted in (QAX_AI_HEAL=true or --ai-heal). AI healing
    and the AI visual selector fallback send page HTML / a screenshot of the app to the
    AI provider, so they never run just because an API key happens to be in .env.
    """
    return os.getenv("QAX_AI_HEAL", "false").strip().lower() == "true"


class TestExecutor:
    """
    Orchestrates Playwright execution of EXAMPLE QA test cases.

    Handles step execution, screenshot capture on failure, and one round
    of AI-powered CSS selector healing per failed step via ClaudeHealer.

    Usage:
        runner  = PlaywrightRunner()
        await runner.setup(headless=True)

        executor = TestExecutor()
        result   = await executor.execute_test_case(test_case, runner)
        batch    = await executor.execute_batch([tc1, tc2, tc3], runner)

        await runner.teardown()

    Attributes:
        action_mapper (ActionMapper): Maps natural language steps to actions.
        healer (ClaudeHealer): AI selector self-healer.
    """

    def __init__(self, dry_run: bool = True) -> None:
        """
        Initialise executor with an ActionMapper and ClaudeHealer.

        Logs a warning if the execution dependencies are not available.
        All execute methods will return safe error dicts in that case.

        Args:
            dry_run: Auto-filed Jira bugs (AUTO_BUG_REPORT) are only drafted unless the
                caller passes False — an explicit operator opt-in such as `execute --upload`.
                DRY_RUN=true in the environment still forces a dry run.
        """
        self.dry_run = dry_run
        if not _EXECUTION_AVAILABLE:
            logger.warning(
                "TestExecutor: execution dependencies not available. "
                "All execute methods will return safe error results."
            )
            self.action_mapper = None
            self.healer = None
            return

        self.action_mapper = ActionMapper()
        self.healer = ClaudeHealer()

    # ─────────────────────────────────────────────────────────────────────────
    # Public: execute_test_case
    # ─────────────────────────────────────────────────────────────────────────

    async def execute_test_case(
        self,
        test_case: dict,
        runner: "PlaywrightRunner",
    ) -> dict:
        """
        Execute a single test case through all its steps using Playwright.

        For each step in test_case["steps"]:
          - Maps the natural-language action to a Playwright call
          - On failure: takes a screenshot and attempts AI healing once
          - Records per-step results with status, error, and screenshot path

        Overall status is PASS only if every non-skipped step passes.
        A single FAIL marks the entire test case as FAIL.

        Args:
            test_case (dict): Test case dict with at least:
                                - test_case_id (str)
                                - title (str)
                                - steps (list[dict])
                                  Each step: {action, expected_result, ...}
            runner (PlaywrightRunner): Initialized runner (setup() must have
                                       been called before passing it here).

        Returns:
            dict: Execution result with keys:
                    - test_case_id (str)
                    - title (str)
                    - status (str): "PASS" | "FAIL" | "ERROR" | "SKIP"
                    - steps (list[dict]): Per-step results
                    - start_time (str): ISO timestamp
                    - end_time (str): ISO timestamp
                    - duration_seconds (float)
                    - screenshot (str): Path to failure screenshot or ""
                    - healed_steps (int): Count of AI-healed steps
                    - error (str): Top-level error message or ""
        """
        start_ts = datetime.now(timezone.utc).isoformat()
        start_time = time.monotonic()

        tc_id = test_case.get("test_case_id", "UNKNOWN")
        title = test_case.get("title", "Untitled")
        if getattr(self, "action_mapper", None) is not None:
            import uuid as _uuid
            try:
                _u = _uuid.uuid4().hex[:8]
                # {{unique}}: 8 hex characters; {{unique_letters}}: letters only, for fields that
                # accept letters only (names — found on PetClinic Angular); {{unique_digits}}: digits
                self.action_mapper.case_vars = {
                    "unique": _u,
                    "unique_letters": "".join("abcdefghijklmnop"[int(c, 16)] for c in _u),
                    "unique_digits": str(int(_u, 16) % 10**8).zfill(8),
                }
            except AttributeError:   # a mapper without per-case variables
                pass

        # ── Guard: execution not available ─────────────────────────────────
        if not _EXECUTION_AVAILABLE or not self.action_mapper:
            return self._error_result(
                tc_id, title, start_ts,
                "Execution layer not available (Playwright not installed)"
            )

        # ── Guard: runner not ready ────────────────────────────────────────
        if not runner or not runner.is_ready():
            return self._error_result(
                tc_id, title, start_ts,
                "PlaywrightRunner is not set up. Call runner.setup() first."
            )

        # ── Guard: validate steps ──────────────────────────────────────────
        # Generated tests carry automation_steps (playwright_action / selector_key
        # / value) built for browser execution. Prefer those; fall back to
        # manual_steps, then the legacy "steps" key for older files. The Jira
        # push keeps reading manual_steps separately — this does not touch it.
        steps = (
            test_case.get("automation_steps")
            or test_case.get("manual_steps")
            or test_case.get("steps")
            or []
        )
        if not steps:
            return self._error_result(
                tc_id, title, start_ts,
                "Test case has no steps to execute."
            )

        page = await runner.get_page()

        logger.info("Executing: [%s] %s (%d steps)", tc_id, title, len(steps))

        step_results = []
        overall_status = "PASS"
        failure_screenshot = ""
        html_snapshot_path = ""
        healed_count = 0
        first_failure_step = None  # capture details for bug report

        step_shots = os.getenv("QAX_STEP_SCREENSHOTS", "false").strip().lower() == "true"
        quality = None
        if os.getenv("QAX_PAGE_QUALITY", "true").strip().lower() != "false" and page is not None:
            from core.page_quality import QualityObserver
            quality = QualityObserver()    # accessibility + performance of every page visited
        for i, step in enumerate(steps, start=1):
            step["step_number"] = i  # Ensure step_number is set
            page = await runner.get_page() or page        # a new tab opened by the last step
            step_result = await self._execute_step(step, page, runner, tc_id)
            step_results.append(step_result)
            if quality is not None:
                await quality.observe(await runner.get_page() or page)
            # `--screenshots all` / `--watch`: what the browser showed after EVERY step —
            # the run can be followed (and reviewed) step by step, not only at a failure.
            if step_shots and not step_result.get("screenshot") and hasattr(runner, "take_screenshot"):
                try:
                    shot = await runner.take_screenshot(f"{str(tc_id).replace('/', '_')}_step{i}",
                                                        folder="output/screenshots/steps")
                    if shot:
                        step_result["screenshot"] = shot
                except Exception:  # noqa: BLE001
                    pass

            # ── Handle failure ──────────────────────────────────────────
            if step_result["status"] == "FAIL":
                overall_status = "FAIL"

                if not first_failure_step:
                    first_failure_step = {
                        "step_number":   i,
                        "step_title":    step.get("action", f"Step {i}"),
                        "expected_result": step.get("expected_result", ""),
                        "actual_result": step_result.get("actual_result", ""),
                        "error":         step_result.get("error", ""),
                        "selector_used": step_result.get("selector_used", ""),
                    }

                # ── Screenshot ─────────────────────────────────────────
                if not failure_screenshot:
                    safe_id = tc_id.replace("/", "_")
                    failure_screenshot = await runner.take_screenshot(
                        f"{safe_id}_step{i}_fail"
                    )
                    if failure_screenshot:
                        step_result["screenshot"] = failure_screenshot

                # ── HTML snapshot of page at point of failure ──────────
                if not html_snapshot_path:
                    try:
                        from pathlib import Path as _Path
                        html_content = await page.content()
                        ts_snap = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                        safe_id = tc_id.replace("/", "_")
                        snap_dir = _Path("output") / "screenshots"
                        snap_dir.mkdir(parents=True, exist_ok=True)
                        snap_file = snap_dir / f"snapshot_{safe_id}_{ts_snap}.html"
                        snap_file.write_text(html_content, encoding="utf-8")
                        html_snapshot_path = str(snap_file)
                        logger.info(
                            "[%s] HTML snapshot saved: %s", tc_id, html_snapshot_path
                        )
                    except Exception as _he:
                        logger.debug("HTML snapshot failed (non-fatal): %s", _he)

            elif step_result["status"] == "HEALED":
                healed_count += 1
                # HEALED means it eventually passed after AI healing
                # Don't mark overall as FAIL

        # A failure that FOLLOWS a step that could not run (ERROR: an unknown element name, a
        # crash) is a consequence of the broken test, not evidence about the app —
        # e.g. "log in" never ran, so "add a note" timed out. That case is ERROR, and no bug
        # is filed for it (found in a real-browser run of the E2E flow, 2026-10-20).
        case_error = ""
        first_bad = next((sr for sr in step_results if sr.get("status") not in ("PASS", "HEALED")), None)
        if overall_status == "FAIL" and first_bad is not None and first_bad.get("status") == "ERROR":
            # (a SKIP — an assertion that could not be evaluated — does not stop later steps
            # from meaning something, so a real failure after it still wins)
            overall_status = "ERROR"
            first_failure_step = None
            case_error = (f"Step {first_bad.get('step_number')} could not run ({first_bad.get('status')}); "
                          "the failures after it are its consequence — fix the test, then re-run")

        # A case is PASS only when every step actually ran and passed. A step that
        # crashed (ERROR) or was not executed (SKIP: unrecognised action, no browser)
        # proved nothing — never report the case as PASS on its strength.
        if overall_status == "PASS":
            step_statuses = {sr.get("status") for sr in step_results}
            if "ERROR" in step_statuses:
                overall_status = "ERROR"
            elif not step_statuses <= {"PASS", "HEALED"}:
                overall_status = "SKIP"

        end_ts = datetime.now(timezone.utc).isoformat()
        duration = round(time.monotonic() - start_time, 2)

        logger.info(
            "Result: [%s] %s — %s in %.1fs (%d healed)",
            tc_id, title, overall_status, duration, healed_count
        )

        # ── Build test log text from all step results ──────────────────
        log_lines = [f"Test: {title} [{tc_id}]", ""]
        for sr in step_results:
            snum   = sr.get("step_number", "?")
            sstatus = sr.get("status", "?")
            saction = sr.get("actual_result", "")[:120]
            log_lines.append(f"Step {snum}: {sstatus} — {saction}")
            if sr.get("error"):
                log_lines.append(f"  Error: {sr['error'][:200]}")
        test_log_text = "\n".join(log_lines)

        # ── Auto-report bug to Jira on failure ────────────────────────
        bug_key      = ""
        bug_url      = ""
        risk_level   = test_case.get("risk_level", "medium")
        test_type    = test_case.get("test_type", "")
        auto_enabled = os.getenv("AUTO_BUG_REPORT", "true").lower() == "true"

        if overall_status == "FAIL" and first_failure_step and auto_enabled:
            try:
                from core.bug_reporter import BugReporter
                reporter = BugReporter(dry_run=getattr(self, "dry_run", True))
                bug_result = reporter.create_bug(
                    test_case=test_case,
                    failure=first_failure_step,
                    screenshot_path=failure_screenshot or None,
                    html_snapshot_path=html_snapshot_path or None,
                    test_log=test_log_text,
                    risk_level=risk_level,
                    test_type=test_type,
                    selector_used=first_failure_step.get("selector_used"),
                    heal_attempted=healed_count > 0,
                    heal_succeeded=False,
                )
                if bug_result:
                    bug_key = bug_result.get("key", "")
                    bug_url = bug_result.get("url", "")
                    logger.info(
                        "[%s] Bug reported: %s", tc_id, bug_key
                    )
            except Exception as _be:
                logger.debug("Auto-bug-report failed (non-fatal): %s", _be)

        out_quality = quality.summary() if quality is not None else {}
        return {
            **({"quality": out_quality} if out_quality else {}),
            "test_case_id":      tc_id,
            "title":             title,
            "status":            overall_status,
            "steps":             step_results,
            "start_time":        start_ts,
            "end_time":          end_ts,
            "duration_seconds":  duration,
            "screenshot":        failure_screenshot,
            "html_snapshot":     html_snapshot_path,
            "healed_steps":      healed_count,
            "error":             case_error,
            "risk_level":        risk_level,
            "bug_key":           bug_key,
            "bug_url":           bug_url,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Public: execute_batch
    # ─────────────────────────────────────────────────────────────────────────

    # Unambiguous phrases only: a bare "rate limit" also titles settings pages
    # (PocketBase's "Rate limits" section) and is not a signal (found 2026-10-19).
    _THROTTLE_TEXT = re.compile(r"(too many (attempts|requests|login attempts)|try (again )?later|"
                                r"please try later|slow down|rate limit (exceeded|reached)|"
                                r"temporarily (locked|blocked))", re.I)

    async def _mark_if_throttled(self, result: dict, runner) -> None:
        """
        A case that failed while the app was rate-limiting QAX is not a verdict on the
        feature: turn it into ERROR (throttled). Signals: an HTTP 429 during the case, or
        a rate-limit message on the page (Healthchecks answers 200 + "Too many attempts,
        please try later." — found 2026-10-19). Never raises.
        """
        try:
            n429 = runner.take_throttled() if hasattr(runner, "take_throttled") else 0
            if result.get("status") not in ("FAIL", "ERROR"):
                return
            text = ""
            page = await runner.get_page() if hasattr(runner, "get_page") else None
            if page is not None:
                try:
                    text = await page.inner_text("body", timeout=2_000)
                except Exception:  # noqa: BLE001
                    text = ""
            m = self._THROTTLE_TEXT.search(text or "")
            if n429 or m:
                why = f"{n429} HTTP 429 answer(s)" if n429 else f"the page says \"{m.group(0)}\""
                result["status"] = "ERROR"
                result["throttled"] = True
                result["error"] = (f"The app rate-limited QAX ({why}) — not a verdict on the feature. "
                                   "Wait and re-run, or raise the limit on the staging app.")
        except Exception:  # noqa: BLE001
            pass

    async def execute_batch(
        self,
        test_cases: list[dict],
        runner: "PlaywrightRunner",
        stop_on_fail: bool = False,
    ) -> dict:
        """
        Execute a list of test cases sequentially and return a batch summary.

        Each test case is run in order. If stop_on_fail is True, execution
        stops immediately after the first FAIL result.

        Args:
            test_cases (list[dict]): List of test case dicts to execute.
            runner (PlaywrightRunner): Initialized Playwright runner.
            stop_on_fail (bool): If True, stop after first FAIL.
                                  Default: False (run all cases).

        Returns:
            dict: Batch result with keys:
                    - total (int): Total test cases attempted
                    - passed (int): Count of PASS results
                    - failed (int): Count of FAIL results
                    - errors (int): Count of ERROR results
                    - skipped (int): Count of SKIP results
                    - pass_rate (float): Percentage of passed tests
                    - results (list[dict]): Individual test case results
                    - start_time (str): ISO timestamp
                    - end_time (str): ISO timestamp
                    - duration_seconds (float)
                    - stopped_early (bool): True if stop_on_fail triggered
        """
        if not test_cases:
            return {
                "total": 0, "passed": 0, "failed": 0, "errors": 0,
                "skipped": 0, "pass_rate": 0.0, "results": [],
                "start_time": datetime.now(timezone.utc).isoformat(),
                "end_time": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": 0.0, "stopped_early": False,
            }

        start_ts = datetime.now(timezone.utc).isoformat()
        start_time = time.monotonic()
        results = []
        stopped_early = False

        logger.info(
            "Starting batch execution: %d test case(s) (stop_on_fail=%s)",
            len(test_cases), stop_on_fail
        )

        for n, tc in enumerate(test_cases):
            # Every case starts in a clean browser session (no cookies/storage from
            # the case before), so a verdict never depends on the order of the cases.
            if n and hasattr(runner, "new_session"):
                await runner.new_session()
            if hasattr(runner, "take_writes"):
                runner.take_writes()                       # start this case's ledger empty
            if hasattr(runner, "take_throttled"):
                runner.take_throttled()
            if hasattr(runner, "take_dialogs"):
                runner.take_dialogs()
            if hasattr(runner, "take_page_errors"):
                runner.take_page_errors()
            result = await self.execute_test_case(tc, runner)
            await self._after_case(tc, result, runner)
            if hasattr(runner, "take_page_errors"):
                errs = runner.take_page_errors()
                if errs:
                    result["page_errors"] = errs          # observed problems, even on a PASS
            if hasattr(runner, "take_dialogs"):
                dialogs = runner.take_dialogs()
                if dialogs:
                    result["dialogs"] = dialogs            # e.g. "Are you sure?" — accepted
            await self._mark_if_throttled(result, runner)
            if hasattr(runner, "stop_trace"):
                trace = await runner.stop_trace(str(tc.get("test_case_id", "case")))
                if trace:
                    result["trace"] = trace
            # What this case sent that can change data on the target (form posts, deletes…).
            # QAX does not undo data created through the UI — it says what it sent.
            result["writes"] = runner.take_writes() if hasattr(runner, "take_writes") else []
            results.append(result)

            if stop_on_fail and result["status"] in ("FAIL", "ERROR"):
                tc_id = result.get("test_case_id", "?")
                logger.info("stop_on_fail=True — halting after %s (%s)", tc_id, result["status"])
                stopped_early = True
                break

        throttled = sum(1 for r in results if r.get("throttled"))

        # ── Tally results ──────────────────────────────────────────────────
        counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "SKIP": 0}
        for r in results:
            status = r.get("status", "ERROR")
            counts[status] = counts.get(status, 0) + 1

        total = len(results)
        passed = counts["PASS"]
        pass_rate = round((passed / total * 100) if total > 0 else 0.0, 1)

        end_ts = datetime.now(timezone.utc).isoformat()
        duration = round(time.monotonic() - start_time, 2)

        logger.info(
            "Batch complete: %d/%d passed (%.1f%%) in %.1fs",
            passed, total, pass_rate, duration
        )

        writes_sent = sum(len(r.get("writes") or []) for r in results)
        return {
            "writes_sent": writes_sent,
            "throttled": throttled,
            "total": total,
            "passed": counts["PASS"],
            "failed": counts["FAIL"],
            "errors": counts["ERROR"],
            "skipped": counts["SKIP"],
            "pass_rate": pass_rate,
            "results": results,
            "start_time": start_ts,
            "end_time": end_ts,
            "duration_seconds": duration,
            "stopped_early": stopped_early,
        }

    async def _after_case(self, tc: dict, result: dict, runner) -> None:
        """
        After a case, in its own session: the visual check of the screen it ended on
        (`--visual`, a lead, never a verdict), then its cleanup_steps — undo what it created
        (run on PASS and FAIL; their outcome is reported, the verdict does not change).
        Never raises.
        """
        try:
            page = await runner.get_page() if hasattr(runner, "get_page") else None
        except Exception:  # noqa: BLE001
            page = None
        if page is None:
            return
        try:
            from core.visual_check import check_case_screen
            vis = await check_case_screen(page, str(tc.get("test_case_id", "case")))
            if vis:
                result["visual"] = vis
        except Exception:  # noqa: BLE001
            pass
        cleanup = [s for s in (tc.get("cleanup_steps") or []) if isinstance(s, dict)]
        if not cleanup or os.getenv("QAX_CLEANUP", "true").strip().lower() == "false":
            return
        done = []
        for n, step in enumerate(cleanup, start=1):
            step = dict(step, step_number=step.get("step_number") or n)
            try:
                page = await runner.get_page() or page
                r = await self._execute_step(step, page, runner, f"{tc.get('test_case_id', 'case')}-cleanup")
            except Exception as e:  # noqa: BLE001
                r = {"step_number": n, "status": "ERROR", "error": str(e)[:200]}
            done.append({"step_number": r.get("step_number", n), "status": r.get("status"),
                         "actual_result": r.get("actual_result", ""), "error": r.get("error")})
        ok = all(d["status"] in ("PASS", "HEALED") for d in done)
        result["cleanup"] = {"status": "done" if ok else "incomplete", "steps": done}

    # ─────────────────────────────────────────────────────────────────────────
    # Private: _execute_step
    # ─────────────────────────────────────────────────────────────────────────

    async def _execute_step(
        self,
        step: dict,
        page: "Page",
        runner: "PlaywrightRunner",
        tc_id: str,
    ) -> dict:
        """
        Execute a single test step with optional AI healing on failure.

        Runs the step through ActionMapper. On FAIL:
          1. Captures page HTML
          2. Calls ClaudeHealer.heal_step() for a selector suggestion
          3. If suggestion returned: retries once with the new selector
          4. Reports HEALED or FAIL accordingly

        Args:
            step (dict): Step dict with action, expected_result, step_number.
            page (Page): Active Playwright page.
            runner (PlaywrightRunner): Used for screenshot capture.
            tc_id (str): Parent test case ID (for logging).

        Returns:
            dict: Step result with:
                    - status: "PASS" | "FAIL" | "SKIP" | "HEALED"
                    - healed: bool
                    - healed_selector: str | None (the selector that fixed it)
                    - Plus all fields from ActionMapper.map_and_execute()
        """
        step_num = step.get("step_number", "?")

        try:
            # ── First attempt ──────────────────────────────────────────────
            result = await self.action_mapper.map_and_execute(step, page)
            result["healed"] = False
            result["healed_selector"] = None

            if result["status"] != "FAIL":
                return result

            # ── AI healing sends the page HTML to the AI provider: opt-in only ──
            if not ai_assist_enabled():
                logger.info("[%s] Step %s FAILED — AI healing off (QAX_AI_HEAL=true / --ai-heal "
                            "to send the page HTML to the AI provider for a selector fix)",
                            tc_id, step_num)
                return result

            # ── Step failed — attempt AI healing ───────────────────────────
            logger.info("[%s] Step %s FAILED — attempting AI healing", tc_id, step_num)

            error_msg = result.get("error", "")
            page_html = await self.healer.get_page_html(page) if self.healer else ""
            suggestion = None

            if self.healer and page_html:
                step["action_type"] = result.get("action_type", "")
                step["selector_used"] = result.get("selector_used", "")
                suggestion = await self.healer.heal_step(step, error_msg, page_html)

            if suggestion and suggestion.get("suggested_selector"):
                healed_selector = suggestion["suggested_selector"]
                logger.info(
                    "[%s] Step %s — retrying with healed selector: %s",
                    tc_id, step_num, healed_selector
                )

                # ── Retry with healed selector ─────────────────────────────
                healed_result = await self.action_mapper.map_and_execute(
                    step, page, selector_override=healed_selector
                )

                if healed_result["status"] == "PASS":
                    healed_result["status"] = "HEALED"
                    healed_result["healed"] = True
                    healed_result["healed_selector"] = healed_selector
                    logger.info(
                        "[%s] Step %s HEALED successfully with '%s'",
                        tc_id, step_num, healed_selector
                    )
                    # C4: Persist the healed selector to selectors.json
                    try:
                        await self.healer.refresh_selectors(
                            broken_selector=step.get("selector_used", ""),
                            element_description=step.get("action", ""),
                            playwright_page=page,
                        )
                        logger.info(
                            "[%s] Step %s — Selector auto-healed and updated",
                            tc_id, step_num,
                        )
                    except Exception as _re:
                        logger.debug("refresh_selectors failed (non-fatal): %s", _re)
                    return healed_result

                logger.info(
                    "[%s] Step %s still FAILED after healing attempt",
                    tc_id, step_num
                )
                # Return the latest fail result (more informative)
                healed_result["healed"] = False
                healed_result["healed_selector"] = None
                return healed_result

            # ── No healing available — return original failure ─────────────
            return result

        except Exception as e:  # noqa: BLE001
            logger.error("[%s] Step %s raised unexpected error: %s", tc_id, step_num, e)
            return {
                "step_number": step_num,
                "action_type": "unknown",
                "status": "ERROR",
                "actual_result": "",
                "error": str(e),
                "screenshot": "",
                "selector_used": "",
                "healed": False,
                "healed_selector": None,
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Private: _error_result
    # ─────────────────────────────────────────────────────────────────────────

    def _error_result(
        self,
        tc_id: str,
        title: str,
        start_ts: str,
        error_message: str,
    ) -> dict:
        """
        Build a standardised ERROR result dict for a test case that could
        not be started (e.g., runner not ready, no steps defined).

        Args:
            tc_id (str): Test case ID.
            title (str): Test case title.
            start_ts (str): ISO start timestamp.
            error_message (str): Human-readable error description.

        Returns:
            dict: Standard result dict with status="ERROR".
        """
        logger.error("Cannot execute [%s] — %s", tc_id, error_message)
        now = datetime.now(timezone.utc).isoformat()
        return {
            "test_case_id": tc_id,
            "title": title,
            "status": "ERROR",
            "steps": [],
            "start_time": start_ts,
            "end_time": now,
            "duration_seconds": 0.0,
            "screenshot": "",
            "healed_steps": 0,
            "error": error_message,
        }


async def run_parallel(test_cases: list, workers: int = 2, *, headless: bool = True,
                       stop_on_fail: bool = False, dry_run: bool = True) -> dict:
    """
    Run cases on several browsers at once (each worker: its own browser, its own clean
    session per case) and merge the results in the original order. Cases must not depend
    on each other — QAX cases are self-contained ({{unique}} values, a fresh session per
    case) — so a verdict does not change with the number of workers.

    Args:
        test_cases: the cases.
        workers: browsers in parallel (1 = the sequential batch).
        headless, stop_on_fail, dry_run: as for a single batch.

    Returns:
        dict: the same shape as TestExecutor.execute_batch(), plus "workers".
    """
    from execution.playwright_runner import PlaywrightRunner
    workers = max(1, min(int(workers or 1), 8, len(test_cases) or 1))
    start = time.monotonic()
    start_ts = datetime.now(timezone.utc).isoformat()
    chunks = [[(i, tc) for i, tc in enumerate(test_cases) if i % workers == w] for w in range(workers)]

    async def one(chunk):
        runner = PlaywrightRunner()
        executor = TestExecutor(dry_run=dry_run)
        try:
            await runner.setup(headless=headless)
            batch = await executor.execute_batch([tc for _, tc in chunk], runner, stop_on_fail=stop_on_fail)
        finally:
            try:
                await runner.teardown()
            except Exception:  # noqa: BLE001
                pass
        return [(chunk[k][0], r) for k, r in enumerate(batch.get("results") or [])], batch.get("stopped_early", False)

    parts = await asyncio.gather(*(one(c) for c in chunks if c))
    results = [r for _, r in sorted((x for part, _ in parts for x in part), key=lambda t: t[0])]
    counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "SKIP": 0}
    for r in results:
        counts[r.get("status", "ERROR")] = counts.get(r.get("status", "ERROR"), 0) + 1
    total = len(results)
    return {
        "writes_sent": sum(len(r.get("writes") or []) for r in results),
        "throttled": sum(1 for r in results if r.get("throttled")),
        "total": total, "passed": counts["PASS"], "failed": counts["FAIL"],
        "errors": counts["ERROR"], "skipped": counts["SKIP"],
        "pass_rate": round((counts["PASS"] / total * 100) if total else 0.0, 1),
        "results": results, "start_time": start_ts, "end_time": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.monotonic() - start, 2),
        "stopped_early": any(stopped for _, stopped in parts), "workers": workers,
    }
