"""
core/triage.py — why did a case not pass: the app, the test, or the environment?
================================================================================
A QA engineer's first question about a red result. Rules only — from signals QAX already
records — never an AI judgement:

  environment  the app did not answer (refused, DNS, timeout on opening a page), the browser
               crashed, the app rate-limited QAX, or the app server failed to start a page load
  test         the case named an element QAX never saw, an expectation QAX cannot read, a step
               that could not run, a navigation to another site, a failure after an unrunnable step,
               a missing element on step 1 when step 1 never opened a page
  app          the page or API answered and an assertion on it did not hold (expected vs actual),
               or the app answered a page / API call with a server error (5xx)
  unclear      a click/fill timed out on an element that should be there: a missing element is
               either an app change or a stale test — the evidence decides

Each result gets {"cause": ..., "cause_why": ...}. PASS gets none. Never raises.
"""

from __future__ import annotations

import re

_ENV = re.compile(r"ERR_CONNECTION_REFUSED|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_RESET|ERR_INTERNET_DISCONNECTED|"
                  r"no answer from the app|net::ERR_ADDRESS_UNREACHABLE|Connection refused|Max retries exceeded|"
                  r"Name or service not known|browser stopped|could not be restarted|worker crashed|"
                  r"rate-limited|QAX_VAR_[A-Z0-9_]+ is not set|Timeout \d+ms exceeded.*\n.*navigating to", re.I | re.S)
_TEST = re.compile(r"No element named|not in the project's selectors|is not a status code|could not run \(ERROR\)|"
                   r"refused: .* is not the app under test|not evaluated|Action '.*' not recognised|no steps", re.I)
_APP_ASSERT = re.compile(r"Text mismatch|URL '.*' missing|Expected status \d+, got|Element is (not )?|"
                         r"Element absent|Element is visible|Value mismatch|assertion failed", re.I)
_OPENS_A_PAGE = {"navigate", "goto", "go", "go to", "open", "visit", "browse", "load"}
_ACTS_ON_ONE_ELEMENT = {"click", "fill", "select", "hover", "type", "press"}
_MISSING = re.compile(r"Timeout \d+ms exceeded.*waiting for locator|Element not found", re.I | re.S)


def _texts(result: dict) -> str:
    """All the failure text of a result, first failing step first."""
    steps = [s for s in result.get("steps") or [] if s.get("status") not in ("PASS", "HEALED")]
    parts = [str(result.get("error") or "")]
    for s in steps[:3]:
        parts += [str(s.get("actual_result") or ""), str(s.get("error") or "")]
    return "\n".join(p for p in parts if p)


def classify(result: dict) -> dict:
    """Add `cause` and `cause_why` to a non-passing result (returns the same dict)."""
    try:
        status = result.get("status")
        if status in ("PASS", None):
            return result
        text = _texts(result)
        bad = next((s for s in result.get("steps") or [] if s.get("status") not in ("PASS", "HEALED")), {})
        code = bad.get("response_code")
        if result.get("throttled"):
            cause, why = "environment", "the app rate-limited QAX"
        elif _ENV.search(text):
            cause, why = "environment", "the app or the browser did not answer"
        elif _TEST.search(text):
            cause, why = "test", "the test itself could not run as written (fix the case, then re-run)"
        elif (str(bad.get("step_number")) == "1" and _MISSING.search(text)
              and str(bad.get("action_type") or "").lower() not in _OPENS_A_PAGE | {""}):   # a KNOWN non-visit action
            cause, why = "test", ("the case's first step is not a page visit, so no page was ever opened: "
                                  "add a navigate step (an AI-written case can omit it)")
        elif isinstance(code, int) and code >= 500:
            cause, why = "app", f"the app answered with a server error ({code})"
        elif (status == "FAIL" and re.search(r"Text mismatch", str(bad.get("actual_result") or ""), re.I)
              and str(bad.get("action_type") or "").lower() in _ACTS_ON_ONE_ELEMENT):
            cause, why = "unclear", ("a text check riding on a click/fill step reads THAT element's own text. If the case meant "
                                     "a message or heading the action should make appear, it needs its own assert step; "
                                     "if it meant this element, the app did not show it")
        elif status == "FAIL" and _APP_ASSERT.search(text):
            cause, why = "app", "the app answered and the expected result did not hold"
        elif status == "FAIL" and _MISSING.search(text):
            cause, why = "unclear", ("an element the case needs was not there — the app changed, or the case is "
                                     "stale; check the screenshot")
        elif status == "FAIL":
            cause, why = "app", "a step failed on a page the app served"
        else:
            cause, why = "test", "the case did not run to a verdict"
        if any(e.get("kind", "").startswith("HTTP 5") for e in result.get("page_errors") or []) and cause == "unclear":
            cause, why = "app", "the app returned a server error (5xx) while the case ran"
        result["cause"], result["cause_why"] = cause, why
    except Exception:  # noqa: BLE001
        pass
    return result


def summary(results: list) -> dict:
    """{"app": n, "test": n, "environment": n, "unclear": n} over non-passing results."""
    out = {"app": 0, "test": 0, "environment": 0, "unclear": 0}
    for r in results or []:
        c = r.get("cause")
        if c in out:
            out[c] += 1
    return out
