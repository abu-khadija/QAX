"""
services/investigator_service.py — InvestigatorService (the second agent behavior)
==================================================================================
"Verify before you file." The Critic classifies a failure as a suspected bug from
the test's intended steps; the Investigator then goes to the LIVE app and tries to
REPRODUCE it, and only calls it confirmed when the evidence holds up.

Why this exists: on TaskFlow the Critic flagged a "SQL-injection accepted" bug that
was a false alarm — the payload was a validly-formatted email the app correctly
stored (verified by a real request). The Critic never saw the request body. The
Investigator re-runs the failing test against the app, captures the ACTUAL request
and response, and re-judges with that evidence — biased toward "not_reproduced",
because a false alarm costs more trust than a missed edge case.

Approach (hybrid, like the Critic): deterministic reproduction first — re-run each
suspected bug N times; a failure that does not recur consistently is downgraded
without spending a model call. Only the failures that reproduce deterministically
go to one Claude judgement over the real evidence (intended request + actual
response). Never prints, never calls sys.exit(); errors are captured in the result.

Usage:
    from services.investigator_service import InvestigatorService
    result = InvestigatorService(ctx).investigate(critic_result, test_cases,
                                                   base_url="http://localhost:8000")
    print(result.summary())     # "1 confirmed, 1 not reproduced, 0 inconclusive"
"""

from __future__ import annotations

import json
import logging
import re

from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import CriticResult, InvestigationResult, InvestigationVerdict

logger = logging.getLogger(__name__)


class InvestigatorService:
    """
    Reproduce each suspected bug on the live app and confirm or downgrade it.

    Deterministic reproduction (re-run N times) filters flaky/non-reproducing
    failures for free; one Claude call judges the ones that reproduce.
    """

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Config/credentials. Built from the
                environment when None.
        """
        self.context = context or QAXContext.from_env()

    def investigate(
        self,
        critic: CriticResult | list,
        test_cases: list[dict],
        base_url: str | None,
        *,
        runs: int = 2,
        progress: ProgressReporter | None = None,
    ) -> InvestigationResult:
        """
        Verify every suspected real bug by reproducing it against the live app.

        Args:
            critic (CriticResult | list): The Critic's result, or a list of the
                suspected-bug verdicts to verify.
            test_cases (list[dict]): The generated test cases (for their api_steps).
            base_url (str | None): The running app to reproduce against. Without
                it nothing can be reproduced and every candidate is inconclusive.
            runs (int): How many times to re-run each candidate (>= 2 recommended).
            progress (ProgressReporter | None): Progress sink.

        Returns:
            InvestigationResult: Per-candidate verdicts. Never raises at runtime.

        Raises:
            QAXConfigError: If the Anthropic API key is missing and there is work
                that needs a judgement.
        """
        prog = progress or NullProgressReporter()

        candidates = critic.real_bugs if isinstance(critic, CriticResult) else list(critic or [])
        if not candidates:
            prog.emit("investigate", "ok", "no suspected bugs to verify")
            return InvestigationResult(verdicts=[])

        if not base_url:
            prog.emit("investigate", "info", "no live app URL — cannot reproduce")
            return InvestigationResult(
                verdicts=[self._inconclusive(c, "No running app URL to reproduce against.")
                          for c in candidates],
                note="Provide a base_url to verify suspected bugs against the live app.",
            )

        self.context.require_anthropic_key()
        prog.emit("investigate", "start", f"reproducing {len(candidates)} suspected bug(s)")

        tc_by_key = {}
        for tc in test_cases:
            tc_by_key[tc.get("test_case_id", "")] = tc
            tc_by_key[tc.get("title", "")] = tc

        reproduced: list[dict] = []   # evidence dicts for the model
        verdicts: list[InvestigationVerdict] = []

        for c in candidates:
            tc = tc_by_key.get(getattr(c, "test_case_id", "")) or tc_by_key.get(getattr(c, "title", ""))
            if not tc or not tc.get("api_steps"):
                verdicts.append(self._inconclusive(c, "No executable api_steps to reproduce."))
                continue

            rep = self._reproduce(tc, base_url, runs)
            if not rep["reproduced"]:
                verdicts.append(InvestigationVerdict(
                    test_case_id=getattr(c, "test_case_id", ""), title=getattr(c, "title", ""),
                    status="not_reproduced", reproduced=False,
                    reason=(f"Failure did not reproduce consistently "
                            f"({rep['fail_runs']}/{runs} runs failed) — treated as a false alarm."),
                    evidence=rep["evidence"],
                ))
            else:
                reproduced.append({
                    "title": getattr(c, "title", ""),
                    "test_case_id": getattr(c, "test_case_id", ""),
                    "critic_reason": getattr(c, "reason", ""),
                    "intended": rep["intended"],
                    "actual": rep["actual"],
                })

        # Judge only the deterministically-reproduced candidates.
        if reproduced:
            try:
                judged = self._judge(reproduced)
            except Exception as exc:
                logger.error("InvestigatorService judgement failed: %s", exc)
                prog.emit("investigate", "error", str(exc))
                # Reproduced but unjudged → inconclusive, not a false confirm.
                for r in reproduced:
                    verdicts.append(InvestigationVerdict(
                        test_case_id=r["test_case_id"], title=r["title"],
                        status="inconclusive", reproduced=True,
                        reason=f"Reproduced but could not be judged: {exc}",
                        evidence=self._evidence_str(r),
                    ))
                return InvestigationResult(verdicts=verdicts, error=str(exc))
            verdicts.extend(judged)

        prog.emit("investigate", "ok",
                  f"{sum(1 for v in verdicts if v.status == 'confirmed')} confirmed")
        return InvestigationResult(verdicts=verdicts)

    # ── Internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _inconclusive(candidate, reason: str) -> InvestigationVerdict:
        """Build an inconclusive verdict for a candidate."""
        return InvestigationVerdict(
            test_case_id=getattr(candidate, "test_case_id", ""),
            title=getattr(candidate, "title", ""),
            status="inconclusive", reproduced=False, reason=reason,
        )

    def _reproduce(self, test_case: dict, base_url: str, runs: int) -> dict:
        """
        Re-run one test's api_steps against the live app `runs` times.

        Returns a dict with:
            reproduced (bool): the SAME step failed with the SAME status every run.
            fail_runs (int): how many runs failed.
            intended (dict): the failing step's method/path/body/expected_status.
            actual (dict): the reproduced status + response snippet.
            evidence (str): a compact human string.
        """
        from services.execution_service import ExecutionService

        svc = ExecutionService(self.context)
        fail_steps: list[int] = []
        fail_codes: list[int | None] = []
        last_fail_result: dict = {}

        for _ in range(max(1, runs)):
            ex = svc.execute([test_case], mode="api", base_url=base_url)
            r = (ex.results or [{}])[0]
            steps = r.get("steps") or r.get("steps_results") or []
            fstep = next((s for s in steps if s.get("status") in ("FAIL", "ERROR")), None)
            if fstep is None:
                fail_steps.append(-1)          # this run passed
                fail_codes.append(None)
            else:
                fail_steps.append(fstep.get("step_number"))
                fail_codes.append(fstep.get("response_code"))
                last_fail_result = fstep

        fail_runs = sum(1 for s in fail_steps if s != -1)
        # Reproduced = every run failed at the same step with the same status.
        reproduced = (
            fail_runs == len(fail_steps)
            and len(set(fail_steps)) == 1
            and len(set(fail_codes)) == 1
        )

        intended = {}
        if last_fail_result:
            step_no = last_fail_result.get("step_number")
            api_step = next((s for s in (test_case.get("api_steps") or [])
                             if s.get("step_number") == step_no), {})
            intended = {k: api_step.get(k) for k in
                        ("step_number", "method", "path", "body", "expected_status", "use_auth")
                        if api_step.get(k) is not None}
        actual = {
            "response_code": last_fail_result.get("response_code"),
            "error": (last_fail_result.get("error") or "")[:220],
        }
        evidence = (f"intended {intended.get('method','?')} {intended.get('path','?')} "
                    f"expect {intended.get('expected_status','?')}; "
                    f"got {actual['response_code']} ({fail_runs}/{len(fail_steps)} runs failed)")
        return {"reproduced": reproduced, "fail_runs": fail_runs,
                "intended": intended, "actual": actual, "evidence": evidence}

    @staticmethod
    def _evidence_str(r: dict) -> str:
        """Compact evidence string for a reproduced candidate."""
        i, a = r.get("intended", {}), r.get("actual", {})
        return (f"intended {i.get('method','?')} {i.get('path','?')} "
                f"expect {i.get('expected_status','?')}; got {a.get('response_code','?')}")

    def _judge(self, reproduced: list[dict]) -> list[InvestigationVerdict]:
        """Ask Claude to confirm or downgrade each reproduced candidate."""
        import anthropic

        client = anthropic.Anthropic(api_key=self.context.anthropic_key)
        response = client.messages.create(
            model=self.context.model_heavy,
            max_tokens=1500,
            messages=[{"role": "user", "content": self._build_prompt(reproduced)}],
        )
        data = self._parse_json_array(response.content[0].text)

        by_title = {r["title"]: r for r in reproduced}
        verdicts: list[InvestigationVerdict] = []
        returned = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", ""))
            r = by_title.get(title)
            if not r:
                continue
            returned.add(title)
            status = str(item.get("status", "inconclusive")).strip().lower()
            if status not in ("confirmed", "not_reproduced"):
                status = "inconclusive"
            verdicts.append(InvestigationVerdict(
                test_case_id=r["test_case_id"], title=title,
                status=status, reproduced=True,
                reason=str(item.get("reason", ""))[:400],
                evidence=self._evidence_str(r),
            ))
        # Anything reproduced but unjudged → inconclusive (never silently confirmed).
        for r in reproduced:
            if r["title"] not in returned:
                verdicts.append(InvestigationVerdict(
                    test_case_id=r["test_case_id"], title=r["title"],
                    status="inconclusive", reproduced=True,
                    reason="Reproduced but no verdict returned.",
                    evidence=self._evidence_str(r),
                ))
        return verdicts

    @staticmethod
    def _build_prompt(reproduced: list[dict]) -> str:
        """Build the verify-before-file judgement prompt."""
        return (
            "You are a QA lead doing the final verify-before-file check. Each candidate below "
            "FAILED and then RE-FAILED consistently when reproduced against the LIVE app. Using the "
            "ACTUAL evidence, decide for EACH:\n"
            "- \"confirmed\": the application genuinely violated its contract or a universal convention "
            "for a valid, well-formed request (e.g. a created resource returned 200 not 201; a valid "
            "request returned 500; another user's data was returned; a persisted value was lost).\n"
            "- \"not_reproduced\": the app actually behaved correctly and the TEST's expectation was "
            "wrong. Common false alarms to catch here:\n"
            "  * a security/injection payload that is a validly-formatted value being stored and "
            "returning success is NOT a bug — a status code alone does not prove the query was altered;\n"
            "  * the test expected a rejection for input that is actually valid;\n"
            "  * the expected status was guessed and the actual status is correct per convention.\n"
            "Bias toward \"not_reproduced\" whenever the evidence is ambiguous — a false alarm costs "
            "more trust than a missed edge case. Look hard at the request body: if it is a legitimate "
            "value, accepting it is usually correct.\n\n"
            "CANDIDATES (intended request vs what actually happened):\n"
            + json.dumps(reproduced, ensure_ascii=False, indent=2) + "\n\n"
            "Return ONLY a JSON array, one object per candidate, matching by exact title:\n"
            "[{\"title\": \"...\", \"status\": \"confirmed|not_reproduced\", "
            "\"reason\": \"one sentence citing the evidence\"}]\n"
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
        logger.warning("InvestigatorService: could not parse JSON — first 200 chars: %s", cleaned[:200])
        return []
