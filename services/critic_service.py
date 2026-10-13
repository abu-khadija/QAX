"""
services/critic_service.py — CriticService (the first agent behavior)
=====================================================================
"Verify, don't trust." After execution, this reviews every FAILED test and
decides whether it is a real application bug or a broken/self-inflicted test
failure — the judgement a QA lead makes, and the one a raw pipeline skips.

This exists because a pipeline reports every FAIL as if the app is broken. On
TaskFlow that meant 8 "failures" that were actually the tests' own fault (they
logged in without registering a user first → 401). The Critic separates those
from genuine bugs, so a report only cries wolf when there's a real wolf.

Approach (hybrid): cheap deterministic signals flag the obvious broken-test
patterns; one Claude call makes the judgement over the collected evidence
(each failing test's intended steps + what actually happened per step). Never
prints, never calls sys.exit(); failures are captured in CriticResult.error.

Usage:
    from services.critic_service import CriticService
    result = CriticService(ctx).review(execution_result, test_cases)
    print(result.summary())          # "0 real bug(s), 8 broken test(s), 0 uncertain"
"""

from __future__ import annotations

import json
import logging
import re

from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import CriticResult, ExecutionResult, FailureVerdict

logger = logging.getLogger(__name__)

_VALID_CLASSES = {"real_bug", "broken_test", "uncertain"}


class CriticService:
    """
    Review failed tests and classify each as a real bug or a broken test.

    One Claude call over all failures; deterministic hints are folded into the
    evidence to steer the judgement. dry_run has no effect (read-only review).
    """

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Config/credentials. Built from the
                environment when None.
        """
        self.context = context or QAXContext.from_env()

    def review(
        self,
        execution: ExecutionResult | dict,
        test_cases: list[dict],
        *,
        progress: ProgressReporter | None = None,
    ) -> CriticResult:
        """
        Classify every failed test in the execution result.

        Args:
            execution (ExecutionResult | dict): The execution outcome.
            test_cases (list[dict]): The generated test cases (for their steps).
            progress (ProgressReporter | None): Progress sink.

        Returns:
            CriticResult: Per-failure verdicts. Never raises for runtime issues.

        Raises:
            QAXConfigError: If the Anthropic API key is missing.
        """
        prog = progress or NullProgressReporter()
        self.context.require_anthropic_key()

        raw = execution.to_batch_dict() if isinstance(execution, ExecutionResult) else (execution or {})
        results = raw.get("results", [])
        failed = [r for r in results if r.get("status") in ("FAIL", "ERROR")]

        if not failed:
            prog.emit("critic", "ok", "no failures to review")
            return CriticResult(verdicts=[])

        prog.emit("critic", "start", f"reviewing {len(failed)} failure(s)")

        # Match test cases to their results so we can show intended vs actual.
        tc_by_key = {}
        for tc in test_cases:
            tc_by_key[tc.get("test_case_id", "")] = tc
            tc_by_key[tc.get("title", "")] = tc

        evidence = [self._evidence_for(r, tc_by_key) for r in failed]

        try:
            verdicts = self._judge(evidence)
        except Exception as exc:
            logger.error("CriticService failed: %s", exc)
            prog.emit("critic", "error", str(exc))
            return CriticResult(error=str(exc))

        prog.emit("critic", "ok",
                  f"{sum(1 for v in verdicts if v.classification == 'real_bug')} real bug(s)")
        return CriticResult(verdicts=verdicts)

    # ── Internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _evidence_for(result: dict, tc_by_key: dict) -> dict:
        """Collect intended steps + actual per-step outcomes for one failure."""
        tc = (tc_by_key.get(result.get("test_case_id", ""))
              or tc_by_key.get(result.get("title", "")) or {})
        steps = result.get("steps") or result.get("steps_results") or []
        failed_step = next(
            (s.get("step_number") for s in steps if s.get("status") in ("FAIL", "ERROR")),
            None,
        )
        return {
            "title": result.get("title", ""),
            "test_case_id": result.get("test_case_id", ""),
            "failed_step": failed_step,
            "intended": [
                {k: s.get(k) for k in ("step_number", "method", "path", "expected_status", "use_auth")
                 if s.get(k) is not None}
                for s in (tc.get("api_steps") or [])
            ],
            "actual": [
                {"step": s.get("step_number"), "got": s.get("response_code"),
                 "error": (s.get("error") or "")[:160]}
                for s in steps
            ],
        }

    def _judge(self, evidence: list[dict]) -> list[FailureVerdict]:
        """Ask Claude to classify each failure; return verdicts."""
        import anthropic

        client = anthropic.Anthropic(api_key=self.context.anthropic_key)
        response = client.messages.create(
            model=self.context.model_heavy,
            max_tokens=2500,
            messages=[{"role": "user", "content": self._build_prompt(evidence)}],
        )
        data = self._parse_json_array(response.content[0].text)

        verdicts: list[FailureVerdict] = []
        by_title = {e["title"]: e for e in evidence}
        for item in data:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", ""))
            cls = str(item.get("classification", "uncertain")).strip().lower()
            if cls not in _VALID_CLASSES:
                cls = "uncertain"
            ev = by_title.get(title, {})
            verdicts.append(FailureVerdict(
                test_case_id=ev.get("test_case_id", ""),
                title=title,
                classification=cls,
                reason=str(item.get("reason", ""))[:400],
                suggestion=str(item.get("suggestion", ""))[:400],
                failed_step=ev.get("failed_step"),
            ))

        # Any failure the model didn't return → uncertain, so nothing is dropped.
        returned = {v.title for v in verdicts}
        for e in evidence:
            if e["title"] not in returned:
                verdicts.append(FailureVerdict(
                    test_case_id=e.get("test_case_id", ""), title=e["title"],
                    classification="uncertain",
                    reason="No verdict returned for this failure.",
                    failed_step=e.get("failed_step"),
                ))
        return verdicts

    @staticmethod
    def _build_prompt(evidence: list[dict]) -> str:
        """Build the Critic judgement prompt."""
        return (
            "You are a QA lead reviewing FAILED automated API tests. For EACH failure decide:\n"
            "- \"real_bug\": the application behaved incorrectly for a valid, well-formed request.\n"
            "- \"broken_test\": the test itself is at fault — bad setup, wrong expectation, a missing\n"
            "  prerequisite (e.g. logging in without first registering the user → 401), an unresolved\n"
            "  {{placeholder}}, or a value a prior step failed to capture.\n"
            "- \"uncertain\": not enough evidence.\n\n"
            "Key rule: a failure at an auth/setup step (e.g. the FIRST step is a login that returns 401,\n"
            "with no register before it) is almost always a broken_test, NOT an app bug.\n\n"
            "For each failure you are given the intended HTTP steps and what actually happened per step.\n\n"
            "FAILURES:\n" + json.dumps(evidence, ensure_ascii=False, indent=2) + "\n\n"
            "Return ONLY a JSON array, one object per failure, matching by exact title:\n"
            "[{\"title\": \"...\", \"classification\": \"real_bug|broken_test|uncertain\", "
            "\"reason\": \"one sentence\", \"suggestion\": \"how to fix the test or reproduce the bug\"}]\n"
            "First character [, last character ]. No markdown, no prose."
        )

    @staticmethod
    def _parse_json_array(text: str) -> list:
        """Extract a JSON array from the response."""
        if not text:
            return []
        cleaned = text.strip()
        candidates = [cleaned]
        match = re.search(r"\[[\s\S]*\]", cleaned)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                obj = json.loads(candidate)
                if isinstance(obj, list):
                    return obj
            except json.JSONDecodeError:
                continue
        logger.warning("CriticService: could not parse JSON — first 200 chars: %s", cleaned[:200])
        return []
