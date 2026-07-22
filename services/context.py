"""
services/context.py — QAXContext
=================================
A single object that carries configuration, credentials, and run-wide flags
into a service call. It replaces the scattered ``load_dotenv()`` calls and the
global ``os.environ["DRY_RUN"]`` state that make the current code impossible to
run safely inside a web server (where two tenants must not share globals).

Single-tenant today:
    ctx = QAXContext.from_env()          # reads .env, same behaviour as before

Multi-tenant later (SaaS):
    ctx = QAXContext(
        anthropic_key=tenant.anthropic_key,
        project=tenant.project,
        dry_run=True,
        jira=tenant.jira_creds,
        xray=tenant.xray_creds,
    )

Because every service takes a QAXContext, moving from one .env to per-tenant
secrets later is a change in *how the context is built*, not a change in the
services themselves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass
class JiraCredentials:
    """Jira REST credentials. Empty fields mean 'not configured'."""

    base_url: str = ""
    email: str = ""
    api_token: str = ""

    def is_configured(self) -> bool:
        """Return True only when all three Jira fields are present."""
        return bool(self.base_url and self.email and self.api_token)


@dataclass
class XrayCredentials:
    """Xray Cloud API credentials. Empty fields mean 'not configured'."""

    client_id: str = ""
    client_secret: str = ""

    def is_configured(self) -> bool:
        """Return True only when both Xray fields are present."""
        return bool(self.client_id and self.client_secret)


@dataclass
class QAXContext:
    """
    Configuration and credentials for a single QAX operation.

    Attributes:
        anthropic_key (str): Anthropic API key. Empty string when unset —
            services that need it raise QAXConfigError rather than crashing.
        project (str): Project name used to load context/selectors
            (e.g. "example"). Falls back to DEFAULT_PROJECT.
        dry_run (bool): When True, no external writes (Jira/Xray) happen.
            Carried here so it is never a global env var.
        jira (JiraCredentials): Jira credentials (may be unconfigured).
        xray (XrayCredentials): Xray credentials (may be unconfigured).
        model_heavy (str): Model id for large/high-risk files.
        model_light (str): Model id for small/low-risk files.
    """

    anthropic_key: str = ""
    project: str = ""
    dry_run: bool = True
    jira: JiraCredentials = field(default_factory=JiraCredentials)
    xray: XrayCredentials = field(default_factory=XrayCredentials)
    model_heavy: str = "claude-sonnet-4-5"
    model_light: str = "claude-haiku-4-5-20251001"

    @classmethod
    def from_env(cls, project: str | None = None, dry_run: bool | None = None) -> "QAXContext":
        """
        Build a QAXContext from the .env file / process environment.

        This preserves today's single-tenant behaviour: values come from the
        same environment variables the existing code already reads. Callers can
        override project and dry_run explicitly.

        Args:
            project (str | None): Project name. Falls back to DEFAULT_PROJECT.
            dry_run (bool | None): Override for dry-run. When None, reads the
                DRY_RUN env var (default True, matching the project's safety rule).

        Returns:
            QAXContext: A populated context object.
        """
        load_dotenv(override=True)

        if dry_run is None:
            dry_run = os.getenv("DRY_RUN", "true").strip().lower() == "true"

        return cls(
            anthropic_key=os.getenv("ANTHROPIC_API_KEY", ""),
            project=project or os.getenv("DEFAULT_PROJECT", ""),
            dry_run=dry_run,
            jira=JiraCredentials(
                base_url=os.getenv("JIRA_BASE_URL", ""),
                email=os.getenv("JIRA_EMAIL", ""),
                api_token=os.getenv("JIRA_API_TOKEN", ""),
            ),
            xray=XrayCredentials(
                client_id=os.getenv("XRAY_CLIENT_ID", ""),
                client_secret=os.getenv("XRAY_CLIENT_SECRET", ""),
            ),
            model_heavy=os.getenv("CLAUDE_MODEL_HEAVY", "claude-sonnet-4-5"),
            model_light=os.getenv("CLAUDE_MODEL_LIGHT", "claude-haiku-4-5-20251001"),
        )

    def require_anthropic_key(self) -> str:
        """
        Return the Anthropic key or raise QAXConfigError if it is missing.

        Returns:
            str: The Anthropic API key.

        Raises:
            QAXConfigError: If anthropic_key is empty.
        """
        # Imported here to avoid a circular import at module load time.
        from services.exceptions import QAXConfigError

        if not self.anthropic_key:
            raise QAXConfigError(
                "ANTHROPIC_API_KEY is not set. Add it to .env (single-tenant) "
                "or supply it on the QAXContext (multi-tenant)."
            )
        return self.anthropic_key
