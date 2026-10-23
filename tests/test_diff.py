"""cli/diff.py — gone is not fixed unless the check really ran on a changed build."""
import json

from cli.diff import (ACCEPTED, FLAKY, GONE, NEW, NOT_RECHECKED, STILL, compare, render, run)


def _f(title, target="/x", probe="sqli", sev="P1", source="probe"):
    return {"title": title, "target": target, "probe": probe, "severity": sev, "source": source}


def _scan(confirmed, build="b1", **kw):
    d = {"target_url": "http://app", "scan_mode": "quick", "build": {"id": build} if build else None,
         "confirmed": confirmed, "not_tested": []}
    d.update(kw)
    return d


def _buckets(rep):
    return {r["finding"]["title"]: r["bucket"] for r in rep["rows"]}


def test_new_still_and_gone_on_a_changed_build():
    old = _scan([_f("A"), _f("B", sev="P2")])
    new = _scan([_f("B", sev="P1"), _f("C")], build="b2")
    rep = compare(old, new)
    assert _buckets(rep) == {"A": GONE, "B": STILL, "C": NEW}
    assert next(r for r in rep["rows"] if r["finding"]["title"] == "B")["why"] == "severity P2 → P1"


def test_gone_on_the_same_build_is_flaky_not_fixed():
    rep = compare(_scan([_f("A")]), _scan([]))
    assert _buckets(rep) == {"A": FLAKY} and "SAME build" in rep["rows"][0]["why"]


def test_gone_but_probe_did_not_run_is_not_rechecked():
    for kind in ("missing-prerequisite", "error", "read-only", "not-applicable"):
        new = _scan([], build="b2", not_tested=[{"probe": "bola", "kind": kind, "reason": "why"}])
        rep = compare(_scan([_f("A", probe="bola")]), new)
        assert _buckets(rep) == {"A": NOT_RECHECKED}, kind
    unstable = _scan([], build="b2", not_tested=[{"probe": "sqli", "kind": "unstable", "title": "A", "target": "/x"}])
    assert _buckets(compare(_scan([_f("A")]), unstable)) == {"A": NOT_RECHECKED}


def test_accepted_and_ai_leads_are_never_called_fixed():
    old = _scan([_f("A"), _f("lead", source="investigator")])
    new = _scan([], build="b2", accepted=[dict(_f("A"), accepted={"category": "by-design", "reason": "intended"})])
    rep = compare(old, new)
    assert _buckets(rep) == {"A": ACCEPTED} and rep["ai"] == {"old": 1, "new": 0}
    assert "AI leads (not compared" in render(rep)


def test_warnings_for_different_targets_and_modes():
    rep = compare(_scan([]), _scan([], target_url="http://other", scan_mode="deep"))
    assert len(rep["warnings"]) == 2


def test_cli_exit_codes(tmp_path, capsys):
    o, n = tmp_path / "o.json", tmp_path / "n.json"
    o.write_text(json.dumps(_scan([])), encoding="utf-8")
    n.write_text(json.dumps(_scan([_f("C", sev="P2")], build="b2")), encoding="utf-8")
    assert run([str(o), str(n)]) == 0
    assert run([str(o), str(n), "--fail-on-new", "P1,P2"]) == 1
    assert run([str(o), str(n), "--fail-on-new", "P1"]) == 0
    assert run([str(o), str(tmp_path / "missing.json")]) == 2
    capsys.readouterr()
    run([str(o), str(n), "--json"])
    assert json.loads(capsys.readouterr().out)["rows"][0]["bucket"] == NEW
