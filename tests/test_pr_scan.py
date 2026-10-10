"""test_pr_scan.py — CI PR-comment formatting (pure logic)."""

import os

from ci.pr_scan import format_comment, main


def _findings():
    return [
        {"severity": "P1", "probe": "sqli", "title": "SQL injection in GET /users/{id}",
         "target": "/users/{id}", "evidence": "a quote broke the query"},
        {"severity": "P3", "probe": "security-headers", "title": "Response security headers missing",
         "target": "/"},
    ]


def test_format_splits_claims_and_leads():
    md, claims, leads = format_comment("http://app", _findings(), duration=7.0)
    assert len(claims) == 1 and len(leads) == 1
    assert "confirmed issue" in md.lower()
    assert "Confirmed — proven exploitable" in md
    assert "Leads — flagged, not confirmed" in md
    assert "/users/{id}" in md
    # remediation for sqli gets pulled in (How to fix)
    assert "How to fix" in md


def test_format_clean_when_no_claims():
    md, claims, leads = format_comment("http://app", [
        {"severity": "P3", "probe": "security-headers", "title": "headers missing", "target": "/"}])
    assert claims == [] and len(leads) == 1
    assert "No confirmed issues" in md


def test_format_handles_empty():
    md, claims, leads = format_comment("http://app", [])
    assert claims == [] and leads == [] and "No confirmed issues" in md


def test_main_no_url_does_not_block(tmp_path, monkeypatch):
    comment = tmp_path / "c.md"
    monkeypatch.setenv("QAX_TARGET_URL", "")
    monkeypatch.setenv("QAX_COMMENT_PATH", str(comment))
    assert main() == 0                      # never blocks when there's no target
    assert "skipping" in comment.read_text().lower()
