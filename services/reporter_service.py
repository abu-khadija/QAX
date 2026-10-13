"""
services/reporter_service.py — ReporterService (the third agent behavior)
=========================================================================
Turns CONFIRMED bugs into professional, de-duplicated bug reports. A pipeline
files one ticket per failing test — so the same defect surfaces five times. A QA
lead files it ONCE, grouped by root cause, with severity, evidence, reproduction
and impact. That is what this does.

On TaskFlow the Critic/Investigator confirmed "5 bugs" that were one defect (a
create returning 200) hit by five tests. The Reporter folds those into a single
report that says "surfaced by 5 tests", assigns a severity, and writes it up in
the shape the user files bugs in.

One Claude call groups the confirmed verdicts into root-cause families and writes
each report over the real evidence the Investigator captured. Never prints, never
calls sys.exit(); failures are captured in BugReportResult.error.

Usage:
    from services.reporter_service import ReporterService
    report = ReporterService(ctx).report(investigation_result)
    print(report.summary())          # "1 bug from 5 failing test(s)"
    print(report.to_markdown())
"""

from __future__ import annotations

import json
import logging
import re

from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import BugReportEntry, BugReportResult, InvestigationResult

logger = logging.getLogger(__name__)

_VALID_SEVERITY = {"P1", "P2", "P3", "P4"}


class ReporterService:
    """Group confirmed bugs by root cause and write professional reports."""

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Config/credentials. Built from the
                environment when None.
        """
        self.context = context or QAXContext.from_env()

    def report(
        self,
        investigation: InvestigationResult | list,
        *,
        progress: ProgressReporter | None = None,
    ) -> BugReportResult:
        """
        Group confirmed bugs into root-cause families and write each up.

        Args:
            investigation (InvestigationResult | list): The Investigator's result,
                or a list of the confirmed verdicts to report.
            progress (ProgressReporter | None): Progress sink.

        Returns:
            BugReportResult: One entry per root-cause family. Never raises at
                runtime.

        Raises:
            QAXConfigError: If the Anthropic API key is missing and there is work.
        """
        prog = progress or NullProgressReporter()

        confirmed = (investigation.confirmed
                     if isinstance(investigation, InvestigationResult)
                     else list(investigation or []))
        if not confirmed:
            prog.emit("report", "ok", "no confirmed bugs to report")
            return BugReportResult(entries=[])

        self.context.require_anthropic_key()
        prog.emit("report", "start", f"writing report for {len(confirmed)} confirmed bug(s)")

        evidence = [{
            "title": getattr(v, "title", ""),
            "reason": getattr(v, "reason", ""),
            "evidence": getattr(v, "evidence", ""),
        } for v in confirmed]

        try:
            entries = self._write(evidence)
        except Exception as exc:
            logger.error("ReporterService failed: %s", exc)
            prog.emit("report", "error", str(exc))
            return BugReportResult(error=str(exc))

        # Guarantee nothing is dropped: any confirmed bug not covered by a family
        # becomes its own minimal entry.
        covered = {o for e in entries for o in e.occurrences}
        for ev in evidence:
            if ev["title"] not in covered:
                entries.append(BugReportEntry(
                    title=ev["title"], severity="P3",
                    description=ev["reason"], actual=ev["evidence"],
                    occurrences=[ev["title"]],
                ))

        prog.emit("report", "ok", f"{len(entries)} report(s)")
        return BugReportResult(entries=entries)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _write(self, evidence: list[dict]) -> list[BugReportEntry]:
        """One Claude call: group by root cause and write each report."""
        import anthropic

        client = anthropic.Anthropic(api_key=self.context.anthropic_key)
        response = client.messages.create(
            model=self.context.model_heavy,
            max_tokens=3000,
            messages=[{"role": "user", "content": self._build_prompt(evidence)}],
        )
        data = self._parse_json_array(response.content[0].text)

        titles = {e["title"] for e in evidence}
        entries: list[BugReportEntry] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            sev = str(item.get("severity", "P3")).strip().upper()
            if sev not in _VALID_SEVERITY:
                sev = "P3"
            occ = [str(o) for o in (item.get("occurrences") or []) if str(o) in titles]
            steps = [str(s) for s in (item.get("steps_to_reproduce") or [])]
            entries.append(BugReportEntry(
                title=str(item.get("title", ""))[:160],
                severity=sev,
                severity_reason=str(item.get("severity_reason", ""))[:200],
                description=str(item.get("description", ""))[:600],
                steps_to_reproduce=steps[:12],
                expected=str(item.get("expected", ""))[:300],
                actual=str(item.get("actual", ""))[:300],
                root_cause=str(item.get("root_cause", ""))[:400],
                impact=str(item.get("impact", ""))[:300],
                occurrences=occ,
            ))
        return entries

    @staticmethod
    def _build_prompt(evidence: list[dict]) -> str:
        """Build the grouping + report-writing prompt."""
        return (
            "You are a QA lead writing bug reports. Below are CONFIRMED bugs (each "
            "reproduced on the live app). Do TWO things:\n"
            "1. GROUP them into root-cause families — several tests that fail for the SAME "
            "underlying defect become ONE report. Do not file the same defect more than once.\n"
            "2. Write each family as a professional report.\n\n"
            "Severity model:\n"
            "- P1 Critical: workflow failure, data loss, or a resource made inaccessible.\n"
            "- P2 High: feature broken (workaround exists) or wrong-but-detectable data.\n"
            "- P3 Medium: unexpected behaviour, minor data/display issue, contract violation.\n"
            "- P4 Low: cosmetic, no functional impact.\n\n"
            "CONFIRMED BUGS (title, why it's a bug, evidence):\n"
            + json.dumps(evidence, ensure_ascii=False, indent=2) + "\n\n"
            "Return ONLY a JSON array, one object per root-cause family:\n"
            "[{\"title\": \"[Area] symptom — decisive detail\", "
            "\"severity\": \"P1|P2|P3|P4\", \"severity_reason\": \"one line\", "
            "\"description\": \"what happens and why it matters\", "
            "\"steps_to_reproduce\": [\"step 1\", \"step 2\"], "
            "\"expected\": \"...\", \"actual\": \"...\", "
            "\"root_cause\": \"mechanism-level hypothesis\", "
            "\"impact\": \"who/what is affected\", "
            "\"occurrences\": [\"exact title of each confirmed bug in this family\"]}]\n"
            "Every confirmed bug title must appear in exactly one family's occurrences. "
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
        logger.warning("ReporterService: could not parse JSON — first 200 chars: %s", cleaned[:200])
        return []
