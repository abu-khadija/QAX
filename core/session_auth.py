"""
core/session_auth.py — credentials that live on the SESSION, not on one request
==============================================================================
A session cookie and static headers (a CSRF nonce, a tenant id, an API version) must accompany EVERY request QAX
sends, from every component: the probes, the family sweep, the executed coverage tests and discovery. One helper
puts them on a requests.Session so no component can forget (the cookie used to reach the probes only).
"""

from __future__ import annotations


def parse_headers(items) -> dict:
    """["Name: value", ...] -> {"Name": "value"}. Bad entries are ignored."""
    out: dict = {}
    for raw in items or []:
        if ":" in str(raw):
            k, v = str(raw).split(":", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def apply_extras(sess, auth: dict | None):
    """Seed the supplied cookie jar entries and static headers onto `sess`. Returns `sess`. Never raises."""
    try:
        auth = auth or {}
        for part in str(auth.get("cookie") or "").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip():
                    sess.cookies.set(k.strip(), v.strip())
        for k, v in (auth.get("headers") or {}).items():
            sess.headers[k] = v
        if auth.get("insecure"):          # --insecure: the operator accepts an unverified certificate for THIS target
            sess.verify = False
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
    except Exception:
        pass
    return sess
