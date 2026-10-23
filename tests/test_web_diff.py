"""
test_web_diff.py — "what changed between two scans" in the web UI (POST /api/diff)
==================================================================================
Same rule as `qax diff`: a finding that is gone is only "likely fixed" when its check ran; when the
check did not run it is NOT RE-CHECKED, never a pass. The two saved scans are read by id, never a path.
"""
import json

import pytest

from web import server
from web.server import app


def _finding(title, target, sev="P2", probe="sqli"):
    return {"title": title, "target": target, "severity": sev, "source": "probe", "probe": probe}


def _save(tmp_path, rid, claims, not_tested=(), url="http://127.0.0.1:9"):
    (tmp_path / f"{rid}.json").write_text(json.dumps(
        {"run_id": rid, "scanned_at": "2026-10-23 10:00", "url": url, "claims": claims,
         "not_tested": list(not_tested)}), encoding="utf-8")


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_RUNS_DIR", str(tmp_path))
    monkeypatch.delenv("QAX_API_TOKEN", raising=False)
    return tmp_path


def _post(old, new):
    return app.test_client().post("/api/diff", json={"old": old, "new": new})


def test_new_still_and_gone_are_told_apart(runs):
    _save(runs, "a", [_finding("SQL injection in /a", "/a"), _finding("IDOR on /b", "/b", probe="bola")])
    _save(runs, "b", [_finding("IDOR on /b", "/b", probe="bola"), _finding("XSS in /c", "/c", probe="xss")])
    r = _post("a", "b")
    assert r.status_code == 200
    body = r.get_json()
    by = {row["title"]: row["bucket"] for row in body["rows"]}
    assert by == {"IDOR on /b": "STILL THERE", "XSS in /c": "NEW", "SQL injection in /a": "GONE"}
    assert body["counts"]["NEW"] == 1 and body["counts"]["GONE"] == 1 and body["counts"]["STILL THERE"] == 1
    gone = next(row for row in body["rows"] if row["bucket"] == "GONE")
    assert "build unknown" in gone["why"]                      # a web scan has no build id: say so, not "fixed"


def test_a_check_that_did_not_run_is_not_called_fixed(runs):
    _save(runs, "a", [_finding("SQL injection in /a", "/a")])
    _save(runs, "b", [], not_tested=[{"probe": "sqli", "kind": "read-only", "reason": "--read-only refused its writes"}])
    row = _post("a", "b").get_json()["rows"][0]
    assert row["bucket"] == "NOT RE-CHECKED" and "read-only" in row["why"]


def test_different_targets_are_warned(runs):
    _save(runs, "a", [], url="http://127.0.0.1:1")
    _save(runs, "b", [], url="http://127.0.0.1:2")
    assert any("different targets" in w for w in _post("a", "b").get_json()["warnings"])


def test_unknown_or_path_like_ids_are_a_404_not_a_file_read(runs):
    _save(runs, "a", [])
    assert _post("a", "missing").status_code == 404
    assert _post("a", "../../etc/passwd").status_code == 404
    assert _post("", "a").status_code == 404
    # a body that is not JSON never reaches the route: the server's own guard answers 415 (never a crash)
    assert app.test_client().post("/api/diff", data="not json").status_code in (404, 415)
