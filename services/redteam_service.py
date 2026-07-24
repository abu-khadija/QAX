"""
services/redteam_service.py — RedTeamService (Phase 2: adversarial generation)
==============================================================================
QAX tested what an endpoint is SUPPOSED to do. A real tester also asks "how would
I break in?" — and that abuse-case mindset is where the worst bugs surface. This
turns the DetectorService's suspects into EXECUTABLE adversarial tests that try to
exploit them, each written so it FAILS when the app is vulnerable.

It covers the attacks expressible over HTTP — broken object authorization (IDOR),
mass assignment / over-posting, auth bypass, and validation abuse — using named
auth tokens so one test can hold two users at once (userA vs userB), the thing the
single-token api_steps could never do. Attacks that need a special tool (forging a
JWT with a leaked secret) are emitted as marked intents for the probe phase.

Output is standard QAX test cases, so they flow straight into execute → Critic →
Investigator → Reporter. Never prints, never calls sys.exit().

Usage:
    from services.redteam_service import RedTeamService
    result = RedTeamService(ctx).generate(app_model, detection_result)
    execution = ExecutionService(ctx).execute(result.test_cases, mode="api", base_url=...)
"""

from __future__ import annotations

import json
import logging
import re

from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import AppModel, DetectionResult, GenerationResult

logger = logging.getLogger(__name__)


class RedTeamService:
    """Generate executable adversarial (abuse-case) tests from the model + suspects."""

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Config/credentials. Built from the
                environment when None.
        """
        self.context = context or QAXContext.from_env()

    def generate(
        self,
        model: AppModel,
        detection: DetectionResult | list | None = None,
        *,
        progress: ProgressReporter | None = None,
    ) -> GenerationResult:
        """
        Generate adversarial test cases that attempt to exploit the app's weaknesses.

        Args:
            model (AppModel): The whole-system model.
            detection (DetectionResult | list | None): Suspected findings to target
                (uses all of them if a DetectionResult is given).
            progress (ProgressReporter | None): Progress sink.

        Returns:
            GenerationResult: Adversarial test cases (module="red-team"). Never
                raises at runtime.

        Raises:
            QAXConfigError: If the Anthropic API key is missing.
        """
        prog = progress or NullProgressReporter()
        if not isinstance(model, AppModel) or not model.success:
            return GenerationResult(module="red-team", error="No valid AppModel to attack.")

        self.context.require_anthropic_key()

        findings = (detection.findings if isinstance(detection, DetectionResult)
                    else list(detection or []))
        prog.emit("redteam", "start", f"writing attacks for {len(findings)} suspect(s)")

        try:
            cases = self._write(model, findings)
        except Exception as exc:
            logger.error("RedTeamService failed: %s", exc)
            prog.emit("redteam", "error", str(exc))
            return GenerationResult(module="red-team", error=str(exc))

        prog.emit("redteam", "ok", f"{len(cases)} attack(s)")
        return GenerationResult(test_cases=cases, module="red-team",
                                source="red-team", model=self.context.model_heavy)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _write(self, model: AppModel, findings: list) -> list[dict]:
        """One model call producing adversarial api_steps test cases."""
        import anthropic

        client = anthropic.Anthropic(api_key=self.context.anthropic_key)
        response = client.messages.create(
            model=self.context.model_heavy, max_tokens=8000,
            messages=[{"role": "user", "content": self._build_prompt(model, findings)}],
        )
        data = self._parse_json_array(response.content[0].text)

        cases: list[dict] = []
        for i, item in enumerate(data, 1):
            if not isinstance(item, dict):
                continue
            item.setdefault("test_case_id", f"RT-{i:04d}")
            item.setdefault("test_type", "security")
            item.setdefault("module", "red-team")
            item["attack"] = str(item.get("attack", "")) or "abuse-case"
            cases.append(item)
        return cases

    @staticmethod
    def _build_prompt(model: AppModel, findings: list) -> str:
        """Build the red-team generation prompt."""
        suspects = [{
            "pattern": getattr(f, "pattern", ""), "title": getattr(f, "title", ""),
            "where": getattr(f, "where", ""), "severity": getattr(f, "severity", ""),
        } for f in findings][:20]
        model_json = json.dumps({
            "endpoints": model.endpoints, "auth": model.auth,
            "exposed_secrets": model.exposed_secrets,
        }, ensure_ascii=False)[:8000]

        return (
            "You are a red-team API tester. Given a whole-system app model and a list of SUSPECTED "
            "weaknesses, write EXECUTABLE adversarial tests that try to EXPLOIT each. Each test must be "
            "written so that it FAILS when the app is actually vulnerable — i.e. expected_status is the "
            "SECURE response, and if the app responds insecurely the assertion fails.\n\n"
            "Use \"api_steps\" (real HTTP). Rules:\n"
            "- Self-contained: register/login every user the attack needs.\n"
            "- NAMED TOKENS for multi-user attacks: save one user's token with "
            "\"save\": {\"token_a\": \"access_token\"} and another with \"token_b\", then send a step as "
            "that user with \"use_auth\": \"token_a\" (or \"token_b\"). Use {{random}} for unique emails "
            "(a_{{random}}@t.com, b_{{random}}@t.com).\n"
            "- Capture ids with \"save\": {\"rid\": \"id\"} and reuse as {{rid}} in a later path.\n"
            "- Cover these attack classes where the suspects/endpoints allow:\n"
            "  * broken-object-authz (IDOR): userB tries to GET/PUT/DELETE userA's resource → expect "
            "403 or 404 (NOT 200).\n"
            "  * mass-assignment: create/update sending a protected field (user_id, id, order, another "
            "user's category_id) → the server must ignore or reject it.\n"
            "  * auth-bypass: call a protected endpoint with no token / a malformed token → expect 401.\n"
            "  * validation-abuse: empty/oversized/wrong-type input → expect 422/400 (NOT accepted).\n"
            "- For an attack that needs forging a token from a leaked/hardcoded secret, DO NOT fake it "
            "with api_steps; instead output the case with \"attack\": \"forge-jwt\" and a \"note\" "
            "describing the exploit (a later probe executes it).\n\n"
            "SUSPECTS:\n" + json.dumps(suspects, ensure_ascii=False, indent=2) + "\n\n"
            "APP MODEL:\n" + model_json + "\n\n"
            "Return ONLY a JSON array of test cases:\n"
            "[{\"title\": \"Attack: ...\", \"attack\": \"<class>\", \"module\": \"red-team\", "
            "\"api_steps\": [ ... ], \"note\": \"optional\"}]\n"
            "Keep to the ~8 highest-value attacks so the response is complete. "
            "First character [, last character ]. No markdown, no prose."
        )

    @staticmethod
    def _parse_json_array(text: str) -> list:
        """Extract a JSON array — tolerant of markdown fences and truncation."""
        if not text:
            return []
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned).rstrip("`").strip()
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
        # Salvage: the array was truncated mid-generation — recover complete objects.
        salvaged = RedTeamService._salvage_objects(cleaned)
        if salvaged:
            logger.warning("RedTeamService: salvaged %d object(s) from truncated JSON", len(salvaged))
            return salvaged
        logger.warning("RedTeamService: could not parse JSON — first 200 chars: %s", cleaned[:200])
        return []

    @staticmethod
    def _salvage_objects(text: str) -> list:
        """Recover complete top-level {...} objects from a truncated JSON array."""
        objects: list = []
        depth = 0
        start = None
        in_str = False
        esc = False
        for i, ch in enumerate(text):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        objects.append(json.loads(text[start:i + 1]))
                    except json.JSONDecodeError:
                        pass
                    start = None
        return objects
