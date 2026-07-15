"""
core/input_adapters/__init__.py — Input Adapter Registry
=========================================================
Exports all input adapter classes and a factory helper.

Available adapters:
  file   → Read a single source file from disk
  folder → Walk a directory and collect all matching files
  jira   → Fetch a Jira ticket's description as story input
  pr     → Fetch changed files from a GitHub Pull Request
  story  → Use plain text (user story / requirement) directly

Usage:
    from core.input_adapters import get_input_adapter

    adapter = get_input_adapter("jira")
    items   = adapter.fetch("PROJ-123")
    # items[0]["content"] ready for pipeline generation phase
"""

from core.input_adapters.base import BaseInputAdapter
from core.input_adapters.file_adapter import FileInputAdapter
from core.input_adapters.folder_adapter import FolderInputAdapter
from core.input_adapters.jira_adapter import JiraInputAdapter
from core.input_adapters.pr_adapter import PRInputAdapter
from core.input_adapters.story_adapter import StoryInputAdapter

# ── Registry: name → class ────────────────────────────────────────────────────
ADAPTERS: dict[str, type[BaseInputAdapter]] = {
    "file":   FileInputAdapter,
    "folder": FolderInputAdapter,
    "jira":   JiraInputAdapter,
    "pr":     PRInputAdapter,
    "story":  StoryInputAdapter,
}


def get_input_adapter(name: str) -> BaseInputAdapter:
    """
    Instantiate and return an input adapter by name.

    Falls back to FileInputAdapter for unknown names so the pipeline
    never crashes on a typo in --input.

    Args:
        name (str): Adapter key — "file", "folder", "jira", "pr", or "story".

    Returns:
        BaseInputAdapter: Ready-to-use adapter instance.
    """
    cls = ADAPTERS.get((name or "file").lower())
    if cls is None:
        print(f"  [QAX] Unknown input adapter '{name}' — using 'file'")
        cls = FileInputAdapter
    return cls()


__all__ = [
    "BaseInputAdapter",
    "FileInputAdapter",
    "FolderInputAdapter",
    "JiraInputAdapter",
    "PRInputAdapter",
    "StoryInputAdapter",
    "ADAPTERS",
    "get_input_adapter",
]
