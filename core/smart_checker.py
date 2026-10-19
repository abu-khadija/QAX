"""
smart_checker.py — Smart Check Orchestrator
============================================
B5: Orchestrates all pre-generation checks in one unified call before
Claude is invoked. Combines cache lookup, risk assessment, test type
detection, skill library lookup, and optional Jira gap analysis.

The result dict from run_pre_checks() is passed into claude.analyze_code()
so the generation pipeline can use the right model, depth, and context.

Usage:
    checker = SmartChecker(project="example")
    checks = checker.run_pre_checks(
        file_path="src/ChecklistController.cs",
        file_content=code,
        module="Orders",
        git_history=git_history_dict,
        user_story="As a QA, I want to ...",
    )
    print(checks["pre_check_summary"])

    if checks["cache_hit"]:
        return checks["cached_result"]   # skip API call

    # Use checks in generation:
    #   model = checks["recommended_model"]
    #   skill_context = checks["skill_context"]
    #   risk = checks["risk"]
    #   test_type = checks["test_type"]
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from core.cache_manager import CacheManager
from core.feedback_store import FeedbackStore
from core.jira_gap_analyzer import JiraGapAnalyzer
from core.risk_assessor import RiskAssessor
from core.skill_library import SkillLibrary

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Model env vars ─────────────────────────────────────────────────────────────
from core.env_loader import load_env
load_env()
CLAUDE_MODEL_HEAVY = os.getenv("CLAUDE_MODEL_HEAVY", "claude-sonnet-4-5")
CLAUDE_MODEL_LIGHT = os.getenv("CLAUDE_MODEL_LIGHT", "claude-haiku-4-5-20251001")


class SmartChecker:
    """
    Orchestrates all pre-generation intelligence checks in one unified call.

    Run run_pre_checks() before calling claude.analyze_code() — it returns
    everything you need to drive the generation: model choice, prompt extras,
    and an early-exit flag when the cache has a fresh result.

    Components wired in:
        CacheManager      — file-level result cache
        JiraGapAnalyzer   — existing Xray test lookup (graceful if unconfigured)
        RiskAssessor      — content + git-based risk scoring
        SkillLibrary      — module-specific known patterns
        FeedbackStore     — (initialized for future use; not queried here)
    """

    def __init__(self, project: str = "") -> None:
        """
        Initialize the SmartChecker and all sub-components.

        All components are initialized defensively — failures are caught
        and logged so the pipeline never crashes.

        Args:
            project (str): Project name (e.g., "example"). Not used internally
                           yet but kept for future project-specific logic.
        """
        self.project = project or os.getenv("DEFAULT_PROJECT", "")

        # ── Initialize sub-components (all non-crashing) ───────────────────
        try:
            self.cache = CacheManager()
        except Exception as e:  # noqa: BLE001
            logger.warning("QAX SmartChecker: CacheManager init failed: %s", e)
            self.cache = None

        try:
            self.gap_analyzer = JiraGapAnalyzer()
        except Exception as e:  # noqa: BLE001
            logger.warning("QAX SmartChecker: JiraGapAnalyzer init failed: %s", e)
            self.gap_analyzer = None

        self.risk_assessor = RiskAssessor()

        try:
            self.skill_lib = SkillLibrary()
        except Exception as e:  # noqa: BLE001
            logger.warning("QAX SmartChecker: SkillLibrary init failed: %s", e)
            self.skill_lib = None

        try:
            self.feedback_store = FeedbackStore()
        except Exception as e:  # noqa: BLE001
            logger.warning("QAX SmartChecker: FeedbackStore init failed: %s", e)
            self.feedback_store = None

        logger.debug(
            "QAX SmartChecker initialized (project=%s, "
            "cache=%s, gap_analyzer=%s, skills=%s)",
            self.project,
            "✓" if self.cache else "✗",
            "✓" if (self.gap_analyzer and self.gap_analyzer._configured) else "✗",
            "✓" if self.skill_lib else "✗",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Main Orchestration Method
    # ─────────────────────────────────────────────────────────────────────────

    def run_pre_checks(
        self,
        file_path: str,
        file_content: str,
        module: str | None = None,
        git_history: dict | None = None,
        user_story: str | None = None,
    ) -> dict:
        """
        Run all pre-generation intelligence checks and return a unified result.

        Checks performed (in order):
          1. Cache check — return cached result if file unchanged
          2. Risk assessment — content + git risk scoring
          3. Test type detection — regression / feature / smoke / uat
          4. Skill library lookup — known patterns for this module
          5. Gap analysis (if module + Jira configured) — existing Xray tests

        Args:
            file_path (str): Relative path to the source file.
            file_content (str): Full text content of the source file.
            module (str | None): Module name for skill lookup and gap analysis.
            git_history (dict | None): Result from GitAnalyzer.get_file_history().
            user_story (str | None): User story or acceptance criteria text.

        Returns:
            dict: {
                "cache_hit": bool,
                "cached_result": list | None,     # populated if cache_hit
                "risk": dict,                     # from RiskAssessor.assess_file()
                "test_type": str,                 # "regression"|"feature"|"smoke"|"uat"
                "skill_context": str,             # prompt-ready skill text (or "")
                "existing_tests": list,           # from JiraGapAnalyzer (or [])
                "recommended_model": str,         # model ID string
                "pre_check_summary": str,         # human-readable summary line
            }
        """
        file_name = Path(file_path).name

        # ── 1. Cache check ─────────────────────────────────────────────────
        cache_hit = False
        cached_result = None
        if self.cache and file_path:
            try:
                cache_key = self.cache.get_key(file_path)
                if cache_key:
                    cached = self.cache.get(cache_key)
                    if cached is not None:
                        cache_hit = True
                        cached_result = cached
                        logger.info(
                            "QAX SmartChecker: Cache hit for %s (%d test case(s))",
                            file_name, len(cached),
                        )
            except Exception as e:  # noqa: BLE001
                logger.debug("QAX SmartChecker: Cache check failed (non-fatal): %s", e)

        # ── 2. Risk assessment ─────────────────────────────────────────────
        try:
            risk = self.risk_assessor.assess_file(
                file_path, file_content, git_history
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "QAX SmartChecker: Risk assessment failed (non-fatal): %s", e
            )
            risk = {
                "risk_level": "medium",
                "risk_factors": [],
                "test_priority": "P3",
                "recommended_depth": "standard",
                "critical_areas": [],
            }

        # ── 3. Test type detection ─────────────────────────────────────────
        try:
            test_type = self.risk_assessor.detect_test_type(
                file_path, file_content, git_history, user_story
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "QAX SmartChecker: Test type detection failed (non-fatal): %s", e
            )
            test_type = "feature"

        # ── 4. Skill library lookup ────────────────────────────────────────
        skill_context = ""
        if module and self.skill_lib:
            try:
                skill_context = self.skill_lib.get_context(module)
                if skill_context:
                    skill_count = self.skill_lib.skill_count(module)
                    logger.info(
                        "QAX SmartChecker: %d skill(s) loaded for module '%s'",
                        skill_count, module,
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "QAX SmartChecker: Skill library lookup failed (non-fatal): %s", e
                )

        # ── 5. Gap analysis (if module + Jira configured) ──────────────────
        existing_tests: list[dict] = []
        gap_status = ""
        if module and self.gap_analyzer and self.gap_analyzer._configured:
            try:
                existing_tests = self.gap_analyzer.fetch_existing_tests(module)
                if existing_tests:
                    gap_status = f"Xray: {len(existing_tests)} existing test(s) found"
                else:
                    gap_status = "Xray: no existing tests found"
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "QAX SmartChecker: Gap analysis failed (non-fatal): %s", e
                )

        # ── Model recommendation ───────────────────────────────────────────
        # critical or high risk → always use the heavy model
        recommended_model = self._recommend_model(
            risk_level=risk["risk_level"],
            line_count=len(file_content.splitlines()),
        )

        # ── Build pre-check summary ────────────────────────────────────────
        summary_parts = [
            f"File: {file_name}",
            f"Risk: {risk['risk_level'].upper()} ({risk['test_priority']})",
            f"Type: {test_type}",
            f"Model: {_short_model(recommended_model)}",
        ]
        if cache_hit:
            summary_parts.append("Cache: HIT — skipping API call")
        if skill_context:
            skill_count = self.skill_lib.skill_count(module) if (module and self.skill_lib) else 0
            summary_parts.append(f"Skills: {skill_count} known pattern(s) loaded")
        if gap_status:
            summary_parts.append(gap_status)

        pre_check_summary = " | ".join(summary_parts)

        return {
            "cache_hit": cache_hit,
            "cached_result": cached_result,
            "risk": risk,
            "test_type": test_type,
            "skill_context": skill_context,
            "existing_tests": existing_tests,
            "recommended_model": recommended_model,
            "pre_check_summary": pre_check_summary,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Private Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _recommend_model(self, risk_level: str, line_count: int) -> str:
        """
        Select the appropriate Claude model based on risk level and file size.

        Model selection rules:
          - critical or high risk → heavy model always (best reasoning)
          - line_count > 150 → heavy model (large file)
          - Otherwise → light model (saves ~80% cost)

        Args:
            risk_level (str): Risk level from RiskAssessor.
            line_count (int): Number of lines in the source file.

        Returns:
            str: Model ID string (CLAUDE_MODEL_HEAVY or CLAUDE_MODEL_LIGHT).
        """
        if risk_level in ("critical", "high"):
            return CLAUDE_MODEL_HEAVY
        if line_count > 150:
            return CLAUDE_MODEL_HEAVY
        return CLAUDE_MODEL_LIGHT


def _short_model(model_id: str) -> str:
    """
    Return a short display name for a model ID.

    Args:
        model_id (str): Full model ID string.

    Returns:
        str: Shortened name, e.g. "Sonnet" or "Haiku".
    """
    lower = model_id.lower()
    if "sonnet" in lower:
        return "Sonnet"
    if "haiku" in lower:
        return "Haiku"
    if "opus" in lower:
        return "Opus"
    return model_id.split("-")[0].title()
