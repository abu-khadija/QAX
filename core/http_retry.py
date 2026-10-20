"""
core/http_retry.py — bounded retries for calls to Jira / Xray
=============================================================
Jira Cloud and Xray Cloud answer 429 (rate limit) and 503 under load. One such answer used to
fail an export or a bug filing outright. This helper retries a call a few times with backoff,
honouring Retry-After (capped), and never retries in a way that can create a duplicate:

  * 429 and 503, and a connection that never opened → the server did not process the request:
    always safe to retry, also for a create (POST).
  * 500 / 502 / 504 and a read timeout → the server MAY have done the work. Retried only when the
    caller says the call is idempotent (a GET, a search). A create is not retried — the caller
    reports the failure and the operator decides (a duplicate Jira issue is worse than a retry).

Never raises on its own: the last response is returned, or the last exception re-raised so the
caller's existing error handling stays the same.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import requests

logger = logging.getLogger("qax.http")

_ALWAYS = (429, 503)
_IF_IDEMPOTENT = (500, 502, 504)
MAX_WAIT = 30.0


def _wait_for(resp, attempt: int) -> float:
    """Seconds to wait: Retry-After when given (capped), else 1, 2, 4 …"""
    try:
        ra = resp.headers.get("Retry-After") if resp is not None else None
        if ra:
            return max(0.0, min(float(ra), MAX_WAIT))
    except Exception:  # noqa: BLE001
        pass
    return float(min(2 ** attempt, 8))


def send(call: Callable[[], "requests.Response"], *, idempotent: bool = False, attempts: int = 3,
         what: str = "request", sleep: Callable[[float], None] = time.sleep):
    """
    Run `call()` (one HTTP request) with bounded retries.

    Args:
        call: A zero-argument function that sends the request and returns the response.
        idempotent: True for calls that are safe to repeat after a possible partial success.
        attempts: Total tries (default 3).
        what: Label for the log ("Xray import test").
        sleep: Injected for tests.

    Returns:
        The last response.

    Raises:
        The last exception when no attempt produced a response.
    """
    last_exc = None
    resp = None
    for attempt in range(max(1, attempts)):
        resp, last_exc = None, None
        try:
            resp = call()
        except requests.exceptions.ConnectTimeout as exc:   # never reached the server
            last_exc = exc
        except requests.exceptions.ReadTimeout as exc:      # the server may have done it
            last_exc = exc
            if not idempotent:
                raise
        except requests.exceptions.ConnectionError as exc:
            last_exc = exc
            # refused / DNS = never sent; a reset after sending may have been processed
            never_sent = any(t in str(exc) for t in ("NewConnectionError", "Failed to establish",
                                                     "Name or service not known", "nodename nor servname"))
            if not (idempotent or never_sent):
                raise
        if resp is not None:
            code = getattr(resp, "status_code", 0)
            if code not in _ALWAYS and not (idempotent and code in _IF_IDEMPOTENT):
                return resp
        if attempt + 1 >= attempts:
            break
        wait = _wait_for(resp, attempt)
        logger.warning("QAX: %s got %s — retrying in %.0f s (%d/%d)", what,
                       getattr(resp, "status_code", type(last_exc).__name__), wait, attempt + 1, attempts - 1)
        sleep(wait)
    if resp is not None:
        return resp
    raise last_exc  # type: ignore[misc]
