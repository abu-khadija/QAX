"""
skill_library.py — Reusable Test Pattern Library
=================================================
B4: Stores known-good test patterns per module so Claude can reference
real examples from past successful runs, improving consistency over time.

Patterns are stored in config/skills.json as a simple JSON dict keyed by
module name. Each module has a list of patterns with usage counts so the
most useful patterns surface to the top.

Usage:
    lib = SkillLibrary()

    # Get context for a module (injected into prompts)
    context = lib.get_context("Orders")

    # Add a new skill after accepting a test case
    lib.add_skill("Orders", "permission check", "always test 403 response")

    # Increment use count when a pattern is reused
    lib.increment_use("Orders", "permission check")
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Default skills file location ──────────────────────────────────────────────
from core.paths import user_config_dir  # noqa: E402 — written to (learned patterns): never site-packages
DEFAULT_SKILLS_PATH = user_config_dir() / "skills.json"

# ── How many top skills to include in the prompt context ──────────────────────
TOP_SKILLS_LIMIT = 5


class SkillLibrary:
    """
    JSON-backed library of reusable test patterns per module.

    Patterns are weighted by usage count — the most-used patterns
    appear first in the prompt context injected into Claude.

    Skills file format (config/skills.json):
    {
        "Orders": [
            {
                "pattern": "permission check",
                "solution": "always test 403 response for unauthorized roles",
                "uses": 7,
                "added_at": "2025-01-15T10:30:00Z"
            }
        ],
        "Reports": [ ... ]
    }
    """

    def __init__(self, path: str | None = None) -> None:
        """
        Initialize the skill library and load the JSON file.

        Creates an empty skills.json if the file does not exist.

        Args:
            path (str | None): Custom path to the skills JSON file.
                               Defaults to config/skills.json.
        """
        self.path = Path(path) if path else DEFAULT_SKILLS_PATH
        self._data: dict[str, list[dict]] = {}
        self._load()

    # ─────────────────────────────────────────────────────────────────────────
    # Load / Save
    # ─────────────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """
        Load skills from the JSON file.

        Creates an empty file if it does not exist. Logs a warning and
        continues with an empty dict if the file cannot be parsed.
        """
        if not self.path.exists():
            # Create empty skills file
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._data = {}
            self._save()
            logger.debug("QAX SkillLibrary: Created empty skills file at %s", self.path)
            return

        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self._data = loaded
            else:
                logger.warning(
                    "QAX SkillLibrary: Unexpected JSON structure in %s — "
                    "using empty library",
                    self.path,
                )
                self._data = {}
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(
                "QAX SkillLibrary: Cannot read %s (%s) — using empty library",
                self.path, e,
            )
            self._data = {}

    def _save(self) -> None:
        """
        Persist the current skills data to the JSON file.

        Logs a warning on failure but never raises — saving is non-fatal.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.warning(
                "QAX SkillLibrary: Could not save skills file: %s", e
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def get_context(self, module: str) -> str:
        """
        Return the top-used skill patterns for a module as formatted text.

        Returns the top N patterns sorted by usage count (highest first).
        The returned string is ready to inject directly into a Claude prompt.

        Args:
            module (str): Module name (e.g., "Orders").

        Returns:
            str: Formatted skill context string, or "" if no skills exist yet.

        Example output:
            "Known working patterns for Orders:
             - permission check: always test 403 response for unauthorized roles
             - null input: always include a test for empty/null required fields"
        """
        skills = self._data.get(module, [])
        if not skills:
            return ""

        # Sort by usage count, highest first, then take top N
        sorted_skills = sorted(
            skills, key=lambda s: s.get("uses", 0), reverse=True
        )[:TOP_SKILLS_LIMIT]

        lines = [f"Known working patterns for {module}:"]
        for s in sorted_skills:
            pattern = s.get("pattern", "")
            solution = s.get("solution", "")
            uses = s.get("uses", 0)
            lines.append(f"  - {pattern}: {solution}  (used {uses}x)")

        context = "\n".join(lines)
        logger.debug(
            "QAX SkillLibrary: Loaded %d skill(s) for module '%s'",
            len(sorted_skills), module,
        )
        return context

    def add_skill(
        self,
        module: str,
        pattern: str,
        solution: str,
    ) -> None:
        """
        Add a new reusable pattern to the library for a module.

        If the exact pattern already exists for this module, it is NOT
        duplicated — the existing entry's solution is updated instead.

        Args:
            module (str): Module name (e.g., "Orders").
            pattern (str): Short name for the pattern
                           (e.g., "permission check", "null input").
            solution (str): Guidance note for how to test this pattern
                            (e.g., "always test 403 response").
        """
        if module not in self._data:
            self._data[module] = []

        # Check if pattern already exists — update instead of duplicating
        for existing in self._data[module]:
            if existing.get("pattern", "").lower() == pattern.lower():
                existing["solution"] = solution
                self._save()
                logger.info(
                    "QAX SkillLibrary: Updated skill '%s' in module '%s'",
                    pattern, module,
                )
                return

        # New pattern — append
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._data[module].append({
            "pattern": pattern,
            "solution": solution,
            "uses": 1,
            "added_at": now,
        })
        self._save()
        logger.info(
            "QAX SkillLibrary: Added skill '%s' to module '%s'",
            pattern, module,
        )

    def increment_use(self, module: str, pattern: str) -> None:
        """
        Increment the usage count for a pattern in a module.

        No-op if the pattern does not exist for this module.

        Args:
            module (str): Module name.
            pattern (str): Pattern name to increment.
        """
        skills = self._data.get(module, [])
        for skill in skills:
            if skill.get("pattern", "").lower() == pattern.lower():
                skill["uses"] = skill.get("uses", 0) + 1
                self._save()
                logger.debug(
                    "QAX SkillLibrary: Incremented '%s' in '%s' (uses=%d)",
                    pattern, module, skill["uses"],
                )
                return

        logger.debug(
            "QAX SkillLibrary: Pattern '%s' not found in module '%s' — "
            "nothing incremented",
            pattern, module,
        )

    def list_modules(self) -> list[str]:
        """
        Return a list of all module names that have skills.

        Returns:
            list[str]: Module names sorted alphabetically.
        """
        return sorted(self._data.keys())

    def skill_count(self, module: str | None = None) -> int:
        """
        Count skills in the library.

        Args:
            module (str | None): If given, count only for that module.
                                 If None, count all skills across all modules.

        Returns:
            int: Number of skill entries.
        """
        if module is not None:
            return len(self._data.get(module, []))
        return sum(len(skills) for skills in self._data.values())
