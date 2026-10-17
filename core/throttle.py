"""
core/throttle.py — behave when the target rate-limits us (PLAN.md Phase 1 item 5)
================================================================================
A scan sends hundreds of requests. Targets answer HTTP 429 ("slow down"), and a WAF may answer with a block page.
Before this layer a probe read a 429 as "rejected" and produced a wrong or empty verdict — a silent false negative.

`guard(sess)` wraps a requests.Session:
  • HTTP 429 (or 503 with Retry-After): wait the server's Retry-After (capped), retry a bounded number of times;
  • after a 429 the scan paces itself — a growing delay between requests, never faster than the server asked;
  • a hard budget (total wait, number of throttled answers): when it is spent the session STOPS sending and raises
    ScanThrottled, so the probe is recorded as "QAX stopped: the target is rate-limiting" instead of a quiet pass;
  • what happened is kept on `sess._qax_throttle` and reported (counts, seconds waited, stopped?).

It never tries to evade a limit or a WAF: no header spoofing, no rotation, no parallelism — it slows down and, past
its budget, stops and says so. Never raises except ScanThrottled.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

MAX_RETRIES = 2            # extra attempts per request after a 429
MAX_RETRY_WAIT = 15.0      # seconds: longest single wait, whatever Retry-After says
TOTAL_WAIT_BUDGET = 120.0  # seconds spent waiting across the whole session
THROTTLED_BUDGET = 40      # throttled (429) answers tolerated before the scan stops
PACE_START = 0.25          # seconds between requests once throttled; doubles per 429 up to PACE_MAX
PACE_MAX = 3.0

_WAF_HEADERS = ("cf-ray", "x-sucuri-id", "x-akamai-transformed", "x-iinfo", "x-amzn-waf-action")
_WAF_BODY = ("attention required", "access denied", "request blocked", "web application firewall",
             "you have been blocked", "reference #", "incident id", "sorry, you have been blocked")


class ScanThrottled(RuntimeError):
    """The target kept rate-limiting; QAX stopped sending requests (the scan is partial, and says so)."""


def _new_state() -> dict:
    return {"throttled": 0, "retries": 0, "waited": 0.0, "pace": 0.0, "ok_streak": 0, "stopped": False,
            "stop_reason": None, "waf_blocks": 0, "hosts": []}


def _retry_after(resp) -> float:
    """Seconds the server asked us to wait (Retry-After, in seconds; HTTP-dates are treated as the cap)."""
    try:
        raw = str(getattr(resp, "headers", {}).get("Retry-After", "")).strip()
        if not raw:
            return 1.0
        try:
            return max(0.0, float(raw))
        except ValueError:
            return MAX_RETRY_WAIT        # an HTTP-date: do not parse, wait the cap
    except Exception:
        return 1.0


def looks_like_waf_block(resp) -> bool:
    """A response that reads like a WAF/bot-protection block page (a signal to report, never to evade)."""
    try:
        if getattr(resp, "status_code", 0) not in (403, 406, 429, 503):
            return False
        hdrs = {str(k).lower() for k in (getattr(resp, "headers", {}) or {})}
        body = str(getattr(resp, "text", "") or "")[:4000].lower()
        server = str((getattr(resp, "headers", {}) or {}).get("Server", "")).lower()
        return bool((hdrs & set(_WAF_HEADERS)) or "cloudflare" in server or any(m in body for m in _WAF_BODY))
    except Exception:
        return False


def guard(sess, *, sleep=None):
    """Wrap `sess.request` with the throttle behaviour (idempotent). Returns the same session."""
    if getattr(sess, "_qax_throttle", None) is not None:
        return sess
    state = _new_state()
    sess._qax_throttle = state
    real = sess.request
    nap = sleep or time.sleep

    def _request(method, url, *a, **kw):
        if state["stopped"]:
            raise ScanThrottled(state["stop_reason"] or "the target is rate-limiting")
        host = urlparse(str(url)).netloc
        if host and host not in state["hosts"]:
            state["hosts"].append(host)
        attempt = 0
        while True:
            if state["pace"]:
                nap(state["pace"])
            resp = real(method, url, *a, **kw)
            code = getattr(resp, "status_code", 0)
            if looks_like_waf_block(resp):
                state["waf_blocks"] += 1
            if code != 429 and not (code == 503 and getattr(resp, "headers", {}).get("Retry-After")):
                if state["pace"] and code < 400:      # recovering: ease the pace down after 3 clean answers in a row
                    state["ok_streak"] += 1
                    if state["ok_streak"] >= 3:
                        state["ok_streak"] = 0
                        state["pace"] = state["pace"] / 2 if state["pace"] > PACE_START else 0.0
                return resp
            state["ok_streak"] = 0
            state["throttled"] += 1
            state["pace"] = min(PACE_MAX, max(PACE_START, state["pace"] * 2 or PACE_START))
            if state["throttled"] > THROTTLED_BUDGET:
                state["stopped"] = True
                state["stop_reason"] = (f"QAX stopped sending requests: the target answered HTTP 429 "
                                        f"{state['throttled']} times (budget {THROTTLED_BUDGET}). Re-run slower, or "
                                        f"allow-list the scanner, then scan again")
                raise ScanThrottled(state["stop_reason"])
            if attempt >= MAX_RETRIES:
                return resp                           # still limited: hand the 429 back, the probe will see it
            wait = min(MAX_RETRY_WAIT, _retry_after(resp))
            if state["waited"] + wait > TOTAL_WAIT_BUDGET:
                state["stopped"] = True
                state["stop_reason"] = (f"QAX stopped sending requests: waiting out the target's rate limit would "
                                        f"exceed {int(TOTAL_WAIT_BUDGET)} s in total. Re-run slower, or allow-list "
                                        f"the scanner, then scan again")
                raise ScanThrottled(state["stop_reason"])
            nap(wait)
            state["waited"] += wait
            state["retries"] += 1
            attempt += 1

    sess.request = _request
    return sess


def summary(sess) -> dict | None:
    """The throttle record of a session, or None when it never met a limit or a WAF."""
    st = getattr(sess, "_qax_throttle", None)
    if not st or not (st["throttled"] or st["waf_blocks"] or st["stopped"]):
        return None
    return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in st.items()}


def ledger_entries(states: list[dict]) -> list[dict]:
    """Not-tested ledger entries describing what throttling did to this scan (empty when nothing happened)."""
    out: list[dict] = []
    thr = sum(s.get("throttled", 0) for s in states)
    waited = sum(s.get("waited", 0.0) for s in states)
    stopped = [s for s in states if s.get("stopped")]
    waf = sum(s.get("waf_blocks", 0) for s in states)
    if stopped:
        out.append({"probe": "scan", "kind": "error", "needs": "slower-scan",
                    "reason": stopped[0]["stop_reason"] + ". Probes that ran after the limit began may be "
                              "inconclusive; probes that did not run are listed as crashed above"})
    elif thr:
        out.append({"probe": "scan", "kind": "not-applicable", "needs": None,
                    "reason": f"the target rate-limited QAX: {thr} request(s) were answered HTTP 429; QAX waited "
                              f"{waited:.0f} s in total and retried. Results are complete, the scan was slower"})
    if waf >= 3:
        out.append({"probe": "scan", "kind": "missing-prerequisite", "needs": "waf-allowlist",
                    "reason": f"{waf} response(s) look like a WAF / bot-protection block page. Results may be "
                              f"incomplete: allow-list the scanner's address, then scan again (QAX does not try to "
                              f"evade a WAF)"})
    return out
