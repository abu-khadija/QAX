"""
services/comprehension_service.py — RepoComprehensionService (Understand step)
==============================================================================
Reads a whole source (folder / file / PR / story) and builds a high-level map
of the application BEFORE generation: what it does, the main flows, entry
points, and the riskiest areas. That map is then injected into generation as
app context, so the generated tests are informed by the whole picture rather
than one file in isolation.

It is a single bounded Claude call regardless of file count — the source is
digested (each file truncated) to fit a token budget. Never prints, never
calls sys.exit(); failures are captured in ComprehensionResult.error.

Usage:
    from services import QAXContext
    from services.comprehension_service import RepoComprehensionService

    svc = RepoComprehensionService(QAXContext.from_env())
    result = svc.comprehend("src/", input_type="folder", ext=".py")
    print(result.overview, result.risk_areas)
    context_for_generation = result.as_context()
"""

from __future__ import annotations

import json
import logging
import re

from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import ComprehensionResult

logger = logging.getLogger(__name__)

# Bound the digest so a big repo still fits one call.
_DIGEST_CHAR_BUDGET = 24000
_PER_FILE_LINES = 45


class RepoComprehensionService:
    """
    Build a high-level comprehension of a source before test generation.

    One Claude call over a digest of the source. dry_run has no effect here —
    comprehension never writes anything externally.
    """

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Configuration/credentials. Built from
                the environment when None.
        """
        self.context = context or QAXContext.from_env()

    def comprehend(
        self,
        source: str,
        *,
        input_type: str = "folder",
        ext: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> ComprehensionResult:
        """
        Read the source and return a high-level app map.

        Args:
            source (str): Path / folder / PR / story text, per input_type.
            input_type (str): Input adapter — "file", "folder", "jira", "pr",
                "story".
            ext (str | None): Extension filter for the folder adapter.
            progress (ProgressReporter | None): Progress sink.

        Returns:
            ComprehensionResult: The app map, or one with .error set on failure.

        Raises:
            QAXConfigError: If the Anthropic API key is missing.
        """
        prog = progress or NullProgressReporter()
        self.context.require_anthropic_key()

        prog.emit("understand", "start", f"reading source via '{input_type}'")
        try:
            from core.input_adapters import get_input_adapter

            items = get_input_adapter(input_type).fetch(source, ext=ext)
        except Exception as exc:  # adapters shouldn't raise, but be safe
            logger.error("comprehension: adapter '%s' failed: %s", input_type, exc)
            return ComprehensionResult(error=str(exc))

        if not items:
            prog.emit("understand", "warn", "no analyzable input found")
            return ComprehensionResult(error="No analyzable input found for this source")

        digest = self._build_digest(items)
        prog.emit("understand", "ok", f"{len(items)} file(s) read")

        prog.emit("understand", "start", "building the app map")
        try:
            data = self._call_claude(digest, len(items))
        except Exception as exc:
            logger.error("comprehension: Claude call failed: %s", exc)
            prog.emit("understand", "error", str(exc))
            return ComprehensionResult(file_count=len(items), error=str(exc))

        result = ComprehensionResult(
            overview=str(data.get("overview", "")),
            main_flows=[str(x) for x in data.get("main_flows", []) if x],
            entry_points=[str(x) for x in data.get("entry_points", []) if x],
            risk_areas=[str(x) for x in data.get("risk_areas", []) if x],
            files=[f for f in data.get("files", []) if isinstance(f, dict)],
            file_count=len(items),
        )
        prog.emit("understand", "ok", "app map ready")
        return result

    # ── Internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _build_digest(items: list[dict]) -> str:
        """
        Build a truncated digest of the source that fits the char budget.

        Each file contributes its path and first ~45 lines; once the budget is
        full, remaining files are listed by path only.

        Args:
            items (list[dict]): Normalised input items from an adapter.

        Returns:
            str: The digest text.
        """
        parts: list[str] = []
        used = 0
        for it in items:
            path = it.get("file_path") or it.get("file_name") or "?"
            content = it.get("content", "") or ""
            snippet = "\n".join(content.splitlines()[:_PER_FILE_LINES])
            block = f"=== {path} ===\n{snippet}\n"
            if used + len(block) > _DIGEST_CHAR_BUDGET:
                parts.append(f"=== {path} === (listed only — digest full)\n")
                continue
            parts.append(block)
            used += len(block)
        return "\n".join(parts)

    def _call_claude(self, digest: str, file_count: int) -> dict:
        """
        Send the digest to Claude and parse the JSON comprehension.

        Args:
            digest (str): The source digest.
            file_count (int): Number of files read.

        Returns:
            dict: Parsed comprehension fields (possibly empty on parse failure).
        """
        import anthropic

        client = anthropic.Anthropic(api_key=self.context.anthropic_key)
        response = client.messages.create(
            model=self.context.model_heavy,
            max_tokens=2000,
            messages=[{"role": "user", "content": self._build_prompt(digest, file_count)}],
        )
        return self._parse_json(response.content[0].text)

    @staticmethod
    def _build_prompt(digest: str, file_count: int) -> str:
        """Build the comprehension prompt."""
        return f"""You are a senior QA engineer reviewing a codebase before writing tests.

Below are {file_count} source file(s) (each truncated). Read them and produce a
concise, high-level map of the application to guide test design.

Return ONLY a JSON object with these keys:
- "overview": one or two sentences on what this app/module does.
- "main_flows": array of the key user/business flows (short phrases).
- "entry_points": array of entry points (controllers, routes, handlers) as "file: what it does".
- "risk_areas": array of the riskiest areas to test hard (e.g. auth, permissions, payments, data mutations, validation).
- "files": array of {{"path": "...", "role": "one-line role"}} for the most important files.

Source:
{digest}

Return the JSON object only — first character {{, last character }}. No markdown, no prose."""

    @staticmethod
    def _parse_json(text: str) -> dict:
        """
        Extract the JSON object from Claude's response.

        Args:
            text (str): Raw response text.

        Returns:
            dict: Parsed object, or {} if nothing parseable was found.
        """
        if not text:
            return {}
        cleaned = text.strip()
        candidates = [cleaned]
        match = re.search(r"\{[\s\S]*\}", cleaned)  # first { ... last }
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
        logger.warning("comprehension: could not parse JSON — first 200 chars: %s", cleaned[:200])
        return {}
