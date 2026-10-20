"""
core/public_error.py — error text that is safe to show in the UI
================================================================
An exception's raw text can carry internal paths, URLs with credentials, tokens or stack
fragments. The web UI gets a short, cleaned message plus a reference; the full exception (with
its traceback) goes to the QAX log only, under the same reference, so an operator can find it.
Never raises.
"""

from __future__ import annotations

import logging
import re
import uuid

_PATH = re.compile(r"(?:(?<=\s)|(?<=^)|(?<=[\"'(=:]))(?:/(?:Users|home|private|tmp|var|opt|etc|root|usr|srv|app|mnt|Volumes)"
                   r"(?:/[^\s\"'),:]+)+|[A-Za-z]:\\[^\s\"'),]+)")
_CRED_URL = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I)
_SECRET = re.compile(r"(?i)\b(bearer|basic|token|api[_-]?key|apikey|password|passwd|secret|authorization|cookie|"
                     r"client[_-]?secret|access[_-]?token)(\s*[:=]\s*|\s+)([^\s,;\"'&]+)")
_SCHEME_TOKEN = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_LONG_HEX = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


def clean(text: str, limit: int = 200) -> str:
    """Strip internal paths, credentials in URLs, tokens and long opaque strings from a message."""
    try:
        s = " ".join(str(text or "").split())
        s = _CRED_URL.sub(r"\1•••@", s)
        s = _SCHEME_TOKEN.sub(lambda m: f"{m.group(1)} •••", s)
        s = _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}•••", s)
        s = _PATH.sub("<path>", s)
        s = _LONG_HEX.sub("•••", s)
        return s[:limit] + ("…" if len(s) > limit else "")
    except Exception:  # noqa: BLE001
        return "an internal error"


def public_error(prefix: str, exc: BaseException, logger: "logging.Logger | None" = None) -> str:
    """
    Log the full exception under a short reference and return a cleaned message for the UI.

    Args:
        prefix: What failed, in user words ("Scan failed").
        exc: The exception.
        logger: Where the full detail goes (default: the "qax" logger).

    Returns:
        str: "<prefix>: <cleaned message> (ref ab12cd34 — full detail in the QAX log)".
    """
    ref = uuid.uuid4().hex[:8]
    try:
        (logger or logging.getLogger("qax")).error("QAX: %s [ref %s]", prefix, ref, exc_info=exc)
    except Exception:  # noqa: BLE001
        pass
    detail = clean(str(exc)) or type(exc).__name__
    return f"{prefix}: {detail} (ref {ref} — full detail in the QAX log)"
