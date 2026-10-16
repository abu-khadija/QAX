"""core/accepted.py — known/by-design findings: strict, visible, expiring."""
import json
from datetime import date

from core import accepted as A
from core.jira_bugs import fingerprint

TODAY = date(2026, 10, 16)


def _entry(**kw):
    e = {"probe": "cors", "target": "/", "category": "by-design",
         "reason": "Public read-only API, CORS is intentionally open", "expires": "2027-03-01", "owner": "alice"}
    e.update(kw)
    return e


def _write(tmp_path, rows):
    p = tmp_path / "accepted.json"
    p.write_text(json.dumps({"accepted": rows}))
    return str(p)


def _finding(**kw):
    f = {"probe": "cors", "title": "CORS reflects an arbitrary Origin", "target": "/", "severity": "P2"}
    f.update(kw)
    return f


def test_valid_entry_loads_and_suppresses_with_reason(tmp_path):
    loaded = A.load(_write(tmp_path, [_entry()]), today=TODAY)
    assert len(loaded["entries"]) == 1 and not loaded["invalid"] and not loaded["error"]
    out = A.split([_finding(), _finding(probe="sqli", title="SQLi", target="/p")], loaded["entries"], today=TODAY)
    assert [f["probe"] for f in out["active"]] == ["sqli"]
    assert out["accepted"][0]["entry"]["reason"].startswith("Public read-only")
    text = A.render(out, loaded)
    assert "ACCEPTED" in text and "by-design" in text and "2027-03-01" in text


def test_entries_missing_reason_expiry_category_or_selector_are_rejected(tmp_path):
    bad = [_entry(reason="x"), _entry(expires=None), _entry(expires="soon"), _entry(category="whatever"),
           {"category": "by-design", "reason": "long enough reason", "expires": "2027-01-01"}, "nope"]
    loaded = A.load(_write(tmp_path, bad), today=TODAY)
    assert loaded["entries"] == [] and len(loaded["invalid"]) == len(bad)
    out = A.split([_finding()], loaded["entries"], today=TODAY)
    assert out["active"] == [_finding()] and not out["accepted"]            # nothing silently suppressed
    assert "REJECTED" in A.render(out, loaded)


def test_expiry_more_than_a_year_ahead_is_rejected(tmp_path):
    loaded = A.load(_write(tmp_path, [_entry(expires="2030-01-01")]), today=TODAY)
    assert loaded["entries"] == [] and "yearly" in loaded["invalid"][0]["why"]


def test_expired_entry_stops_suppressing_and_is_flagged(tmp_path):
    loaded = A.load(_write(tmp_path, [_entry(expires="2026-10-01")]), today=TODAY)
    out = A.split([_finding()], loaded["entries"], today=TODAY)
    assert len(out["active"]) == 1 and len(out["lapsed"]) == 1 and not out["accepted"]
    assert "LAPSED" in A.render(out, loaded)


def test_selectors_target_title_and_fingerprint(tmp_path):
    f = _finding(title="CORS reflects an arbitrary Origin with credentials", target="/api")
    fp = fingerprint(f["title"], f["target"])
    loaded = A.load(_write(tmp_path, [
        _entry(target="/other"),                                   # wrong target → no match
        _entry(probe="cors", target="/api", title_contains="WITHOUT"),   # wrong title → no match
        _entry(probe="", target="", fingerprint=fp)]), today=TODAY)
    out = A.split([f], loaded["entries"], today=TODAY)
    assert len(out["accepted"]) == 1 and out["accepted"][0]["entry"]["fingerprint"] == fp
    assert len(out["unused"]) == 2 and "UNUSED" in A.render(out, loaded)


def test_missing_default_file_is_silent_but_missing_explicit_file_is_reported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert A.load(None)["error"] is None
    bad = A.load(str(tmp_path / "nope.json"))
    assert bad["error"] and bad["entries"] == []
    (tmp_path / "broken.json").write_text("{not json")
    assert A.load(str(tmp_path / "broken.json"))["error"]


def test_pr_gate_does_not_fail_on_an_accepted_finding(tmp_path, monkeypatch):
    """End to end through ci/pr_scan.main with a stubbed scan."""
    import ci.pr_scan as pr
    from services.results import OrchestratorResult
    acc = _write(tmp_path, [_entry()])
    monkeypatch.setenv("QAX_TARGET_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("QAX_ACCEPTED", acc)
    monkeypatch.setenv("QAX_COMMENT_PATH", str(tmp_path / "c.md"))
    monkeypatch.setenv("QAX_JSON_PATH", str(tmp_path / "r.json"))
    res = OrchestratorResult(confirmed=[dict(_finding(), source="probe")])

    class _Orch:
        def __init__(self, *a, **k): pass
        def run(self, *a, **k): return res
    monkeypatch.setattr("services.orchestrator_service.OrchestratorService", _Orch)
    monkeypatch.setattr("core.build_fingerprint.build_fingerprint", lambda u: None)
    assert pr.main() == 0
    assert "Known / accepted findings" in (tmp_path / "c.md").read_text()
    # without the file the same finding blocks
    monkeypatch.setenv("QAX_ACCEPTED", str(tmp_path / "missing.json"))
    res2 = OrchestratorResult(confirmed=[dict(_finding(), source="probe")])
    monkeypatch.setattr(_Orch, "run", lambda self, *a, **k: res2)
    assert pr.main() == 1
