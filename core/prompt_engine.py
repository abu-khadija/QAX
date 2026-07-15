"""
prompt_engine.py — Prompt Construction Utilities
==================================================
Helper utilities for building and customizing prompts.
The ClaudeClient handles the main system prompt injection,
but this module provides additional prompt building helpers
for specialized analysis scenarios (e.g., chunked analysis,
module-specific focus, re-analysis with context).
"""

import logging
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


def build_chunk_prompt(
    chunk: str,
    chunk_index: int,
    total_chunks: int,
    file_path: str,
    module_hint: str | None = None,
) -> str:
    """
    Build a user prompt for analyzing one chunk of a large file.

    When a file is too large for a single API call, it is split into chunks.
    This prompt tells Claude it is analyzing part of a larger file so it can
    maintain context and avoid generating duplicate test cases.

    Args:
        chunk (str): The code chunk to analyze.
        chunk_index (int): Current chunk number (1-based).
        total_chunks (int): Total number of chunks in the file.
        file_path (str): Relative path to the source file.
        module_hint (str | None): EXAMPLE module hint if known.

    Returns:
        str: Formatted user prompt for this chunk.
    """
    module_context = (
        f"This file belongs to the **{module_hint}** module."
        if module_hint
        else "Determine the relevant module from the code context."
    )

    prompt = f"""Analyze this source code chunk from the target codebase.

**File Path:** {file_path}
**Chunk:** {chunk_index} of {total_chunks}
**Module Context:** {module_context}

**Important:** This is part {chunk_index} of {total_chunks} of the file.
Only generate test cases for functions/logic that are COMPLETE in this chunk.
Do not generate test cases for functions that appear to continue in another chunk.

**Code Chunk:**
```
{chunk}
```

Return ONLY raw JSON array. No markdown, no explanation.
Set generated_from_file to: {file_path}
"""
    return prompt


def build_reanalysis_prompt(
    code: str,
    file_path: str,
    previous_test_cases: list[dict],
    focus_area: str,
) -> str:
    """
    Build a prompt for re-analyzing a file with a specific focus area,
    taking into account test cases already generated.

    Use this when you want Claude to look deeper at a specific aspect
    (e.g., "focus on security checks only" or "look at error handling only").

    Args:
        code (str): The source code to re-analyze.
        file_path (str): Relative path to the source file.
        previous_test_cases (list[dict]): Test cases already generated for this file.
        focus_area (str): Description of what to focus on (e.g., "security", "error handling").

    Returns:
        str: Formatted user prompt for focused re-analysis.
    """
    # Summarize existing test case titles to avoid duplicates
    existing_titles = "\n".join(
        f"  - {tc.get('title', 'Unknown')}"
        for tc in previous_test_cases
    ) or "  (none yet)"

    prompt = f"""Re-analyze this source code with a specific focus on: **{focus_area}**

**File Path:** {file_path}
**Focus Area:** {focus_area}

**Already generated test cases (do NOT duplicate these):**
{existing_titles}

**Source Code:**
```
{code}
```

Generate ONLY new test cases that focus on **{focus_area}** and are NOT already covered above.
Return ONLY raw JSON array. No markdown, no explanation.
Set generated_from_file to: {file_path}
"""
    return prompt


def build_analysis_prompt(
    code: str,
    file_path: str,
    module_hint: str | None = None,
    git_history: dict | None = None,
    bug_patterns: str | None = None,
    user_story: str | None = None,
    risk_assessment: dict | None = None,
    test_type: str | None = None,
    skill_context: str | None = None,
) -> str:
    """
    Build a user prompt for code analysis, optionally enriched with git history,
    recurring bug patterns, and a user story / acceptance criteria.

    When git_history indicates a high-risk file (many bug-fix commits),
    Claude is explicitly instructed to generate extra regression and error
    path test cases beyond the normal estimate.

    When bug_patterns are provided (from BugFetcher.get_patterns_for_module()),
    Claude is asked to generate at least one test case per recurring pattern.

    When user_story is provided, Claude generates tests that specifically verify
    that the described story or acceptance criteria works correctly.

    When risk_assessment is provided (from RiskAssessor.assess_file()), a
    RISK ASSESSMENT section is injected so Claude knows the criticality level
    and adjusts test depth accordingly.

    When test_type is provided (from RiskAssessor.detect_test_type()), a
    TEST TYPE section is injected so Claude focuses on the right kind of tests
    (regression = what used to work, feature = new behavior, etc.).

    When skill_context is provided (from SkillLibrary.get_context()), it is
    injected before the code so Claude can reference known working patterns.

    Args:
        code (str): The source code to analyze.
        file_path (str): Relative path to the source file.
        module_hint (str | None): Module hint if known.
        git_history (dict | None): Result from GitAnalyzer.get_file_history().
            If None or risk_level is "low", no risk context is added.
        bug_patterns (str | None): Recurring bug patterns extracted by BugFetcher.
            When provided, these are injected as an extra instruction block.
            If None or empty string, no bug pattern section is added.
        user_story (str | None): User story or acceptance criteria text.
            When provided, Claude generates tests specifically verifying this story.
        risk_assessment (dict | None): Result from RiskAssessor.assess_file().
            When provided, adds a RISK ASSESSMENT section to the prompt.
        test_type (str | None): Detected test type from RiskAssessor.
            One of "regression", "feature", "smoke", "uat".
        skill_context (str | None): Known patterns from SkillLibrary.get_context().
            When provided, added before the code as example guidance.

    Returns:
        str: Formatted user prompt ready to send to Claude.
    """
    module_context = (
        f"This file belongs to the **{module_hint}** module."
        if module_hint
        else "Determine the relevant module from the code context."
    )

    # ── Build git risk section if history is available and relevant ───────────
    risk_section = ""
    extra_cases_instruction = ""

    if git_history and git_history.get("risk_level") in ("high", "medium"):
        risk_level = git_history["risk_level"]
        bug_count = git_history.get("bug_fix_count", 0)
        total = git_history.get("total_commits", 0)

        # Format recent bug-fix commits for context
        bug_commits = [
            c for c in git_history.get("commits", [])
            if any(k in c.get("message", "").lower()
                   for k in ["fix", "bug", "hotfix", "patch", "error"])
        ]
        commit_lines = "\n".join(
            f"  - [{c['date']}] {c['message']} ({c['hash']})"
            for c in bug_commits[:5]
        )

        risk_section = f"""
⚠️ **RISK ALERT — Git History Analysis:**
This file has **{bug_count} bug-fix commits** out of {total} recent commits.
**Risk Level: {risk_level.upper()}**

Recent bug-fix commits:
{commit_lines}

This means this file has a history of defects. You must:
- Generate test cases that specifically cover the scenarios these bugs likely involved
- Focus on regression testing (ensure old bugs don't return)
- Test error paths and edge cases more thoroughly than usual
"""
        if risk_level == "high":
            extra_cases_instruction = (
                "\n**Generate 2 EXTRA test cases** beyond your normal estimate, "
                "specifically focused on regression and error handling paths "
                "based on the bug history above."
            )

    # ── Build bug patterns section if available ───────────────────────────
    bug_patterns_section = ""
    bug_patterns_instruction = ""
    if bug_patterns and bug_patterns.strip():
        bug_patterns_section = f"""
🐛 **RECURRING BUG PATTERNS (from Jira resolved bugs for this module):**
The following patterns represent classes of bugs that have historically
occurred in this module. Generate at least one test case for each applicable pattern:

{bug_patterns.strip()}

These patterns were automatically extracted from resolved Jira bugs.
Prioritize these patterns over generic test categories.
"""
        bug_patterns_instruction = (
            "\n6. Specifically test for the recurring bug patterns listed above."
        )

    # ── User story section (A6) ───────────────────────────────────────────────
    user_story_section = ""
    user_story_instruction = ""
    if user_story and user_story.strip():
        user_story_section = (
            "\n**USER STORY / ACCEPTANCE CRITERIA:**\n"
            f"{user_story.strip()}\n\n"
            "Generate test cases that specifically verify this story is working correctly.\n"
        )
        user_story_instruction = "\n6. Specifically verify the user story / acceptance criteria above."

    # ── Risk assessment section (B2) ──────────────────────────────────────────
    risk_section_b2 = ""
    if risk_assessment and risk_assessment.get("risk_level"):
        risk_level = risk_assessment["risk_level"]
        priority = risk_assessment.get("test_priority", "P3")
        depth = risk_assessment.get("recommended_depth", "standard")
        areas = risk_assessment.get("critical_areas", [])
        areas_str = ", ".join(areas) if areas else "general logic"
        risk_section_b2 = (
            f"\n**RISK ASSESSMENT:**\n"
            f"Risk level: {risk_level.upper()} ({priority})\n"
            f"Critical areas: {areas_str}\n"
            f"Generate **{depth}** coverage.\n"
            f"Prioritize tests for: {areas_str}\n"
        )

    # ── Test type section (B3) ────────────────────────────────────────────────
    test_type_section = ""
    if test_type:
        type_guidance = {
            "regression": "focus on what used to work; cover known bug scenarios",
            "feature":    "focus on new behavior described in the code",
            "smoke":      "critical path only — keep it short and essential",
            "uat":        "write in business language; avoid technical selectors",
        }
        guidance = type_guidance.get(test_type, "")
        test_type_section = (
            f"\n**TEST TYPE: {test_type.upper()}**\n"
            f"{guidance}\n"
        )

    # ── Skill context section (B4) ────────────────────────────────────────────
    skill_context_section = ""
    if skill_context and skill_context.strip():
        skill_context_section = (
            f"\n**KNOWN PATTERNS (use these as examples):**\n"
            f"{skill_context.strip()}\n"
        )

    prompt = f"""Analyze the following source code file and generate comprehensive test cases.

**File Path:** {file_path}
**Module Context:** {module_context}
{user_story_section}{risk_section}{risk_section_b2}{test_type_section}{bug_patterns_section}{skill_context_section}
**Source Code:**
```
{code}
```

Generate test cases covering:
1. Happy path (successful execution)
2. Validation failures (missing/invalid inputs)
3. Permission/role boundary checks
4. Edge cases and boundary conditions
5. Error handling paths{bug_patterns_instruction}{user_story_instruction}
{extra_cases_instruction}

Return ONLY raw JSON array. No markdown, no explanation.
Set generated_from_file to: {file_path}
"""
    return prompt


def build_module_summary_prompt(module_name: str, generated_test_cases: list[dict]) -> str:
    """
    Build a prompt asking Claude to review a set of test cases for a module
    and identify coverage gaps.

    Args:
        module_name (str): The EXAMPLE module name (e.g., "Incident").
        generated_test_cases (list[dict]): All test cases generated for this module.

    Returns:
        str: Formatted prompt asking Claude for a coverage gap analysis.
    """
    test_summary = "\n".join(
        f"  [{tc.get('test_type', '?')}] {tc.get('title', 'Unknown')}"
        for tc in generated_test_cases
    )

    prompt = f"""Review the following test cases generated for the **{module_name}** module.

**Generated Test Cases:**
{test_summary}

Based on your domain knowledge and the project context provided,
identify any critical testing gaps — scenarios that should be tested but are missing.

Return a JSON array of ADDITIONAL test cases only. Do not re-generate existing ones.
Return ONLY raw JSON array. No markdown, no explanation.
"""
    return prompt
