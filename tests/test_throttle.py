"""core/throttle.py — a rate-limited target is waited out, then (past the budget) the scan stops and says so."""
import pytest
from unittest.mock import MagicMock

from core import throttle as T


class Fake:
    """A session whose answers come from a script; records the order of calls and every nap."""
    def __init__(self, answers):
        self.answers = list(answers); self.calls = 0
    def request(self, method, url, *a, **k):
        self.calls += 1
        a = self.answers.pop(0) if self.answers else (200, {})
        return MagicMock(status_code=a[0], headers=a[1], text=a[2] if len(a) > 2 else "")


def _guard(answers):
    naps = []
    s = T.guard(Fake(answers), sleep=naps.append)
    return s, naps


def test_a_429_is_waited_out_with_retry_after_and_retried():
    s, naps = _guard([(429, {"Retry-After": "2"}), (200, {})])
    r = s.request("GET", "http://t/x")
    assert r.status_code == 200 and s.calls == 2
    assert 2.0 in naps                                              # honoured the server's Retry-After
    st = T.summary(s)
    assert st["throttled"] == 1 and st["retries"] == 1 and st["waited"] == 2.0 and not st["stopped"]


def test_a_retry_wait_is_capped_and_pacing_grows_then_eases():
    s, naps = _guard([(429, {"Retry-After": "999"}), (200, {})])
    s.request("GET", "http://t/x")
    assert max(naps) == T.MAX_RETRY_WAIT                            # never waits an hour because a header said so
    assert s._qax_throttle["pace"] > 0
    assert s._qax_throttle["pace"] == T.PACE_START                  # still pacing right after a 429
    for _ in range(3):
        s.answers.append((200, {})); s.request("GET", "http://t/y")
    assert s._qax_throttle["pace"] == 0                             # 3 clean answers in a row: back to full speed


def test_still_limited_after_the_retries_hands_the_429_back():
    s, _ = _guard([(429, {"Retry-After": "1"})] * (T.MAX_RETRIES + 1))
    assert s.request("GET", "http://t/x").status_code == 429 and s.calls == T.MAX_RETRIES + 1


def test_the_scan_stops_past_the_throttled_budget_and_says_so():
    s, _ = _guard([(429, {"Retry-After": "0"})] * 500)
    with pytest.raises(T.ScanThrottled) as e:
        for i in range(500):
            s.request("GET", f"http://t/{i}")
    assert "stopped sending requests" in str(e.value) and s._qax_throttle["stopped"]
    with pytest.raises(T.ScanThrottled):                            # and it STAYS stopped
        s.request("GET", "http://t/again")
    entries = T.ledger_entries([T.summary(s)])
    assert entries[0]["kind"] == "error" and entries[0]["needs"] == "slower-scan"


def test_total_wait_budget_stops_the_scan():
    s, _ = _guard([(429, {"Retry-After": "15"})] * 100)
    with pytest.raises(T.ScanThrottled) as e:
        for i in range(100):
            s.request("GET", f"http://t/{i}")
    assert "total" in str(e.value)


def test_a_quiet_session_reports_nothing_and_guard_is_idempotent():
    s, _ = _guard([(200, {})])
    s.request("GET", "http://t/x")
    assert T.summary(s) is None and T.ledger_entries([]) == []
    assert T.guard(s) is s


def test_waf_block_pages_are_detected_and_reported_but_never_evaded():
    waf = (403, {"cf-ray": "abc", "Server": "cloudflare"}, "Attention Required! | Cloudflare")
    s, _ = _guard([waf] * 4)
    for i in range(4):
        s.request("GET", f"http://t/{i}")
    st = T.summary(s)
    assert st["waf_blocks"] == 4 and s.calls == 4                   # 4 requests, no retry/rotation tricks
    e = [x for x in T.ledger_entries([st]) if x["needs"] == "waf-allowlist"]
    assert e and "does not try to evade" in e[0]["reason"]
    assert not T.looks_like_waf_block(MagicMock(status_code=403, headers={}, text="Forbidden: not your record"))


def test_some_throttling_that_clears_is_reported_as_slower_not_incomplete():
    s, _ = _guard([(429, {"Retry-After": "1"}), (200, {})])
    s.request("GET", "http://t/x")
    e = T.ledger_entries([T.summary(s)])
    assert len(e) == 1 and e[0]["kind"] == "not-applicable" and "Results are complete" in e[0]["reason"]
