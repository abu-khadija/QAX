"""
services/progress.py — Progress Reporting
==========================================
Services report progress through a ProgressReporter instead of print().
This is the mechanism that lets one service drive four different faces:

    CLI   → PrintProgressReporter  (prints each step to the terminal)
    UI    → a custom reporter that streams progress to the web UI
    MCP   → a reporter that appends events to a list returned as JSON
    SaaS  → a reporter that writes events to a DB / websocket per tenant

Because the service only calls reporter.emit(...), swapping the face never
touches the service code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class ProgressEvent:
    """
    A single progress update emitted by a service.

    Attributes:
        step (str): Machine-friendly step key, e.g. "pre_check", "generate".
        status (str): One of "start", "ok", "warn", "error", "info".
        detail (str): Human-readable message.
        timestamp (str): UTC ISO-8601 timestamp of when the event was created.
    """

    step: str
    status: str
    detail: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        """Stamp the event with the current UTC time if not provided."""
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class ProgressReporter:
    """
    Base progress reporter. The default implementation records events to a
    list and logs them at debug level — it never prints.

    Subclass and override emit() to route events to a terminal, a UI widget,
    or a database.

    Usage:
        progress = ProgressReporter()
        progress.emit("generate", "start", "Calling Claude...")
        ...
        for event in progress.events:
            ...
    """

    def __init__(self) -> None:
        """Initialise with an empty event history."""
        self.events: list[ProgressEvent] = []

    def emit(self, step: str, status: str = "info", detail: str = "") -> None:
        """
        Record a progress event.

        Args:
            step (str): Machine-friendly step key.
            status (str): "start", "ok", "warn", "error", or "info".
            detail (str): Human-readable message.
        """
        event = ProgressEvent(step=step, status=status, detail=detail)
        self.events.append(event)
        self._handle(event)

    def _handle(self, event: ProgressEvent) -> None:
        """
        Hook for subclasses to react to a new event (print, update UI, etc.).

        The base implementation only logs at debug level.

        Args:
            event (ProgressEvent): The event that was just recorded.
        """
        logger.debug("progress[%s/%s] %s", event.step, event.status, event.detail)


class NullProgressReporter(ProgressReporter):
    """
    A reporter that discards everything. Use when progress is irrelevant
    (e.g. inside a unit test) but a reporter argument is still required.
    """

    def emit(self, step: str, status: str = "info", detail: str = "") -> None:
        """Do nothing — intentionally swallow all progress events."""
        return None


class PrintProgressReporter(ProgressReporter):
    """
    A reporter for the CLI face: prints each event to stdout in the same
    understated style the existing commands use.

    This keeps printing OUT of the services and IN the face that wants it.
    """

    _ICONS = {
        "start": "→",
        "ok": "✓",
        "warn": "!",
        "error": "✗",
        "info": " ",
    }

    def __init__(self, indent: str = "  ") -> None:
        """
        Args:
            indent (str): Leading whitespace for each printed line.
        """
        super().__init__()
        self.indent = indent

    def _handle(self, event: ProgressEvent) -> None:
        """Print the event with a small status icon."""
        icon = self._ICONS.get(event.status, " ")
        detail = f" {event.detail}" if event.detail else ""
        print(f"{self.indent}{icon} [{event.step}]{detail}")
