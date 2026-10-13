"""
claude_client.py — Anthropic Claude API Client
================================================
Handles all communication with the Claude AI model.
Sends source code for analysis, receives structured JSON test cases,
parses and validates the response format, and retries on format failures.
"""

import json
import logging
import os
import re
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Path constants ────────────────────────────────────────────────────────────
CONFIG_DIR = Path(__file__).parent.parent / "config"
SYSTEM_PROMPT_PATH = CONFIG_DIR / "system_prompt.md"

# ── Model configuration ───────────────────────────────────────────────────────
CLAUDE_MODEL_HEAVY = os.getenv("CLAUDE_MODEL_HEAVY", "claude-sonnet-4-5")
CLAUDE_MODEL_LIGHT = os.getenv("CLAUDE_MODEL_LIGHT", "claude-haiku-4-5-20251001")
MODEL_ID = CLAUDE_MODEL_HEAVY   # kept for backward compat in retry/check-connection
# Output token ceiling. 4000 was too low for deep/critical coverage (5+ detailed
# test cases with manual + automation steps), causing mid-array truncation. 8000
# gives comfortable headroom; override with CLAUDE_MAX_TOKENS if needed.
MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "8000"))


class ClaudeClient:
    """
    Client for the Anthropic Claude API.

    Loads the system prompt (with EXAMPLE context injected), sends source code
    for analysis, and returns structured test case dictionaries.

    Usage:
        client = ClaudeClient()
        test_cases = client.analyze_code(
            code="def submit_incident(...):\n    ...",
            file_path="src/incidents/forms.py",
            module_hint="Incident"
        )
    """

    def __init__(self, project: str | None = None):
        """
        Initialize the Claude client.

        Loads ANTHROPIC_API_KEY from the .env file and initializes the
        Anthropic SDK client. Raises an error if the API key is missing.

        Args:
            project (str | None): Project name for loading context. If None,
                reads DEFAULT_PROJECT from env.

        Raises:
            ValueError: If ANTHROPIC_API_KEY is not set in the environment.
        """
        # Load environment variables from .env file.
        # override=True ensures .env values win over any empty shell env vars.
        load_dotenv(override=True)

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. "
                "Copy .env.example to .env and fill in your API key."
            )

        # Resolve project name
        self.project = project or os.getenv("DEFAULT_PROJECT", "")

        # Initialize the official Anthropic SDK client
        self.client = anthropic.Anthropic(api_key=api_key)

        # Load the system prompt once at startup (includes project context)
        self.system_prompt = self.load_system_prompt()

        logger.info("ClaudeClient initialized with model: %s, project: %s", MODEL_ID, self.project)

    # ─────────────────────────────────────────────────────────────────────────
    # System Prompt Loading
    # ─────────────────────────────────────────────────────────────────────────

    def load_system_prompt(self) -> str:
        """
        Load system_prompt.md and inject the project context into it.

        Reads config/system_prompt.md and replaces the [PROJECT_CONTEXT]
        placeholder with the full content of config/projects/{project}/context.md.
        Falls back to config/example-context.md for backwards compatibility.

        Returns:
            str: The complete system prompt with project context injected.

        Raises:
            FileNotFoundError: If either config file is missing.
        """
        if not SYSTEM_PROMPT_PATH.exists():
            raise FileNotFoundError(
                f"System prompt not found at: {SYSTEM_PROMPT_PATH}"
            )

        # Try new project-based path first, fall back to legacy path
        system_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        context_path = CONFIG_DIR / "projects" / self.project / "context.md"
        if context_path.exists():
            project_context = context_path.read_text(encoding="utf-8")
        else:
            legacy_path = CONFIG_DIR / "example-context.md"
            if legacy_path.exists():
                logger.warning(
                    "Project context not found at %s — using legacy path %s",
                    context_path, legacy_path
                )
                project_context = legacy_path.read_text(encoding="utf-8")
            elif not self.project:
                # No project configured at all (e.g. a generic scan or a virtual
                # endpoints:// source). QAX is GENERIC — degrade to an empty project
                # context instead of crashing, so generation still works.
                logger.warning(
                    "No project context configured — generating with an empty project context")
                project_context = ""
            else:
                # A NAMED project whose context is missing is a real misconfiguration.
                raise FileNotFoundError(
                    f"Project context file not found at: {context_path}"
                )

        # Replace the placeholder with actual project context
        final_prompt = system_prompt.replace("[PROJECT_CONTEXT]", project_context)

        logger.debug("System prompt loaded (%d chars) for project '%s'", len(final_prompt), self.project)
        return final_prompt

    # ─────────────────────────────────────────────────────────────────────────
    # Model Selection
    # ─────────────────────────────────────────────────────────────────────────

    def select_model(self, line_count: int, risk_level: str = "low") -> str:
        """
        Choose the Claude model based on file size and risk level.

        Uses the lighter Haiku model for small, low-risk files to reduce API
        cost by ~80%. Falls back to the heavier Sonnet model for large files
        or high/critical-risk files that benefit from stronger reasoning.

        Args:
            line_count (int): Number of lines in the source file being analyzed.
            risk_level (str): Risk level — "critical", "high", "medium", or "low".
                              "critical" and "high" always use the heavy model
                              regardless of line count.

        Returns:
            str: The model ID string to pass to the Anthropic API.
        """
        if risk_level in ("critical", "high"):
            return CLAUDE_MODEL_HEAVY
        if line_count > 150:
            return CLAUDE_MODEL_HEAVY
        return CLAUDE_MODEL_LIGHT

    # ─────────────────────────────────────────────────────────────────────────
    # Main Analysis Method
    # ─────────────────────────────────────────────────────────────────────────

    def analyze_code(
        self,
        code: str,
        file_path: str,
        module_hint: str | None = None,
        screenshot_base64: str | None = None,
        dependencies: list[dict] | None = None,
        bug_patterns: str | None = None,
        line_count: int = 0,
        risk_level: str = "low",
        use_cache: bool = True,
        user_story: str | None = None,
        module: str | None = None,
        skill_context: str | None = None,
        risk_assessment: dict | None = None,
        test_type: str | None = None,
        model_override: str | None = None,
        repo_context: str | None = None,
    ) -> list[dict]:
        """
        Send source code to Claude for test case generation.

        Builds the user prompt, calls the Claude API, parses the JSON response,
        and returns a list of test case dictionaries. If the first response
        cannot be parsed as JSON, automatically retries once with a correction prompt.

        Args:
            code (str): The source code content to analyze.
            file_path (str): Relative path to the source file (used in generated_from_file).
            module_hint (str, optional): Module name to help Claude categorize tests.
                                         If None, Claude will infer from the code.
            screenshot_base64 (str, optional): Base64-encoded PNG screenshot of the app UI.
                                               When provided, Claude sees the actual UI and
                                               can use exact button/field names — reduces
                                               needs_review flags significantly.
            dependencies (list[dict], optional): Related files imported by this file.
                                                  Each dict has: file_path, file_name, content,
                                                  import_line. Gives Claude broader context.
            bug_patterns (str, optional): Recurring bug patterns from BugFetcher.
                                           When provided, injected into user prompt so Claude
                                           generates test cases targeting known failure classes.
            line_count (int): Lines in the file — used for model selection.
            risk_level (str): Risk level ("critical"/"high"/"medium"/"low") — model selection.
            use_cache (bool): If True, check/write file-level cache before calling the API.
            user_story (str, optional): User story / acceptance criteria text.
                                         When provided, Claude generates tests that verify this story.
            module (str, optional): Module name. When provided and skill_context is None,
                                    the SkillLibrary is queried for known patterns automatically.
            skill_context (str, optional): Pre-loaded skill context from SkillLibrary.get_context().
                                           Injected into the prompt to improve test quality.
            risk_assessment (dict, optional): Pre-computed risk from RiskAssessor.assess_file().
                                              Injected into the prompt to guide test depth.
            test_type (str, optional): Test type from RiskAssessor.detect_test_type().
                                       One of "regression", "feature", "smoke", "uat".
            model_override (str, optional): If provided, use this model ID instead of
                                            auto-selecting based on risk/line count.

        Returns:
            list[dict]: List of test case dictionaries. Empty list if no tests generated.

        Raises:
            anthropic.APIError: If the API call fails after retry.
        """
        if not code or not code.strip():
            logger.warning("Empty code provided for file: %s — skipping", file_path)
            return []

        # ── B4: Auto-load skill context from SkillLibrary if not provided ─────
        effective_module = module or module_hint
        if skill_context is None and effective_module:
            try:
                from core.skill_library import SkillLibrary
                skill_lib = SkillLibrary()
                skill_context = skill_lib.get_context(effective_module) or None
                if skill_context:
                    logger.debug(
                        "SkillLibrary: loaded context for module '%s'", effective_module
                    )
            except Exception as _sle:
                logger.debug("SkillLibrary lookup failed (non-fatal): %s", _sle)

        # ── Model selection (A1 + B2) ─────────────────────────────────────────
        # model_override from SmartChecker takes priority; otherwise auto-select
        if model_override:
            model = model_override
        else:
            # Use risk_level from risk_assessment if available (B2: handles "critical")
            effective_risk = risk_level
            if risk_assessment and risk_assessment.get("risk_level"):
                effective_risk = risk_assessment["risk_level"]
            model = self.select_model(line_count, effective_risk)
        logger.info("Using %s for %d lines, risk=%s", model, line_count, risk_level)

        # ── Cache check (A2) ──────────────────────────────────────────────────
        if use_cache and file_path:
            try:
                from core.cache_manager import CacheManager
                cache = CacheManager()
                cache_key = cache.get_key(file_path)
                if cache_key:
                    cached = cache.get(cache_key)
                    if cached is not None:
                        return cached
            except Exception as _ce:
                logger.debug("Cache lookup failed (non-fatal): %s", _ce)

        # Build the user message with code context (and optional deps + bug patterns)
        user_prompt = self._build_user_prompt(
            code, file_path, module_hint, dependencies, bug_patterns, user_story,
            risk_assessment=risk_assessment,
            test_type=test_type,
            skill_context=skill_context,
            repo_context=repo_context,
        )

        logger.info("Sending %s to Claude for analysis...", file_path)
        if screenshot_base64:
            logger.info("  → Screenshot included (Claude will see the app UI)")
        if dependencies:
            logger.info("  → %d dependency file(s) included", len(dependencies))
        if bug_patterns:
            logger.info("  → Bug patterns included (targeting known failure classes)")

        # ── Build the message content (text only, or multimodal with screenshot) ──
        if screenshot_base64:
            # Multimodal message: image first, then the text prompt
            # Claude sees the screenshot and can reference exact UI elements
            message_content = [
                {
                    "type": "text",
                    "text": (
                        "Here is a screenshot of the app UI for context. "
                        "Use the exact UI element names visible in this screenshot "
                        "(button labels, field names, tab names) in your test cases. "
                        "This eliminates the need to set needs_review=true for visible elements."
                    )
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": screenshot_base64,
                    }
                },
                {
                    "type": "text",
                    "text": user_prompt
                }
            ]
        else:
            # Standard text-only message
            message_content = user_prompt

        try:
            # ── First attempt — assistant prefill forces JSON array start ──────
            # By seeding the assistant turn with "[", Claude is forced to
            # continue the array. It cannot write any preamble text because
            # we already "said" the first character for it.
            response = self.client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=self.system_prompt,
                messages=[
                    {"role": "user", "content": message_content},
                    {"role": "assistant", "content": "["},
                ]
            )

            # Reconstruct the full response from the assistant prefill + continuation.
            # Three cases to handle:
            #   A) Claude properly continued:  raw starts with {  → prepend "["
            #   B) Claude echoed the prefill:  raw starts with [  → use as-is
            #   C) Claude added preamble text: raw starts with text → don't prepend,
            #      let _parse_response fallback find the actual "[" in the text
            # Detect truncation early: when the model hits the output ceiling the
            # JSON array is cut off mid-object, so downstream parsing must salvage
            # only the complete test cases rather than trust the whole blob.
            if getattr(response, "stop_reason", None) == "max_tokens":
                logger.warning(
                    "Claude response for %s hit max_tokens (%d) — output may be "
                    "truncated; salvaging complete test cases only",
                    file_path, MAX_TOKENS,
                )

            raw_text = response.content[0].text
            stripped = raw_text.lstrip()
            if stripped.startswith("["):
                # Case B — Claude included its own "[", use response as-is
                response_text = stripped
            elif stripped.startswith("{") or stripped.startswith('"'):
                # Case A — proper continuation, prepend the "[" we seeded
                response_text = "[" + raw_text
            else:
                # Case C — preamble before the array; let the fallback parser
                # find the "[" position inside the text (don't prepend here)
                response_text = raw_text
            logger.debug("Raw Claude response (first 200 chars): %s", response_text[:200])

            # ── Parse the JSON response ───────────────────────────────────────
            test_cases = self._parse_response(response_text)

            if test_cases is None:
                # Parsing failed — retry with a correction prompt
                logger.warning("JSON parse failed for %s — retrying with correction prompt", file_path)
                test_cases = self._retry_with_correction(response_text)

            result = test_cases if test_cases is not None else []

            # ── Cache write (A2) ──────────────────────────────────────────────
            if use_cache and result and file_path:
                try:
                    from core.cache_manager import CacheManager
                    cache = CacheManager()
                    cache_key = cache.get_key(file_path)
                    if cache_key:
                        cache.set(cache_key, result)
                except Exception as _ce:
                    logger.debug("Cache write failed (non-fatal): %s", _ce)

            return result

        except anthropic.APIError as e:
            logger.error("Claude API error for file %s: %s", file_path, str(e))
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # User Prompt Builder
    # ─────────────────────────────────────────────────────────────────────────

    def _build_user_prompt(
        self,
        code: str,
        file_path: str,
        module_hint: str | None,
        dependencies: list[dict] | None = None,
        bug_patterns: str | None = None,
        user_story: str | None = None,
        risk_assessment: dict | None = None,
        test_type: str | None = None,
        skill_context: str | None = None,
        repo_context: str | None = None,
    ) -> str:
        """
        Construct the user message that will be sent to Claude alongside the system prompt.

        Args:
            code (str): The source code to analyze.
            file_path (str): Relative file path (shown to Claude for context).
            module_hint (str | None): Optional module name to focus analysis.
            dependencies (list[dict] | None): Related dependency files to include.
                Each dict has: file_path, file_name, content, import_line.
            bug_patterns (str | None): Recurring bug patterns from BugFetcher to inject.
            user_story (str | None): User story / acceptance criteria text. When provided,
                Claude is asked to generate tests that specifically verify this story.
            risk_assessment (dict | None): Risk profile from RiskAssessor. When provided,
                a RISK ASSESSMENT section is added to guide test depth (B2).
            test_type (str | None): Test type from RiskAssessor. One of
                "regression", "feature", "smoke", "uat". Guides test focus (B3).
            skill_context (str | None): Known patterns from SkillLibrary. When provided,
                added before the code as example guidance (B4).

        Returns:
            str: The formatted user prompt string.
        """
        module_context = (
            f"This file belongs to the **{module_hint}** module."
            if module_hint
            else "Determine the relevant module from the code context."
        )

        # ── Build dependency section if provided ──────────────────────────────
        deps_section = ""
        if dependencies:
            dep_lines = ["\n**Related Dependency Files (imported by the main file):**\n"]
            for dep in dependencies:
                dep_lines.append(f"--- {dep.get('file_name', 'unknown')} ---")
                dep_lines.append(f"Import line: {dep.get('import_line', '')}")
                dep_lines.append("```")
                dep_lines.append(dep.get("content", ""))
                dep_lines.append("```\n")
            deps_section = "\n".join(dep_lines)

        # ── Build bug patterns section if provided ─────────────────────────────
        bug_patterns_section = ""
        bug_patterns_instruction = ""
        if bug_patterns and bug_patterns.strip():
            bug_patterns_section = (
                "\n**Recurring Bug Patterns for this Module (from resolved Jira bugs):**\n"
                "Generate at least one test case per applicable pattern below:\n"
                f"{bug_patterns.strip()}\n"
            )
            bug_patterns_instruction = "\n6. Specifically test for each recurring bug pattern listed above."

        # ── User story section (A6) ───────────────────────────────────────────
        user_story_section = ""
        user_story_instruction = ""
        if user_story and user_story.strip():
            user_story_section = (
                "\n**USER STORY / ACCEPTANCE CRITERIA:**\n"
                f"{user_story.strip()}\n\n"
                "Generate test cases that specifically verify this story is working correctly.\n"
            )
            user_story_instruction = "\n6. Specifically verify the user story / acceptance criteria above."

        # ── Risk assessment section (B2) ──────────────────────────────────────
        risk_section_b2 = ""
        if risk_assessment and risk_assessment.get("risk_level"):
            rl = risk_assessment["risk_level"]
            priority = risk_assessment.get("test_priority", "P3")
            depth = risk_assessment.get("recommended_depth", "standard")
            areas = risk_assessment.get("critical_areas", [])
            areas_str = ", ".join(areas) if areas else "general logic"
            risk_section_b2 = (
                f"\n**RISK ASSESSMENT:**\n"
                f"Risk level: {rl.upper()} ({priority})\n"
                f"Critical areas: {areas_str}\n"
                f"Generate **{depth}** coverage.\n"
                f"Prioritize tests for: {areas_str}\n"
            )

        # ── Test type section (B3) ────────────────────────────────────────────
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

        # ── Skill context section (B4) ────────────────────────────────────────
        skill_context_section = ""
        if skill_context and skill_context.strip():
            skill_context_section = (
                f"\n**KNOWN PATTERNS (use these as examples):**\n"
                f"{skill_context.strip()}\n"
            )

        # ── App context section (whole-source comprehension) ──────────────────
        repo_context_section = ""
        if repo_context and repo_context.strip():
            repo_context_section = (
                "\n**APP CONTEXT (whole-source understanding — use it to focus "
                "the tests on real flows and risks):**\n"
                f"{repo_context.strip()}\n"
            )

        # ── API steps instruction (real, executable HTTP) ─────────────────────
        # When testing an API/endpoint file, ask for executable api_steps so the
        # runner can hit the live endpoints — not just UI-shaped automation_steps.
        api_steps_instruction = ""
        if test_type in ("api", "e2e", "security", "integration", "negative", "boundary"):
            api_steps_instruction = (
                "\n\nThis file exposes HTTP endpoints. For EACH test case, also include an "
                "\"api_steps\" array of REAL, EXECUTABLE HTTP requests so the test can run "
                "against the live API. Example:\n"
                "  \"api_steps\": [\n"
                "    {\"step_number\": 1, \"method\": \"POST\", \"path\": \"/auth/register\", "
                "\"body\": {\"email\": \"user_{{random}}@test.com\", \"password\": \"Secret123!\"}, "
                "\"expected_status\": 201},\n"
                "    {\"step_number\": 2, \"method\": \"POST\", \"path\": \"/auth/login\", "
                "\"body\": {\"email\": \"user_{{random}}@test.com\", \"password\": \"Secret123!\"}, "
                "\"expected_status\": 200, \"save\": {\"token\": \"access_token\"}},\n"
                "    {\"step_number\": 3, \"method\": \"GET\", \"path\": \"/auth/me\", "
                "\"expected_status\": 200, \"use_auth\": true}\n"
                "  ]\n"
                "Rules for api_steps:\n"
                "- SELF-CONTAINED: each test sets up ALL its own prerequisites. If an endpoint "
                "needs auth, the FIRST steps must register a fresh user (email \"user_{{random}}@test.com\") "
                "and then log in — never start with login for a user you did not just register, and never "
                "assume any user, record, or session already exists.\n"
                "- Use the EXACT endpoint paths, HTTP methods, and request/response field names from the code.\n"
                "- expected_status must be the real status the endpoint returns for THIS scenario "
                "(e.g. 201 create, 200 ok, 400 validation, 401 unauthorized, 404 not found, 422 unprocessable).\n"
                "- To reuse a value from a response (e.g. an auth token), add \"save\": "
                "{\"token\": \"<response_field>\"} on the step that returns it, then \"use_auth\": true "
                "on later steps that need it.\n"
                "- For values that must be unique per run (e.g. a new user email), use {{random}} "
                "as a placeholder — the SAME {{random}} resolves to the same value within a test case.\n"
                "- ASSERT THE RESPONSE BODY, not just the status. Add \"expect_json\" to a read step "
                "to check values INSIDE the response — a dot-path mapped to its expected value, with "
                "a trailing .length for a collection size, e.g. "
                "{\"total\": 5, \"total_pages\": 3, \"items.length\": 5}.\n"
                "  * Computed values: when an endpoint returns a count, total, page count, or any "
                "derived number, SET UP a known state (e.g. create exactly 5 items) and assert the "
                "computed result you calculate yourself (e.g. with page_size 2, total_pages must be "
                "ceil(5/2)=3). Pick a count that is NOT a multiple of the page size so an off-by "
                "computation is exposed.\n"
                "  * Persistence: after a create/update, add a GET step that re-fetches the resource "
                "and use expect_json to assert the value actually persisted.\n"
                "- Keep api_steps minimal and derived only from the code."
            )

        try:
            from core.istqb_knowledge import prompt_block as _istqb_prompt_block
            istqb_section = "\n" + _istqb_prompt_block() + "\n"
        except Exception:
            istqb_section = ""

        prompt = f"""Analyze the following source code file and generate comprehensive test cases.

**File Path:** {file_path}
**Module Context:** {module_context}
{repo_context_section}{user_story_section}{risk_section_b2}{test_type_section}{bug_patterns_section}{skill_context_section}
**Source Code:**
```
{code}
```
{deps_section}
Cover EVERY operation this code exposes (each endpoint / function / branch) — not just the first one. For each, generate the relevant cases from:
1. Happy path (successful execution)
2. Validation failures (missing / invalid / out-of-range / malformed input)
3. **Authorization & ownership boundaries** — if data or actions are scoped to a user or role, test that another user/role is rejected (e.g. accessing/modifying/deleting someone else's resource → expect 403/404). This is the highest-value class — include it whenever the code scopes access.
4. State-transition and data-integrity cases (invalid transitions, uniqueness, cascade effects)
5. Error-handling paths (auth failure, not-found, conflict, dependency failure){bug_patterns_instruction}{user_story_instruction}{api_steps_instruction}
{istqb_section}
Coverage doctrine (apply in addition to the above):
- OBSERVED UI SURFACE: if the context above lists real screens and selectors from a live tour, generate UI tests grounded in those screens and set every automation_steps[].selector_key to the EXACT real selector shown (e.g. [data-testid="..."]) — never a TODO_ placeholder for an element that appears in the surface. Only tour-observed screens/elements are real; do not invent others.
- Persistence: after a create/update, re-fetch the resource and verify the value actually survived a reload — a "saved"/200 response that loses the value is a real bug.
- Absence & reversal: assert that forbidden things are NOT offered/present, and pair each mutating action with its inverse (create/delete, hold/restore).
- ORACLE: every expected result comes from the operation's contract/convention or the user story — NOT from whatever this (possibly buggy) code returns. If a create returns 200, still expect 201 and surface the mismatch as a candidate bug. When the true result is genuinely uncertain, set needs_review=true and phrase the expected result as "verify as-configured / document as-found" instead of inventing a value.

- Set needs_review=true for any UI element you cannot confirm from this code.
- Set generated_from_file to exactly: {file_path}

FINAL REMINDER: Your response = JSON array only.
Starts with [. Ends with ]. Nothing else.
Do NOT write any text before [ or after ].
Do NOT use markdown. Do NOT explain. Just the JSON array."""
        return prompt

    # ─────────────────────────────────────────────────────────────────────────
    # Response Parser
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_response(self, response_text: str) -> list[dict] | None:
        """
        Parse Claude's text response into a list of test case dictionaries.

        Robust against three real-world failure modes seen in production:
          1. Preamble text or markdown fences around the JSON.
          2. Truncation when the model hits max_tokens mid-array — the outer
             array never closes, so naive "scan back from the last ]" bracket
             matching would grab an inner ``manual_steps`` array and return step
             objects instead of test cases.
          3. An incomplete trailing object after truncation.

        Every candidate list is passed through :meth:`_accept_test_cases`, which
        only accepts lists whose elements actually look like test cases — this is
        what stops a stray array of steps from being mistaken for the result. A
        final salvage pass (:meth:`_salvage_objects`) extracts every complete
        top-level object and keeps the test-case-shaped ones, recovering cleanly
        from truncation.

        Args:
            response_text (str): The raw text response from Claude, with "["
                                 prepended if assistant prefill was used.

        Returns:
            list[dict] | None: Parsed test cases, or None when nothing parseable
                               was found (which triggers the correction retry).
        """
        if not response_text or not response_text.strip():
            logger.warning("Empty response from Claude")
            return None

        cleaned = response_text.strip()

        # ── Attempt 1: Direct JSON parse ──────────────────────────────────────
        try:
            result = json.loads(cleaned)
            if isinstance(result, list):
                accepted = self._accept_test_cases(result)
                if accepted is not None:
                    logger.debug("Parsed JSON directly (%d test case(s))", len(accepted))
                    return accepted
            elif isinstance(result, dict):
                # Some models return {"test_cases": [...]} — unwrap it
                for key in ("test_cases", "tests", "results", "data"):
                    if isinstance(result.get(key), list):
                        accepted = self._accept_test_cases(result[key])
                        if accepted is not None:
                            logger.debug("Unwrapped JSON from key '%s'", key)
                            return accepted
        except json.JSONDecodeError:
            pass

        # ── Attempt 2: Strip markdown code fences (prefill-aware) ─────────────
        # Handles ```json ... ``` blocks, including the prefill case where the
        # opening "[" was consumed by the assistant seed.
        code_block_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
        for match in re.findall(code_block_pattern, cleaned):
            content = match.strip()
            if not content:
                continue
            for candidate in (content, f"[{content}", f"[{content}]"):
                try:
                    result = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(result, list):
                    accepted = self._accept_test_cases(result)
                    if accepted is not None:
                        logger.debug("Parsed JSON from markdown code block")
                        return accepted

        # ── Attempt 3: Extract the OUTERMOST array ────────────────────────────
        # Scan FORWARD from the first "[" to its matching "]" (string-aware).
        # Scanning forward finds the test-case array; the old scan-backward
        # approach could latch onto an inner steps array on truncated output.
        outer = self._extract_outer_array(cleaned)
        if outer is not None:
            try:
                result = json.loads(outer)
                if isinstance(result, list):
                    accepted = self._accept_test_cases(result)
                    if accepted is not None:
                        logger.debug("Parsed JSON by outer-array extraction")
                        return accepted
            except json.JSONDecodeError:
                pass

        # ── Attempt 4: Salvage every complete top-level object ────────────────
        # Recovers from truncation: keeps each complete test-case object and
        # silently drops an incomplete trailing one.
        salvaged = self._salvage_objects(cleaned)
        if salvaged:
            logger.warning(
                "Recovered %d test case(s) via salvage — response was likely "
                "truncated or malformed", len(salvaged),
            )
            return salvaged

        logger.warning("All JSON parsing attempts failed — first 300 chars: %s", cleaned[:300])
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Parsing Helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _looks_like_test_case(obj: object) -> bool:
        """
        Heuristic: does this object look like a test case rather than a step?

        A test case carries identifying/structural fields (test_case_id, title,
        manual_steps, automation_steps). A bare step object has step_number +
        action and none of those, so it is explicitly rejected. This is the
        guard that stops an inner steps array from being returned as the result.

        Args:
            obj: Any parsed JSON value.

        Returns:
            bool: True if obj is a test-case-shaped dict.
        """
        if not isinstance(obj, dict):
            return False
        if "step_number" in obj and "action" in obj and "title" not in obj:
            return False
        return any(
            k in obj
            for k in ("test_case_id", "title", "manual_steps", "automation_steps")
        )

    @classmethod
    def _accept_test_cases(cls, result: list) -> list[dict] | None:
        """
        Accept a parsed list only if it genuinely holds test cases.

        Args:
            result (list): A parsed JSON array.

        Returns:
            list[dict] | None: The test-case-shaped elements (strays dropped),
                               or None if the list is empty or not a test-case
                               list (e.g. an array of steps).
        """
        if not isinstance(result, list) or not result:
            return None
        test_cases = [o for o in result if cls._looks_like_test_case(o)]
        return test_cases or None

    @staticmethod
    def _extract_outer_array(text: str) -> str | None:
        """
        Return the substring of the first balanced top-level ``[...]`` array.

        Scans forward from the first "[" tracking bracket depth while ignoring
        brackets inside JSON strings.

        Args:
            text (str): The cleaned response text.

        Returns:
            str | None: The array substring, or None if no balanced outer array
                        is found (e.g. the array was truncated before closing).
        """
        start = text.find("[")
        if start == -1:
            return None
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return None

    @classmethod
    def _salvage_objects(cls, text: str) -> list[dict]:
        """
        Extract every complete top-level ``{...}`` object and keep the
        test-case-shaped ones.

        Brace depth is tracked string-aware, so objects nested inside a test
        case (manual_steps entries, test_data) are not extracted on their own.
        A truncated final object fails json.loads and is skipped — the graceful
        degradation we want when the model runs out of output tokens mid-array.

        Args:
            text (str): The cleaned response text.

        Returns:
            list[dict]: Complete, test-case-shaped objects (possibly empty).
        """
        objects: list[dict] = []
        depth = 0
        start = -1
        in_str = False
        escape = False
        for i, ch in enumerate(text):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start != -1:
                        try:
                            obj = json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            obj = None
                        if cls._looks_like_test_case(obj):
                            objects.append(obj)
                        start = -1
        return objects

    # ─────────────────────────────────────────────────────────────────────────
    # Retry with Correction Prompt
    # ─────────────────────────────────────────────────────────────────────────

    def _retry_with_correction(self, bad_response: str) -> list[dict] | None:
        """
        Send a follow-up correction prompt to Claude when the first response
        could not be parsed as valid JSON.

        Shows Claude its bad response, estimates how many test cases it
        identified, and explicitly asks it to return ALL of them in clean JSON.

        Args:
            bad_response (str): The malformed response from the first attempt.

        Returns:
            list[dict] | None: Parsed test cases from the corrected response,
                               or None if the retry also fails.
        """
        # ── Estimate how many test cases Claude identified in the bad response ─
        # Count occurrences of "title" or "test_case_id" patterns as a proxy
        estimated_count = max(
            bad_response.count('"title"'),
            bad_response.count('"test_case_id"'),
            bad_response.count("test_case_id"),
            1   # Always at least 1
        )

        correction_prompt = f"""Your previous response could not be parsed as valid JSON because it \
contained text or formatting outside the JSON array.

You identified approximately {estimated_count} test case(s) in your previous response.
Return ALL {estimated_count} test cases — do not drop any of them.
The content of your previous response was good — only the formatting was wrong.

Here is what you returned (for reference):
---
{bad_response[:2000]}
---

Now re-generate the SAME test cases in correct JSON format.

STRICT RULES:
- First character must be [
- Last character must be ]
- No text before [
- No text after ]
- No markdown code fences (no ```)
- No explanations, no introductions, no summaries
- Include ALL {estimated_count} test cases you identified

Return the corrected JSON array now:"""

        try:
            logger.info("Sending correction prompt to Claude...")
            # Use assistant prefill here too for maximum reliability
            # Always use the heavy model for correction retries
            response = self.client.messages.create(
                model=CLAUDE_MODEL_HEAVY,
                max_tokens=MAX_TOKENS,
                system=self.system_prompt,
                messages=[
                    {"role": "user", "content": correction_prompt},
                    {"role": "assistant", "content": "["},
                ]
            )

            raw_corrected = response.content[0].text
            stripped_corrected = raw_corrected.lstrip()
            if stripped_corrected.startswith("["):
                corrected_text = stripped_corrected
            elif stripped_corrected.startswith("{") or stripped_corrected.startswith('"'):
                corrected_text = "[" + raw_corrected
            else:
                corrected_text = raw_corrected
            result = self._parse_response(corrected_text)

            if result is not None:
                logger.info("Correction prompt succeeded — %d test cases recovered", len(result))
            else:
                logger.error("Correction prompt also failed to produce valid JSON")

            return result

        except anthropic.APIError as e:
            logger.error("Claude API error during correction retry: %s", str(e))
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Deduplication
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def deduplicate_by_title(test_cases: list[dict]) -> list[dict]:
        """
        Remove duplicate test cases from a merged list by title similarity.

        When multiple chunks generate test cases for the same logical scenario,
        titles often overlap. If two test cases share >80% Jaccard word similarity,
        only the one with the higher confidence_score is kept.

        Args:
            test_cases (list[dict]): Merged test cases from all chunks.

        Returns:
            list[dict]: Deduplicated list with no near-duplicate titles.
        """
        if not test_cases:
            return test_cases

        def _jaccard(t1: str, t2: str) -> float:
            """Jaccard similarity on word sets (case-insensitive)."""
            w1 = set(t1.lower().split())
            w2 = set(t2.lower().split())
            if not w1 or not w2:
                return 0.0
            return len(w1 & w2) / len(w1 | w2)

        kept: list[dict] = []
        for candidate in test_cases:
            candidate_title = candidate.get("title", "")
            candidate_score = candidate.get("confidence_score", 0)
            is_duplicate = False

            for i, existing in enumerate(kept):
                existing_title = existing.get("title", "")
                sim = _jaccard(candidate_title, existing_title)
                if sim > 0.80:
                    # Duplicate found — keep the one with higher confidence
                    existing_score = existing.get("confidence_score", 0)
                    if candidate_score > existing_score:
                        kept[i] = candidate
                        logger.debug(
                            "Dedup: replaced '%s' (score %s) with '%s' (score %s)",
                            existing_title[:40], existing_score,
                            candidate_title[:40], candidate_score,
                        )
                    else:
                        logger.debug(
                            "Dedup: dropped '%s' (score %s) — kept '%s' (score %s)",
                            candidate_title[:40], candidate_score,
                            existing_title[:40], existing_score,
                        )
                    is_duplicate = True
                    break

            if not is_duplicate:
                kept.append(candidate)

        removed = len(test_cases) - len(kept)
        if removed:
            logger.info("Deduplication removed %d near-duplicate test case(s)", removed)
        return kept
