"""tests for cli/file_findings.py — selecting and rendering findings filed to Jira."""

from cli.file_findings import render_results, select_findings

_RESULT = {"confirmed": [
    {"title": "Host header reflected", "severity": "P2", "source": "probe"},
    {"title": "CSP missing", "severity": "P3", "source": "probe"},
    {"title": "IDOR on /orders", "severity": "P1", "source": "coverage"},
    {"title": "Maybe XSS", "severity": "P2", "source": "test"},        # AI lead
]}


def test_select_only_deterministic_by_default():
    keep, skipped = select_findings(_RESULT)
    assert [b["title"] for b in keep] == ["Host header reflected", "CSP missing", "IDOR on /orders"]
    assert skipped == 1


def test_select_include_leads():
    keep, skipped = select_findings(_RESULT, include_leads=True)
    assert len(keep) == 4 and skipped == 0


def test_render_dry_run_shows_preview_and_dropped_fields():
    out = {"results": [{"success": True, "jira_key": "DRY-RUN-BUG-1", "dropped_fields": ["priority"],
                        "preview": {"fields": {"summary": "[QAX][P2] Host header reflected",
                                               "labels": ["qax", "qax-fp-abc"]}}}],
           "exported_count": 1, "duplicate_count": 0, "failed_count": 0}
    text = render_results(out, dry_run=True, project="SCRUM")
    assert "DRY RUN" in text and "would create Bug" in text
    assert "[QAX][P2] Host header reflected" in text and "omitted): priority" in text


def test_render_duplicate_and_failure():
    out = {"results": [{"success": True, "jira_key": "SCRUM-7", "duplicate_of": "SCRUM-7"},
                       {"success": False, "test_case_id": "X", "error": "HTTP 400"}],
           "exported_count": 0, "duplicate_count": 1, "failed_count": 1}
    text = render_results(out, dry_run=False, project="SCRUM")
    assert "already open as SCRUM-7" in text and "failed: HTTP 400" in text


def test_environment_from_scan_result_and_override():
    from cli.file_findings import environment_of
    r = {"target_url": "http://127.0.0.1:3200", "scan_mode": "quick", "scanned_at": "2026-10-15 10:00 UTC"}
    assert environment_of(r) == "http://127.0.0.1:3200 / QAX quick scan / 2026-10-15 10:00 UTC"
    assert environment_of(r, "Gitea 28.1.0 local") == "Gitea 28.1.0 local"
    assert "re-run qax scan" in environment_of({})


def test_enrich_adds_fix_and_qa_fields():
    from cli.file_findings import enrich
    b = enrich({"title": "x", "probe": "host-header-injection"})
    assert b["remediation"].get("fix") and "MUST" in b["qa"]["expected"]


def test_render_live_shows_summary_not_internal_id():
    out = {"results": [{"success": True, "jira_key": "SCRUM-5", "test_case_id": "QAX-X-1",
                        "summary": "[P2] [Admin] Host header reflected"}],
           "exported_count": 1, "duplicate_count": 0, "failed_count": 0}
    text = render_results(out, dry_run=False, project="SCRUM")
    assert "created SCRUM-5" in text and "[P2] [Admin] Host header reflected" in text
    assert "QAX-X-1" not in text


def test_render_shows_evidence_attachment_status():
    live_ok = {"results": [{"success": True, "jira_key": "SCRUM-9", "attachment": True, "summary": "s"}],
               "exported_count": 1, "duplicate_count": 0, "failed_count": 0}
    live_no = {"results": [{"success": True, "jira_key": "SCRUM-9", "attachment": False, "summary": "s"}],
               "exported_count": 1, "duplicate_count": 0, "failed_count": 0}
    dry = {"results": [{"success": True, "jira_key": "DRY", "would_attach": "evidence-x.txt", "summary": "s"}],
           "exported_count": 1, "duplicate_count": 0, "failed_count": 0}
    assert "evidence attached" in render_results(live_ok, dry_run=False, project="P")
    assert "evidence NOT attached" in render_results(live_no, dry_run=False, project="P")
    assert "attach evidence-x.txt" in render_results(dry, dry_run=True, project="P")
