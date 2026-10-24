"""
services/usage.py — honest, global LLM token + cost accounting
==============================================================
QAX's LLM calls are scattered across ~13 modules, each with its own Anthropic
client. There is no single choke point, so a counter bolted onto one client would
undercount the biggest spenders (red-team, app-model, detectors, critic…) and
produce a MISLEADING cost number — exactly the fake-metric trap QAX must avoid.

This counts EVERY `messages.create()` by wrapping the Anthropic SDK method once,
so the total is complete no matter which module makes the call. `track()` installs
the wrapper (idempotent, degrades to a no-op if the SDK shape is unexpected),
`reset()` zeroes the counters for a fresh run, and `snapshot()` reports tokens,
calls and an APPROXIMATE USD estimate. Prices are a best-effort table, marked
approximate; they are a signal for "is the deep scan burning credits", not billing.
"""

from __future__ import annotations

import contextlib
import contextvars
import threading

# Approximate USD per 1,000,000 tokens (input, output). Update as prices change.
_PRICES = {
    "haiku": (0.80, 4.00),
    "sonnet": (3.00, 15.00),
    "opus": (15.00, 75.00),
}

_lock = threading.Lock()
_state: dict = {"input": 0, "output": 0, "calls": 0, "by_model": {}}
_patched = False
# Scoped meters (measure()): each counts the calls made in its own context — a functional run's
# usage is then its own, even while a scan runs in another thread. Calls made in a worker thread
# that did not copy the context are counted globally only (measure() reports them as untracked).
_meter: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar("qax_usage_meter", default=None)


def _model_key(model: str) -> str:
    """Map a full model id to a price bucket, defaulting to sonnet ("other" for a non-Claude provider)."""
    m = (model or "").lower()
    if m.startswith("other:"):
        return "other"
    for k in _PRICES:
        if k in m:
            return k
    return "sonnet"


def note(input_tokens: int, output_tokens: int, model: str = "") -> None:
    """Record one LLM call's token usage. Safe to call from any thread."""
    m = _meter.get()
    if m is not None:
        with _lock:
            m["input_tokens"] += int(input_tokens or 0)
            m["output_tokens"] += int(output_tokens or 0)
            m["calls"] += 1
            pin, pout = _PRICES.get(_model_key(model), (None, None))
            if pin is None:
                m["unpriced_calls"] += 1
            else:
                m["estimated_cost_usd"] = round(m["estimated_cost_usd"] + int(input_tokens or 0) / 1e6 * pin
                                                + int(output_tokens or 0) / 1e6 * pout, 4)
    with _lock:
        _state["input"] += int(input_tokens or 0)
        _state["output"] += int(output_tokens or 0)
        _state["calls"] += 1
        bm = _state["by_model"].setdefault(_model_key(model),
                                           {"input": 0, "output": 0, "calls": 0})
        bm["input"] += int(input_tokens or 0)
        bm["output"] += int(output_tokens or 0)
        bm["calls"] += 1


class AICallLimit(RuntimeError):
    """Raised before an AI call when QAX_AI_MAX_CALLS calls have already been made in this run."""


def check_cap() -> None:
    """
    Stop a runaway loop from spending without end: when QAX_AI_MAX_CALLS is a positive number and that many AI
    calls have been counted since the last reset(), raise AICallLimit. Unset, 0 or not a number means no cap.
    """
    import os
    try:
        cap = int(os.getenv("QAX_AI_MAX_CALLS", "0") or 0)
    except ValueError:
        return
    if cap <= 0:
        return
    with _lock:
        used = _state["calls"]
    if used >= cap:
        raise AICallLimit(f"QAX_AI_MAX_CALLS={cap} reached ({used} AI calls this run); raise it or unset it to continue")


def reset() -> None:
    """Zero the counters — call at the start of a run to measure just that run."""
    with _lock:
        _state["input"] = 0
        _state["output"] = 0
        _state["calls"] = 0
        _state["by_model"] = {}


def _estimate_cost_locked() -> float:
    """Cost of the current usage. Caller MUST already hold `_lock` (non-reentrant)."""
    total = 0.0
    for mk, bm in _state["by_model"].items():
        if mk not in _PRICES:       # another provider: counted, never priced (it was priced as Sonnet: $0.13 for a free tier)
            continue
        pin, pout = _PRICES[mk]
        total += bm["input"] / 1_000_000 * pin + bm["output"] / 1_000_000 * pout
    return round(total, 4)


def snapshot() -> dict:
    """A copy of the current totals plus the cost estimate."""
    with _lock:
        return {
            "input_tokens": _state["input"],
            "output_tokens": _state["output"],
            "calls": _state["calls"],
            "by_model": {k: dict(v) for k, v in _state["by_model"].items()},
            "estimated_cost_usd": _estimate_cost_locked(),
            "unpriced_calls": sum(bm["calls"] for mk, bm in _state["by_model"].items() if mk not in _PRICES),
        }


def track() -> bool:
    """
    Wrap the Anthropic SDK's Messages.create once so every call is counted,
    regardless of which module makes it. Idempotent; returns False (no-op) if the
    SDK isn't importable or has an unexpected shape, so instrumentation never
    breaks a scan.
    """
    global _patched
    if _patched:
        return True
    try:
        from anthropic.resources.messages import Messages
    except Exception:
        return False
    orig = Messages.create
    if getattr(orig, "_qax_wrapped", False):
        _patched = True
        return True

    def wrapped(self, *args, **kwargs):
        check_cap()
        resp = orig(self, *args, **kwargs)
        try:
            u = getattr(resp, "usage", None)
            if u is not None:
                note(getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0),
                     kwargs.get("model", ""))
        except Exception:
            pass
        return resp

    wrapped._qax_wrapped = True
    try:
        Messages.create = wrapped
    except Exception:
        return False
    _patched = True
    return True


@contextlib.contextmanager
def measure():
    """
    Count the LLM calls made inside the block (this context only) and yield the live totals:
    {"calls", "input_tokens", "output_tokens", "estimated_cost_usd", "unpriced_calls"}.
    Cost is APPROXIMATE (the price table above); calls to a non-Claude provider are counted
    but not priced (unpriced_calls). Installs the SDK wrapper on first use.
    """
    track()
    m = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0, "unpriced_calls": 0}
    tok = _meter.set(m)
    try:
        yield m
    finally:
        _meter.reset(tok)
