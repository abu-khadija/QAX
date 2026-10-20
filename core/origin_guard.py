"""
core/origin_guard.py — QAX stays on the app it was told to test
==============================================================
Test cases are partly written by an AI from untrusted text (stories, repository files, the
target app's own pages). A prompt-injected case could tell the browser to open another
site — or a cloud metadata address — and type a {{env:QAX_VAR_*}} secret into it. QAX's
API runner already refused off-origin requests; this module gives the browser runner and
the app reader the same rule:

- top-level navigation is allowed only to the app's origin (scheme, host and port of
  APP_BASE_URL) and to origins the operator lists in QAX_ALLOWED_ORIGINS (e.g. an SSO
  identity provider): "https://login.example.com,https://idp.example.com";
- a secret is typed only into a page (or frame) on an allowed origin;
- URLs are joined with urljoin and re-checked ("@evil.tld/", "//evil.tld" cannot escape).
Embedded third-party frames (a payment widget) and sub-resources (CDN scripts, images)
still load: only where the browser GOES and where secrets GO are restricted. Never raises.
"""

from __future__ import annotations

import os
from urllib.parse import urljoin, urlsplit


def _origin(url: str) -> str:
    """scheme://host:port of a URL ('' when it has none)."""
    try:
        u = urlsplit(url or "")
        if u.scheme not in ("http", "https") or not u.hostname:
            return ""
        port = u.port or (443 if u.scheme == "https" else 80)
        return f"{u.scheme}://{u.hostname.lower()}:{port}"
    except Exception:  # noqa: BLE001
        return ""


def allowed_origins(base_url: "str | None" = None) -> set:
    """The app's origin plus the operator's QAX_ALLOWED_ORIGINS ('' set = no target known)."""
    base = base_url if base_url is not None else os.getenv("APP_BASE_URL", "")
    out = {o for o in [_origin(base)] if o}
    for extra in os.getenv("QAX_ALLOWED_ORIGINS", "").split(","):
        o = _origin(extra.strip())
        if o:
            out.add(o)
    return out


def is_allowed(url: str, base_url: "str | None" = None) -> bool:
    """
    True when `url` may be visited / receive secrets. Non-network pages (about:blank, data:)
    are allowed; with no known target (APP_BASE_URL unset) nothing is restricted.
    """
    if not url or url.startswith(("about:", "data:", "blob:")):
        return True
    allowed = allowed_origins(base_url)
    if not allowed:
        return True
    return _origin(url) in allowed


def join(target: str, base_url: "str | None" = None) -> str:
    """The URL a navigate step means: absolute as given, relative joined onto the app's URL."""
    base = base_url if base_url is not None else os.getenv("APP_BASE_URL", "http://localhost")
    if (target or "").startswith(("http://", "https://")):
        return target
    base = base.rstrip("/") + "/"
    path = (target or "").lstrip()
    if path.startswith("/") and not path.startswith("//"):
        path = path[1:]
    return urljoin(base, path)


def refusal(url: str) -> str:
    """The message a refused navigation or secret gets."""
    return (f"refused: {_origin(url) or url[:60]} is not the app under test — QAX only navigates to and types "
            "secrets into the app's own origin (add trusted origins, e.g. an SSO provider, to QAX_ALLOWED_ORIGINS)")
