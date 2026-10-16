"""
core/read_only.py — `--read-only`: a scan that provably sends no write request
==============================================================================
PLAN_TO_V1 D9: the first scan of somebody else's staging must not change it. Skipping a list of
"mutating" probes is not enough — read probes also self-register users (cookie-flags, crash-fuzz,
the utility login), send 12 wrong-password logins (rate-limit: can lock an account) or POST
payloads (xxe, graphql). So read-only is enforced where requests leave QAX:

  • `guard(session)` makes a requests.Session refuse every method except GET / HEAD / OPTIONS. The
    refused request is never sent: it raises ReadOnlyViolation, and is recorded with the probe that
    tried it (found from the call stack), so the report can say exactly what did not run and why;
  • `safe_cases(tests)` splits test cases into ones whose every api_step is a safe method and the
    rest, which are not executed.

Never raises outside the guarded call itself.
"""

from __future__ import annotations

import inspect
from urllib.parse import urlparse

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
NEEDS = "write-permission"


class ReadOnlyViolation(RuntimeError):
    """A write request was refused (and NOT sent) because the scan is read-only."""


def _caller_probe() -> str:
    """The probe on the call stack that issued the request ('auth-setup' outside any probe)."""
    try:
        for fr in inspect.stack()[2:40]:
            name = fr.function
            if name.endswith("_probe") and not name.startswith(("_", "test_")):
                return name[:-len("_probe")].replace("_", "-")
    except Exception:
        pass
    return "auth-setup"


def guard(sess):
    """Make `sess` refuse non-safe methods (idempotent). Returns the same session."""
    if getattr(sess, "_qax_read_only", False):
        return sess
    real = sess.request
    sess._qax_blocked = []

    def _request(method, url, *a, **kw):
        m = str(method or "GET").upper()
        if m not in SAFE_METHODS:
            path = urlparse(str(url)).path or "/"
            sess._qax_blocked.append({"probe": _caller_probe(), "method": m, "path": path})
            raise ReadOnlyViolation(f"QAX read-only: refused to send {m} {path}")
        return real(method, url, *a, **kw)

    sess.request = _request
    sess._qax_read_only = True
    return sess


def blocked(sess) -> list[dict]:
    """The write requests the guard refused on this session."""
    return list(getattr(sess, "_qax_blocked", []) or [])


def ledger_entries(blocked_requests: list[dict], existing: list[dict] | None = None) -> list[dict]:
    """
    Rewrite the not-tested ledger for a read-only run: every probe that had a write refused gets ONE
    honest entry (kind "read-only", needs write-permission) listing what was not sent, replacing the
    misleading side effects of the refusal (a probe that then reports "unreached" or "crashed").
    Entries of probes that were not blocked are kept as they are.
    """
    by_probe: dict[str, list[str]] = {}
    for b in blocked_requests or []:
        reqs = by_probe.setdefault(b.get("probe") or "auth-setup", [])
        sig = f"{b.get('method')} {b.get('path')}"
        if sig not in reqs:
            reqs.append(sig)
    out = [e for e in (existing or []) if isinstance(e, dict) and e.get("probe") not in by_probe]
    for probe, reqs in by_probe.items():
        shown = ", ".join(reqs[:3]) + (f" (+{len(reqs) - 3} more)" if len(reqs) > 3 else "")
        out.append({"probe": probe, "kind": "read-only", "needs": NEEDS,
                    "reason": f"not (fully) run: --read-only refused its write request(s) {shown}"})
    return out


def safe_cases(tests: list[dict]) -> tuple[list[dict], list[dict]]:
    """(cases whose every api_step uses a safe method, the rest)."""
    safe, unsafe = [], []
    for t in tests or []:
        steps = t.get("api_steps") if isinstance(t, dict) else None
        ok = isinstance(steps, list) and all(
            str((s or {}).get("method", "GET")).upper() in SAFE_METHODS for s in steps if isinstance(s, dict))
        (safe if ok else unsafe).append(t)
    return safe, unsafe
