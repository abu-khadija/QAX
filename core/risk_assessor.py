"""
risk_assessor.py — File Risk Assessment and Test Type Detection
================================================================
B2: Assesses the risk level of a source file based on content patterns
and git history. Drives model selection and prompt depth.

B3: Detects the most appropriate test type (regression, feature, smoke, uat)
based on file context, git history, and user story presence.

Usage:
    assessor = RiskAssessor()

    # B2 — risk assessment
    risk = assessor.assess_file("src/auth/login.cs", code_content, git_history)
    # → {"risk_level": "critical", "test_priority": "P1", ...}

    # B3 — test type detection
    test_type = assessor.detect_test_type(
        "src/auth/login.cs", code_content, git_history, user_story
    )
    # → "regression" | "feature" | "smoke" | "uat"
"""

import logging
import re

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Risk pattern definitions ──────────────────────────────────────────────────
CRITICAL_PATTERNS = [
    "auth", "login", "password", "token",
    "payment", "checkout", "billing", "invoice",
    "permission", "role", "access", "security",
    "delete", "remove", "purge", "reset",
]

LOW_RISK_PATTERNS = [
    "label", "text", "color", "style",
    "tooltip", "placeholder", "icon",
    "display", "show", "hide", "layout",
]

# ── Functional code indicators (used to distinguish pure UI from logic) ───────
FUNCTIONAL_INDICATORS = [
    "function", "method", "class", "return", "if", "else",
    "try", "catch", "async", "await", "throw", "raise",
    "exception", "validate", "check", "assert",
]

# ── Mapping tables ─────────────────────────────────────────────────────────────
_PRIORITY_MAP = {"critical": "P1", "high": "P2", "medium": "P3", "low": "P4"}
_DEPTH_MAP = {
    "critical": "deep",
    "high": "deep",
    "medium": "standard",
    "low": "light",
}


class RiskAssessor:
    """
    Assesses source file risk and recommends a testing strategy.

    Combines content pattern scanning with git history analysis
    to produce a risk profile that drives model selection and
    prompt depth in the test generation pipeline.

    Risk levels:
        critical — auth/payment/permission logic detected
        high     — high git bug-fix history (from GitAnalyzer)
        medium   — moderate complexity or mixed patterns
        low      — pure UI/display code with no functional logic
    """

    # ─────────────────────────────────────────────────────────────────────────
    # B2 — Risk Assessment
    # ─────────────────────────────────────────────────────────────────────────

    def assess_file(
        self,
        file_path: str,
        file_content: str,
        git_history: dict | None = None,
    ) -> dict:
        """
        Assess the risk level of a source file.

        Risk determination logic (priority order):
          1. Any CRITICAL_PATTERNS match in content → risk=critical
          2. git_history risk_level==high → risk=high (minimum)
          3. git_history risk_level==medium → risk=medium
          4. Only LOW_RISK_PATTERNS and no functional code → risk=low
          5. Default → risk=medium

        Args:
            file_path (str): Relative path to the source file (for logging).
            file_content (str): Full text content of the source file.
            git_history (dict | None): Optional result from
                GitAnalyzer.get_file_history(). Used to check risk_level
                and bug_fix_count fields.

        Returns:
            dict: {
                "risk_level": "critical"|"high"|"medium"|"low",
                "risk_factors": list[str],   # human-readable explanations
                "test_priority": "P1"|"P2"|"P3"|"P4",
                "recommended_depth": "deep"|"standard"|"light",
                "critical_areas": list[str], # matched critical patterns
            }
        """
        content_lower = file_content.lower()
        risk_factors: list[str] = []
        critical_areas: list[str] = []

        # ── Step 1: Scan for critical patterns ────────────────────────────
        found_critical = [
            p for p in CRITICAL_PATTERNS
            if re.search(r"\b" + re.escape(p) + r"\b", content_lower)
        ]

        # ── Step 2: Scan for low-risk patterns ────────────────────────────
        found_low = [
            p for p in LOW_RISK_PATTERNS
            if re.search(r"\b" + re.escape(p) + r"\b", content_lower)
        ]

        # ── Determine risk level ───────────────────────────────────────────
        if found_critical:
            risk_level = "critical"
            risk_factors.append(
                f"Critical patterns detected: {', '.join(found_critical[:5])}"
            )
            critical_areas = found_critical[:5]

        elif git_history and git_history.get("risk_level") == "high":
            risk_level = "high"
            bug_count = git_history.get("bug_fix_count", 0)
            risk_factors.append(
                f"High git risk: {bug_count} bug-fix commit(s) in history"
            )
            critical_areas = ["bug-prone logic", "regression paths"]

        elif git_history and git_history.get("risk_level") == "medium":
            risk_level = "medium"
            risk_factors.append(
                "Medium git risk: moderate bug-fix history"
            )

        elif found_low and not found_critical:
            # Check if the file also contains functional logic
            has_functional = any(
                re.search(r"\b" + re.escape(p) + r"\b", content_lower)
                for p in FUNCTIONAL_INDICATORS
            )
            if not has_functional:
                risk_level = "low"
                risk_factors.append(
                    f"Display/UI patterns only: {', '.join(found_low[:3])}"
                )
            else:
                risk_level = "medium"
                risk_factors.append("Mixed UI and functional code")

        else:
            risk_level = "medium"
            risk_factors.append("Standard file — no specific risk indicators")

        result = {
            "risk_level": risk_level,
            "risk_factors": risk_factors,
            "test_priority": _PRIORITY_MAP[risk_level],
            "recommended_depth": _DEPTH_MAP[risk_level],
            "critical_areas": critical_areas,
        }

        logger.info(
            "QAX RiskAssessor: %s → risk=%s (%s), depth=%s, factors=%s",
            file_path,
            risk_level.upper(),
            result["test_priority"],
            result["recommended_depth"],
            "; ".join(risk_factors),
        )
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # B3 — Test Type Detection
    # ─────────────────────────────────────────────────────────────────────────

    def detect_test_type(
        self,
        file_path: str,
        file_content: str,
        git_history: dict | None = None,
        user_story: str | None = None,
    ) -> str:
        """
        Detect the most appropriate test type for a source file.

        Detection logic (priority order):
          1. user_story provided → "uat" if story mentions user/customer,
             otherwise "feature"
          2. git_history has bug-fix commits AND no user_story → "regression"
          3. risk_level is "critical" → "smoke"
          4. Default → "feature"

        Type meanings:
            regression — focus on what used to work; driven by bug history
            feature    — focus on new behavior
            smoke      — critical path only; keep short
            uat        — write in business language; no technical selectors

        Args:
            file_path (str): Relative path to the source file (for logging).
            file_content (str): Full text content of the source file.
            git_history (dict | None): Optional git history from GitAnalyzer.
            user_story (str | None): Optional user story or acceptance criteria.

        Returns:
            str: One of "regression", "feature", "smoke", "uat".
        """
        # ── Rule 1: User story → feature or uat ───────────────────────────
        if user_story and user_story.strip():
            story_lower = user_story.lower()
            uat_keywords = ["user", "customer", "client", "end-user", "end user", "stakeholder"]
            if any(kw in story_lower for kw in uat_keywords):
                test_type = "uat"
            else:
                test_type = "feature"
            logger.info(
                "QAX RiskAssessor: test type '%s' (user story provided for %s)",
                test_type, file_path,
            )
            return test_type

        # ── Rule 2: Bug-fix history without user story → regression ───────
        if git_history:
            bug_fix_count = git_history.get("bug_fix_count", 0)
            if bug_fix_count > 0:
                logger.info(
                    "QAX RiskAssessor: test type 'regression' "
                    "(%d bug-fix commits in %s)",
                    bug_fix_count, file_path,
                )
                return "regression"

        # ── Rule 3: Critical risk → smoke ─────────────────────────────────
        # Note: assess_file() is safe to call again; it is fast (no I/O)
        risk = self.assess_file(file_path, file_content, git_history)
        if risk["risk_level"] == "critical":
            logger.info(
                "QAX RiskAssessor: test type 'smoke' (critical risk in %s)",
                file_path,
            )
            return "smoke"

        # ── Rule 4: Default → feature ──────────────────────────────────────
        logger.info(
            "QAX RiskAssessor: test type 'feature' (default for %s)", file_path
        )
        return "feature"
