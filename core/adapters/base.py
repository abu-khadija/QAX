"""
core/adapters/base.py — Base Export Adapter
============================================
Abstract contract every export adapter must satisfy.

All adapters follow the same three guarantees:
  1. export() NEVER raises — always returns a result dict.
  2. validate_config() is called first so callers fail fast with a clear message.
  3. dry_run=True is always safe regardless of credentials.

Subclass pattern:
    from core.adapters.base import BaseExportAdapter

    class MyAdapter(BaseExportAdapter):
        name = "mytarget"

        def validate_config(self) -> tuple[bool, str]:
            ...

        def export(self, test_cases, project=None, dry_run=True) -> dict:
            ...
"""


class BaseExportAdapter:
    """
    Base class for all QAX export adapters.

    Defines the interface that the pipeline orchestrator uses to push
    generated test cases to any target system (Xray, Zephyr, Excel, etc.)
    without knowing the implementation details of each system.

    Attributes:
        name (str): Short identifier for this adapter, e.g. "xray".
    """

    name: str = "base"

    def export(
        self,
        test_cases: list[dict],
        project: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        """
        Export test cases to the target system.

        Must never raise — wrap all exceptions and return them in the
        "error" field so the pipeline can continue to execution regardless.

        Args:
            test_cases (list[dict]): Validated test case dicts from the generator.
            project (str | None): Project key/name used by the target system.
                                   Falls back to env vars when None.
            dry_run (bool): When True, simulate the export without making
                            external API calls or writing files.

        Returns:
            dict: Result containing:
                - "success"        (bool)      — overall outcome
                - "exported_count" (int)       — number successfully exported
                - "failed_count"   (int)       — number that failed
                - "references"     (list[str]) — Xray keys, file paths, etc.
                - "error"          (str|None)  — first/summary error or None
        """
        raise NotImplementedError(
            f"Adapter '{self.name}' must implement export()"
        )

    def validate_config(self) -> tuple[bool, str]:
        """
        Verify that all required credentials and dependencies are present.

        Called by the pipeline before export() so misconfiguration is
        reported immediately with an actionable message rather than
        surfacing mid-export.

        Returns:
            tuple[bool, str]: (is_valid, error_message)
                is_valid     — True if the adapter is ready to export.
                error_message — Human-readable reason if is_valid is False,
                               empty string otherwise.
        """
        raise NotImplementedError(
            f"Adapter '{self.name}' must implement validate_config()"
        )

    # ── Convenience ────────────────────────────────────────────────────────────

    def _empty_result(
        self,
        total: int,
        error: str | None = None,
    ) -> dict:
        """
        Build a failed-export result dict with zero successes.

        Args:
            total (int): Number of test cases that were attempted.
            error (str | None): Error message to include.

        Returns:
            dict: Standard result dict with success=False.
        """
        return {
            "success":        False,
            "exported_count": 0,
            "failed_count":   total,
            "references":     [],
            "error":          error,
        }

    def _success_result(
        self,
        exported: int,
        failed: int,
        references: list[str],
    ) -> dict:
        """
        Build a successful-export result dict.

        Args:
            exported (int): Count of successfully exported test cases.
            failed (int): Count of failed test cases.
            references (list[str]): External identifiers (keys, file paths).

        Returns:
            dict: Standard result dict with success=True when exported > 0.
        """
        return {
            "success":        exported > 0,
            "exported_count": exported,
            "failed_count":   failed,
            "references":     references,
            "error":          None if exported > 0 else "All exports failed",
        }
