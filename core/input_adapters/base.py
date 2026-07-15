"""
core/input_adapters/base.py — Base Input Adapter
=================================================
Abstract contract all input adapters must satisfy.

Each adapter fetches source material from a different origin (file, folder,
Jira ticket, GitHub PR, plain text story) and normalises it into the same
list-of-dicts shape so the pipeline treats every source identically.

Guarantee: fetch() NEVER raises. It returns [] on any error.

Subclass pattern:
    from core.input_adapters.base import BaseInputAdapter

    class MyAdapter(BaseInputAdapter):
        name = "mysource"

        def validate(self) -> tuple[bool, str]:
            ...

        def fetch(self, source: str, **kwargs) -> list[dict]:
            ...
"""


class BaseInputAdapter:
    """
    Abstract base class for all QAX input adapters.

    Defines the two-method interface used by the pipeline to fetch and
    normalise source material regardless of its origin.

    Attributes:
        name (str): Short identifier for this adapter, e.g. "file", "jira".
    """

    name: str = "base"

    def fetch(self, source: str, **kwargs) -> list[dict]:
        """
        Fetch source material and return a normalised list of input dicts.

        Must never raise — catch all exceptions internally and return [].

        Args:
            source (str): Source identifier — file path, folder path,
                          Jira key, PR number, or plain text depending
                          on the adapter.
            **kwargs: Adapter-specific options (e.g. ext=".cs", recursive=True).

        Returns:
            list[dict]: Each dict has the shape:
                {
                  "file_path":   str,   # real path or virtual URI
                  "content":     str,   # text content for Claude
                  "file_name":   str,   # short display name
                  "source_type": str,   # adapter name
                  "metadata":    dict,  # adapter-specific extras
                }
            Returns [] when source cannot be fetched.
        """
        raise NotImplementedError(
            f"Adapter '{self.name}' must implement fetch()"
        )

    def validate(self) -> tuple[bool, str]:
        """
        Verify that all required credentials and config are present.

        Called before fetch() so failures are reported immediately with
        an actionable message rather than surfacing mid-fetch.

        Returns:
            tuple[bool, str]: (is_valid, error_message).
                is_valid      — True if the adapter is ready to fetch.
                error_message — Human-readable reason if False, "" otherwise.
        """
        raise NotImplementedError(
            f"Adapter '{self.name}' must implement validate()"
        )
