"""
test_jira_verify.py — verify-fixes: FIXED / STILL REPRODUCES / COULD NOT TEST
============================================================================
Each safety rule is pinned on its own: a quiet probe is not clearance, a bug that vanishes
on an unchanged build is not a fix, a scan of another app proves nothing, a scan that tested
nothing verifies nothing — and --apply never touches anything but FIXED tickets.
"""

import json
from unittest.mock import MagicMock

import pytest

from core.jira_verify import (FIXED, STILL, UNKNOWN, JiraTicketReader, Ticket, adf_text, digest_of,
                              parse_ticket, summarize, verdict_for)

_T = "Host header reflected into a URL on GET /api/v1/admin/orgs"
_TARGET = "/api/v1/admin/orgs"
_BUILD_A = {"id": "aaaaaaaaaaaa", "version": "1.0"}
_BUILD_B = {"id": "bbbbbbbbbbbb", "version": "1.1"}


def _ticket(**kw):
    base = dict(key="SCRUM-5", summary="[P2] host header", status="To Do", status_category="new",
                digest=digest_of(_T, _TARGET), probe="host-header-injection", build=_BUILD_A,
                origin="http://127.0.0.1:3200", client_safe=False)
    base.update(kw)
    return Ticket(**base)


def _scan(confirmed=(), not_tested=(), build=_BUILD_B, url="http://127.0.0.1:3200", **kw):
    s = {"target_url": url, "build": build, "success": True, "legs": {"endpoints_grounded": 40},
         "confirmed": list(confirmed), "not_tested": list(not_tested)}
    s.update(kw)
    return s


def _finding(title=_T, target=_TARGET, source="probe"):
    return {"title": title, "target": target, "source": source, "probe": "host-header-injection", "severity": "P2"}


# ── STILL REPRODUCES ─────────────────────────────────────────────────────────

def test_still_reproduces_on_a_new_build_means_the_fix_did_not_work():
    v = verdict_for(_ticket(), _scan(confirmed=[_finding()], build=_BUILD_B))
    assert v.bucket == STILL and "NEW build" in v.reason and "did not work" in v.reason


def test_still_reproduces_on_the_same_build_means_not_deployed_yet():
    v = verdict_for(_ticket(), _scan(confirmed=[_finding()], build=_BUILD_A))
    assert v.bucket == STILL and "SAME build" in v.reason and "not been deployed" in v.reason


def test_still_reproduces_with_unknown_build_says_the_comparison_is_unavailable():
    v = verdict_for(_ticket(build=None), _scan(confirmed=[_finding()], build=None))
    assert v.bucket == STILL and "unavailable" in v.reason


def test_a_closed_ticket_that_still_reproduces_is_flagged():
    v = verdict_for(_ticket(status="Done", status_category="done"), _scan(confirmed=[_finding()]))
    assert v.bucket == STILL and v.closed_but_reproduces is True
    assert verdict_for(_ticket(), _scan(confirmed=[_finding()])).closed_but_reproduces is False


def test_ai_leads_never_count_as_reproduction():
    # Only deterministic (probe/coverage) findings can confirm a reproduction.
    v = verdict_for(_ticket(), _scan(confirmed=[_finding(source="test")]))
    assert v.bucket != STILL


# ── FIXED ────────────────────────────────────────────────────────────────────

def test_fixed_when_gone_the_probe_ran_cleanly_and_the_build_changed():
    v = verdict_for(_ticket(), _scan(build=_BUILD_B))
    assert v.bucket == FIXED and "the build changed" in v.reason and "ran and reported no gap" in v.reason


def test_fixed_with_unknown_build_carries_the_caveat_and_is_marked_weak():
    v = verdict_for(_ticket(build=None), _scan(build=None))
    assert v.bucket == FIXED and "build comparison unavailable" in v.reason and v.weak is True
    assert verdict_for(_ticket(), _scan(build=_BUILD_B)).weak is False          # a known build change is strong


# ── COULD NOT TEST (the honesty rules) ───────────────────────────────────────

@pytest.mark.parametrize("kind,fragment", [
    ("missing-prerequisite", "could not run"), ("error", "crashed"),
    ("not-applicable", "no longer applies"), ("unstable", "some passes but not all")])
def test_a_quiet_probe_with_any_ledger_entry_is_not_clearance(kind, fragment):
    entry = {"probe": "host-header-injection", "kind": kind, "reason": "why", "title": _T, "target": _TARGET}
    v = verdict_for(_ticket(), _scan(not_tested=[entry]))
    assert v.bucket == UNKNOWN and fragment in v.reason and "not clearance" in v.reason


def test_vanishing_on_an_unchanged_build_is_flakiness_not_a_fix():
    v = verdict_for(_ticket(), _scan(build=_BUILD_A))
    assert v.bucket == UNKNOWN and "UNCHANGED" in v.reason and "--reverify" in v.reason


def test_a_scan_of_a_different_app_proves_nothing():
    v = verdict_for(_ticket(), _scan(url="http://staging.example.com:8080"))
    assert v.bucket == UNKNOWN and "different app" in v.reason


def test_same_origin_ignores_case_and_a_trailing_path():
    assert verdict_for(_ticket(), _scan(url="HTTP://127.0.0.1:3200/api")).bucket == FIXED


def test_a_scan_that_tested_nothing_verifies_nothing():
    assert verdict_for(_ticket(), _scan(legs={"endpoints_grounded": 0})).bucket == UNKNOWN
    v = verdict_for(_ticket(), _scan(success=False, error="boom"))
    assert v.bucket == UNKNOWN and "failed" in v.reason


def test_a_ticket_without_a_probe_label_cannot_be_called_fixed():
    assert verdict_for(_ticket(probe=None), _scan()).bucket == UNKNOWN


def test_summary_counts_sum_to_the_total_and_are_never_merged():
    vs = [verdict_for(_ticket(), _scan(confirmed=[_finding()])), verdict_for(_ticket(), _scan()),
          verdict_for(_ticket(), _scan(build=_BUILD_A))]
    s = summarize(vs)
    assert (s[FIXED], s[STILL], s[UNKNOWN], s["total"]) == (1, 1, 1, 3)


# ── parsing a Jira issue ─────────────────────────────────────────────────────

def _issue(labels, desc_text="", key="SCRUM-9", status=("To Do", "new")):
    adf = {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": ln}]} for ln in desc_text.split("\n") if ln]}
    return {"key": key, "fields": {"summary": "s", "labels": labels, "description": adf,
            "status": {"name": status[0], "statusCategory": {"key": status[1]}}}}


def test_parse_ticket_reads_fingerprint_probe_build_and_origin():
    t = parse_ticket(_issue(["qax", "security", "severity-p2", "host-header-injection", "qax-fp-d5f797150276"],
                            "Build: 7f3a9c1b2d4e (28.1.0)\n1. Send GET http://127.0.0.1:3200/api/v1/x with a forged Host."))
    assert t.digest == "d5f797150276" and t.probe == "host-header-injection" and not t.client_safe
    assert t.build == {"id": "7f3a9c1b2d4e", "version": "28.1.0"} and t.origin == "http://127.0.0.1:3200"


def test_parse_ticket_recognises_client_safe_labels_and_ignores_foreign_tickets():
    t = parse_ticket(_issue(["security", "severity-p3", "security-headers", "fp-9abc8e40bdd7"]))
    assert t.client_safe and t.probe == "security-headers" and t.build is None and t.origin is None
    assert parse_ticket(_issue(["security", "bug"])) is None                      # not filed by QAX
    assert parse_ticket({"key": "X-1", "fields": {}}) is None


def test_the_digest_matches_what_filing_put_on_the_ticket():
    from core.jira_bugs import fingerprint
    assert fingerprint(_T, _TARGET).endswith(digest_of(_T, _TARGET))
    assert fingerprint(_T, _TARGET, client_safe=True).endswith(digest_of(_T, _TARGET))


def test_adf_text_flattens_paragraphs():
    assert adf_text({"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "a"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "b"}]}]}) == "a\nb\n"


# ── Jira I/O (fake) ──────────────────────────────────────────────────────────

class _FakeJira:
    def __init__(self, issues, transitions=None):
        self.issues, self.transitions, self.posts, self.gets = issues, transitions or [], [], []

    def post(self, url, json=None, auth=None, headers=None, timeout=None):
        self.posts.append((url, json))
        if url.endswith("/search/jql"):
            return MagicMock(status_code=200, json=lambda: {"issues": self.issues, "isLast": True})
        if url.endswith("/comment"):
            return MagicMock(status_code=201, json=lambda: {})
        if url.endswith("/transitions"):
            return MagicMock(status_code=204, json=lambda: {})
        return MagicMock(status_code=404, json=lambda: {}, text="")

    def get(self, url, params=None, auth=None, headers=None, timeout=None):
        self.gets.append(url)
        if url.endswith("/transitions"):
            return MagicMock(status_code=200, json=lambda: {"transitions": self.transitions})
        return MagicMock(status_code=404, json=lambda: {})


def _reader(fake):
    from core.jira_bugs import JiraBugFiler
    return JiraTicketReader(JiraBugFiler(dry_run=True, base_url="https://acme.atlassian.net", email="me@acme.io",
                                         token="tok", project_key="SCRUM", session=fake))


_DONE_TR = [{"id": "31", "name": "Done", "to": {"statusCategory": {"key": "done"}}},
            {"id": "11", "name": "To Do", "to": {"statusCategory": {"key": "new"}}}]


def test_list_tickets_keeps_only_qax_filed_ones():
    fake = _FakeJira([_issue(["security", "qax-fp-d5f797150276", "host-header-injection"], key="SCRUM-5"),
                      _issue(["security", "bug"], key="SCRUM-8")])
    assert [t.key for t in _reader(fake).list_tickets()] == ["SCRUM-5"]


def test_apply_dry_run_writes_nothing():
    fake = _FakeJira([], _DONE_TR)
    res = _reader(fake).apply_fixed([verdict_for(_ticket(), _scan())], env="http://x", dry_run=True)
    assert res[0]["key"] == "SCRUM-5" and res[0]["dry_run"] is True and "no longer reproduces" in res[0]["would"]["comment"]
    assert not [p for p in fake.posts if p[0].endswith(("/comment", "/transitions"))]


def test_apply_comments_and_moves_only_fixed_open_tickets():
    fake = _FakeJira([], _DONE_TR)
    fixed = verdict_for(_ticket(key="SCRUM-1"), _scan())
    still = verdict_for(_ticket(key="SCRUM-2"), _scan(confirmed=[_finding()]))
    unknown = verdict_for(_ticket(key="SCRUM-3"), _scan(build=_BUILD_A))
    already_done = verdict_for(_ticket(key="SCRUM-4", status="Done", status_category="done"), _scan())
    res = _reader(fake).apply_fixed([fixed, still, unknown, already_done], dry_run=False)
    assert [r["key"] for r in res] == ["SCRUM-1"]                       # nobody else is even considered
    urls = [p[0] for p in fake.posts]
    assert urls == ["https://acme.atlassian.net/rest/api/3/issue/SCRUM-1/comment",
                    "https://acme.atlassian.net/rest/api/3/issue/SCRUM-1/transitions"]
    assert fake.posts[1][1] == {"transition": {"id": "31"}} and res[0]["commented"] and res[0]["transitioned"]


def test_apply_never_picks_a_duplicate_transition_and_reports_when_none_exists():
    only_dup = [{"id": "41", "name": "Mark as Duplicate", "to": {"statusCategory": {"key": "done"}}}]
    res = _reader(_FakeJira([], only_dup)).apply_fixed([verdict_for(_ticket(), _scan())], dry_run=False)
    assert res[0]["transitioned"] is False and "no transition" in res[0]["error"]


def test_client_safe_ticket_comment_names_no_tool():
    fake = _FakeJira([], _DONE_TR)
    plain = _reader(fake).apply_fixed([verdict_for(_ticket(client_safe=True), _scan())], dry_run=True)[0]
    named = _reader(fake).apply_fixed([verdict_for(_ticket(client_safe=False), _scan())], dry_run=True)[0]
    assert "QAX" not in plain["would"]["comment"] and "QAX" in named["would"]["comment"]


# ── CLI ──────────────────────────────────────────────────────────────────────

def _run_cli(monkeypatch, tmp_path, scan, tickets, extra=()):
    import cli.verify_fixes as V
    p = tmp_path / "scan.json"; p.write_text(json.dumps(scan), encoding="utf-8")

    class _R:
        def __init__(self, *a, **k): pass
        def configured(self): return True
        def list_tickets(self, project=None): return tickets
        def apply_fixed(self, verdicts, env="", dry_run=True, include_unverified_build=False):
            return [{"key": v.ticket.key, "commented": True, "transitioned": True}
                    for v in verdicts if v.bucket == FIXED and not v.ticket.done]
    monkeypatch.setattr(V, "JiraTicketReader", _R)
    return V.run(["--from", str(p), *extra])


def test_cli_reports_three_separate_buckets(monkeypatch, tmp_path, capsys):
    tickets = [_ticket(key="SCRUM-1"), _ticket(key="SCRUM-2", digest="000000000000"), _ticket(key="SCRUM-3", digest="111111111111", probe=None)]
    rc = _run_cli(monkeypatch, tmp_path, _scan(confirmed=[_finding()]), tickets)
    out = capsys.readouterr().out
    assert rc == 0 and "STILL REPRODUCES (1)" in out and "FIXED (1)" in out and "COULD NOT TEST (1)" in out
    assert "fixed 1 · still reproduces 1 · could not test 1" in out and "never closes tickets on its own" in out


def test_cli_writing_needs_apply_AND_yes(monkeypatch, tmp_path, capsys):
    fixed_ticket = [_ticket(digest="000000000000")]
    _run_cli(monkeypatch, tmp_path, _scan(), fixed_ticket)
    assert "--apply" in capsys.readouterr().out                           # a hint only, nothing applied
    _run_cli(monkeypatch, tmp_path, _scan(), fixed_ticket, extra=("--apply",))
    out = capsys.readouterr().out                                         # the PLAN: nothing written
    assert "DRY RUN — nothing written" in out and "add --yes to write" in out and "would comment + move to Done" in out
    _run_cli(monkeypatch, tmp_path, _scan(), fixed_ticket, extra=("--apply", "--yes"))
    out = capsys.readouterr().out
    assert "--apply · APPLIED: 1 FIXED ticket(s)" in out and "commented=True moved=True" in out


def test_cli_says_nothing_to_close_when_there_is_no_fixed_ticket(monkeypatch, tmp_path, capsys):
    _run_cli(monkeypatch, tmp_path, _scan(confirmed=[_finding()]), [_ticket()])
    out = capsys.readouterr().out
    assert "Nothing to close" in out and "review the ✅ list" not in out


def test_the_reason_says_which_side_of_the_build_comparison_is_missing():
    old_ticket = verdict_for(_ticket(build=None), _scan(confirmed=[_finding()], build=_BUILD_B))
    assert "filed before QAX recorded builds" in old_ticket.reason
    no_scan_build = verdict_for(_ticket(), _scan(confirmed=[_finding()], build=None))
    assert "could not fingerprint" in no_scan_build.reason
    neither = verdict_for(_ticket(build=None), _scan(confirmed=[_finding()], build=None))
    assert "neither the ticket nor this scan" in neither.reason


def test_cli_fail_on_regression_exits_2_only_for_closed_but_reproducing(monkeypatch, tmp_path, capsys):
    closed = _ticket(status="Done", status_category="done")
    assert _run_cli(monkeypatch, tmp_path, _scan(confirmed=[_finding()]), [closed], extra=("--fail-on-regression",)) == 2
    assert "CLOSED BUT NOT FIXED" in capsys.readouterr().out
    assert _run_cli(monkeypatch, tmp_path, _scan(confirmed=[_finding()]), [_ticket()], extra=("--fail-on-regression",)) == 0


def test_cli_requires_exactly_one_scan_source_and_authorization(monkeypatch, tmp_path, capsys):
    import cli.verify_fixes as V
    assert V.run([]) == 1 and V.run(["--from", "a.json", "--url", "http://x"]) == 1
    assert V.run(["--url", "http://x"]) == 1 and "--authorized" in capsys.readouterr().out


def test_apply_skips_a_fix_that_cannot_be_tied_to_a_deploy_unless_asked():
    fake = _FakeJira([], _DONE_TR)
    weak = verdict_for(_ticket(key="SCRUM-7", build=None), _scan(build=None))
    strong = verdict_for(_ticket(key="SCRUM-8"), _scan(build=_BUILD_B))
    res = _reader(fake).apply_fixed([weak, strong], dry_run=False)
    assert [r["key"] for r in res] == ["SCRUM-7", "SCRUM-8"]
    assert "build unverified" in res[0]["skipped"] and not res[0]["commented"]
    assert res[1]["commented"] and res[1]["transitioned"]
    assert [p[0].split("/")[-2] for p in fake.posts if p[0].endswith("/comment")] == ["SCRUM-8"]   # only the strong one
    forced = _reader(_FakeJira([], _DONE_TR)).apply_fixed([weak], dry_run=False, include_unverified_build=True)
    assert forced[0]["commented"] and "skipped" not in forced[0]


def test_summary_and_report_count_unverified_fixes_separately():
    vs = [verdict_for(_ticket(build=None), _scan(build=None)), verdict_for(_ticket(), _scan(build=_BUILD_B))]
    s = summarize(vs)
    assert s[FIXED] == 2 and s["fixed_build_unverified"] == 1
    import cli.verify_fixes as V
    text = V.render_report(vs, s, _scan(), applied=[], dry_run=True)
    assert "build unverified" in text and "--apply skips them" in text
