"""
core/input_adapters/story_adapter.py — User Story Input Adapter
================================================================
Takes plain text (user story, acceptance criteria, or any requirement text)
as the input source. No code file is required.

Claude generates test cases directly from the story text, which is useful
when writing tests before the code exists (TDD / BDD workflows) or when
testing against API/system behaviour described in natural language.

Usage:
    adapter = StoryInputAdapter()
    items   = adapter.fetch("As a user I want to log in with email and password")
    # items[0]["content"] = the story text, ready for Claude
"""

import logging

from core.input_adapters.base import BaseInputAdapter

logger = logging.getLogger(__name__)


class StoryInputAdapter(BaseInputAdapter):
    """
    Input adapter that wraps a plain-text user story or requirement string.

    No credentials, no filesystem access, no network calls.
    Always succeeds — returns a single item even for empty strings (with
    a warning logged so the caller can surface it to the user).
    """

    name = "story"

    def validate(self) -> tuple[bool, str]:
        """
        No credentials required — always valid.

        Returns:
            tuple[bool, str]: (True, "") unconditionally.
        """
        return True, ""

    def fetch(self, source: str, **kwargs) -> list[dict]:
        """
        Wrap the story text in a normalised input dict.

        Args:
            source (str): The user story or requirement text.
                          Passed directly to Claude as the "code" content.
            **kwargs: Unused.

        Returns:
            list[dict]: Single-element list with the story content,
                        or [] if source is empty.
        """
        if not source or not source.strip():
            logger.warning("QAX StoryInputAdapter: empty story text provided")
            print("  [QAX] Story input is empty — nothing to generate from")
            return []

        word_count = len(source.split())
        logger.info(
            "QAX StoryInputAdapter: %d word(s) of story text received", word_count
        )

        return [{
            "file_path":   "story://input",
            "content":     source.strip(),
            "file_name":   "user_story",
            "source_type": self.name,
            "metadata": {
                "word_count": word_count,
                "is_story":   True,
                "char_count": len(source),
            },
        }]
