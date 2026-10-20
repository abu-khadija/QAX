"""
services/detector_service.py — DetectorService (Phase 1: knowledge + differential)
==================================================================================
Turns the AppModel into a list of SUSPECTED defects by scanning it against the
failure-mode knowledge base — so bug-finding is a method, not a lucky
comprehension call. This is where the user's craft finally enters QAX's brain.

Two layers (the hybrid pattern used across QAX):
  1. Deterministic detectors — crisp structural rules over the model, no model
     call: hardcoded secrets, the differential "a field lacks a constraint its
     peers have", and frontend/backend contract mismatches.
  2. A knowledge-guided pass — one model call over the AppModel + the pattern
     catalog, for the semantic patterns (crypto limits, IDOR, off-by-one, ...).

Output is candidate Findings, not confirmed bugs — later phases (red-team, probes,
Investigator) test and prove them. Never prints, never calls sys.exit().

Usage:
    from services.detector_service import DetectorService
    result = DetectorService(ctx).detect(app_model)
    print(result.summary())        # "6 suspect(s): 1×P1, 2×P2, 3×P3"
"""

from __future__ import annotations

import json
import logging
import re

from core import failure_patterns
from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import AppModel, DetectionResult, Finding

logger = logging.getLogger(__name__)

_SEV_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


class DetectorService:
    """Scan an AppModel against the failure-mode knowledge base."""

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Config/credentials. Built from the
                environment when None.
        """
        self.context = context or QAXContext.from_env()

    def detect(
        self,
        model: AppModel,
        *,
        use_knowledge_pass: bool = True,
        progress: ProgressReporter | None = None,
    ) -> DetectionResult:
        """
        Produce suspected defects for an AppModel.

        Args:
            model (AppModel): The whole-system model (from AppModelService).
            use_knowledge_pass (bool): Also run the one-call knowledge-guided pass.
            progress (ProgressReporter | None): Progress sink.

        Returns:
            DetectionResult: Candidate findings, most-severe first. Never raises at
                runtime (a failed knowledge pass still returns the static findings).

        Raises:
            QAXConfigError: If the knowledge pass is requested and the API key is
                missing.
        """
        prog = progress or NullProgressReporter()
        if not isinstance(model, AppModel) or not model.success:
            return DetectionResult(error="No valid AppModel to scan.")

        findings: list[Finding] = []
        findings += self._detect_hardcoded_secrets(model)
        findings += self._detect_missing_constraints(model)
        findings += self._detect_contract_mismatches(model)
        prog.emit("detect", "info", f"{len(findings)} static suspect(s)")

        if use_knowledge_pass:
            self.context.require_anthropic_key()
            try:
                findings += self._knowledge_pass(model)
            except Exception as exc:
                logger.error("DetectorService knowledge pass failed: %s", exc)
                prog.emit("detect", "error", f"knowledge pass: {exc}")

        findings = self._dedup(findings)
        findings.sort(key=lambda f: _SEV_ORDER.get(f.severity, 9))
        prog.emit("detect", "ok",
                  f"{sum(1 for f in findings if f.severity == 'P1')} critical suspect(s)")
        return DetectionResult(findings=findings)

    # ── Deterministic detectors ───────────────────────────────────────────────

    @staticmethod
    def _detect_hardcoded_secrets(model: AppModel) -> list[Finding]:
        """A secret config value with a hardcoded/placeholder default."""
        out = []
        for s in model.exposed_secrets:
            p = failure_patterns.get("hardcoded-secret")
            out.append(Finding(
                title=f"Hardcoded secret '{s.get('key')}' — {s.get('risk', 'forgeable')}",
                pattern="hardcoded-secret", severity=p.get("severity", "P1"),
                evidence=f"{s.get('key')} defaults to {str(s.get('default'))[:50]!r}",
                where=s.get("key", "config"), source="static", confidence="high"))
        return out

    @staticmethod
    def _detect_missing_constraints(model: AppModel) -> list[Finding]:
        """
        Differential: a string INPUT field with no length constraint while a sibling
        input field has one. Catches 'title accepts empty while name requires it'.
        """
        def is_str(t) -> bool:
            return "str" in str(t or "").lower()

        def has_len(c) -> bool:
            return "min_length" in str(c or "").lower() or "max_length" in str(c or "").lower()

        # Only reason about input DTOs (schemas ending in Create/Update).
        inputs = [e for e in model.entities
                  if isinstance(e, dict) and re.search(r"(create|update)$", str(e.get("name", "")), re.I)]
        peers_have = any(has_len(f.get("constraints"))
                         for e in inputs for f in (e.get("fields") or []) if is_str(f.get("type")))
        if not peers_have:
            return []

        out = []
        p = failure_patterns.get("missing-input-validation")
        for e in inputs:
            for f in (e.get("fields") or []):
                if is_str(f.get("type")) and not has_len(f.get("constraints")):
                    fname = f.get("name", "")
                    identifying = fname.lower() in ("title", "name", "email", "username", "slug")
                    out.append(Finding(
                        title=f"{e.get('name')}.{fname} has no length constraint (peers do)",
                        pattern="missing-input-validation", severity=p.get("severity", "P3"),
                        evidence=f"'{fname}' is a string input with no min/max_length while sibling "
                                 f"input fields are constrained",
                        where=f"{e.get('name')}.{fname}", source="static",
                        confidence="medium" if identifying else "low"))
        return out

    @staticmethod
    def _detect_contract_mismatches(model: AppModel) -> list[Finding]:
        """Frontend/backend disagreements captured in the model."""
        out = []
        p = failure_patterns.get("contract-mismatch")
        for c in model.contract_mismatches:
            out.append(Finding(
                title=f"Contract mismatch: {str(c.get('mismatch'))[:80]}",
                pattern="contract-mismatch", severity=p.get("severity", "P3"),
                evidence=f"{c.get('frontend_call')} vs {c.get('backend_endpoint')}",
                where=str(c.get("backend_endpoint") or "contract"),
                source="static", confidence="low"))
        return out

    # ── Knowledge-guided pass ─────────────────────────────────────────────────

    def _knowledge_pass(self, model: AppModel) -> list[Finding]:
        """One model call: apply the pattern catalog to the whole-system model."""
        import anthropic

        from core.ai_provider import make_ai_client
        client = make_ai_client(api_key=self.context.anthropic_key)
        prompt = self._build_prompt(model)
        response = client.messages.create(
            model=self.context.model_heavy, max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        data = self._parse_json_array(response.content[0].text)
        valid = {"P1", "P2", "P3", "P4"}
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            sev = str(item.get("severity", "P3")).strip().upper()
            if sev not in valid:
                sev = "P3"
            out.append(Finding(
                title=str(item.get("title", ""))[:160],
                pattern=str(item.get("pattern", ""))[:40],
                severity=sev,
                evidence=str(item.get("evidence", ""))[:300],
                where=str(item.get("where", ""))[:120],
                source="knowledge",
                confidence=str(item.get("confidence", "medium")).strip().lower()
                if str(item.get("confidence", "")).strip().lower() in ("low", "medium", "high") else "medium",
            ))
        return out

    @staticmethod
    def _build_prompt(model: AppModel) -> str:
        """Build the knowledge-guided detection prompt."""
        model_json = json.dumps({
            "endpoints": model.endpoints,
            "entities": model.entities,
            "auth": model.auth,
            "config": model.config,
            "risks": model.risks,
        }, ensure_ascii=False, indent=2)[:12000]
        return (
            "You are a senior security + QA reviewer. Below is a whole-system model of an application "
            "and a catalog of failure modes. Check the app against EACH pattern and report ONLY the "
            "ones with real evidence in THIS app. Reason across files (config vs schema vs security).\n\n"
            + failure_patterns.catalog_block() + "\n\n"
            "APP MODEL:\n" + model_json + "\n\n"
            "Return ONLY a JSON array of suspected findings:\n"
            "[{\"pattern\": \"<catalog id>\", \"title\": \"short specific description\", "
            "\"severity\": \"P1|P2|P3|P4\", \"where\": \"file/endpoint/field\", "
            "\"evidence\": \"the specific thing in this app\", \"confidence\": \"low|medium|high\"}]\n"
            "Only real, evidenced suspects — do not invent. First character [, last character ]. No prose."
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _dedup(findings: list[Finding]) -> list[Finding]:
        """Drop duplicate (pattern, where) findings, keeping the highest confidence."""
        rank = {"high": 0, "medium": 1, "low": 2}
        best: dict[tuple, Finding] = {}
        for f in findings:
            key = (f.pattern, f.where.lower())
            cur = best.get(key)
            if cur is None or rank.get(f.confidence, 3) < rank.get(cur.confidence, 3):
                best[key] = f
        return list(best.values())

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
        logger.warning("DetectorService: could not parse JSON — first 200 chars: %s", cleaned[:200])
        return []
