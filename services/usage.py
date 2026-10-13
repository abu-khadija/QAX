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


def _model_key(model: str) -> str:
    """Map a full model id to a price bucket, defaulting to sonnet."""
    m = (model or "").lower()
    for k in _PRICES:
        if k in m:
            return k
    return "sonnet"


def note(input_tokens: int, output_tokens: int, model: str = "") -> None:
    """Record one LLM call's token usage. Safe to call from any thread."""
    with _lock:
        _state["input"] += int(input_tokens or 0)
        _state["output"] += int(output_tokens or 0)
        _state["calls"] += 1
        bm = _state["by_model"].setdefault(_model_key(model),
                                           {"input": 0, "output": 0, "calls": 0})
        bm["input"] += int(input_tokens or 0)
        bm["output"] += int(output_tokens or 0)
        bm["calls"] += 1


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
        pin, pout = _PRICES.get(mk, _PRICES["sonnet"])
        total += bm["input"] / 1_000_000 * pin + bm["output"] / 1_000_000 * pout
    return round(total, 4)


def estimate_cost() -> float:
    """Approximate USD for the usage recorded so far, priced per model bucket."""
    with _lock:
        return _estimate_cost_locked()


def snapshot() -> dict:
    """A copy of the current totals plus the cost estimate."""
    with _lock:
        return {
            "input_tokens": _state["input"],
            "output_tokens": _state["output"],
            "calls": _state["calls"],
            "by_model": {k: dict(v) for k, v in _state["by_model"].items()},
            "estimated_cost_usd": _estimate_cost_locked(),
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
