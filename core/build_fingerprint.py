"""
core/build_fingerprint.py — "which build was this?" for a running app
=====================================================================
re-verifying a fix on the WRONG build produced
false "still broken" verdicts, and a bug that vanishes on an UNCHANGED build is flakiness,
not a fix. So every scan records a build fingerprint, and `verify-fixes` compares it with
the one recorded when the ticket was filed.

The fingerprint is built ONLY from components that are STABLE — identical across two
consecutive fetches — so a per-request CSRF token, timestamp or nonce can never make the
same build look like a new one:
  • the API spec (its `info.version` and a hash of the whole document) — it changes when
    the API surface or version changes, which is exactly "a new build";
  • the root document: a hash of its body, and the Last-Modified / ETag / Server headers.
If nothing is stable the build is None (unknown) — never a made-up id.

Best-effort and network-safe: any failure → None.
"""

from __future__ import annotations

import hashlib
import json
import logging

import requests

logger = logging.getLogger(__name__)

_MAX_BODY = 1_000_000


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def _root_components(sess, base: str) -> dict:
    """One fetch of the root document → the candidate components (not yet known stable)."""
    try:
        r = sess.get(base + "/", timeout=10, allow_redirects=False)
    except Exception:
        return {}
    out: dict = {}
    body = (getattr(r, "text", "") or "")[:_MAX_BODY]
    if body:
        out["root-body"] = _sha(body)
    for h in ("last-modified", "etag", "server"):
        v = (getattr(r, "headers", {}) or {}).get(h)
        if v:
            out[f"hdr-{h}"] = str(v)
    return out


def _spec_components(base: str, sess) -> dict:
    """The spec's version + a canonical hash, when the app serves one."""
    try:
        from services.live_surface import fetch_openapi
        spec = fetch_openapi(base, session=sess)
    except Exception:
        spec = None
    if not isinstance(spec, dict):
        return {}
    out = {"spec-sha": _sha(json.dumps(spec, sort_keys=True, default=str))}
    ver = (spec.get("info") or {}).get("version")
    if ver:
        out["spec-version"] = str(ver)
    return out


def build_fingerprint(base_url: str, *, session: requests.Session | None = None) -> dict | None:
    """
    {"id": <12 hex>, "version": <str|None>, "components": [<names>]} or None when no
    component is stable. Two fetches are compared; only components equal in both count.
    """
    try:
        sess = session or requests.Session()
        base = base_url.rstrip("/")
        a = {**_spec_components(base, sess), **_root_components(sess, base)}
        b = {**_spec_components(base, sess), **_root_components(sess, base)}
        stable = {k: v for k, v in a.items() if b.get(k) == v}
        if not stable:
            return None
        ident = _sha("|".join(f"{k}={stable[k]}" for k in sorted(stable)))[:12]
        return {"id": ident, "version": stable.get("spec-version"), "components": sorted(stable)}
    except Exception as exc:
        logger.warning("QAX build fingerprint unavailable: %s", exc)
        return None


def same_build(a: dict | None, b: dict | None) -> bool | None:
    """True/False when both builds are known; None when either is unknown (never guess)."""
    if not a or not b or not a.get("id") or not b.get("id"):
        return None
    return a["id"] == b["id"]


def describe(build: dict | None) -> str:
    """Short human form: '7f3a9c1b2d4e (28.1.0)'."""
    if not build or not build.get("id"):
        return "unknown"
    return build["id"] + (f" ({build['version']})" if build.get("version") else "")
