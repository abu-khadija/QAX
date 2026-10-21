"""
test_jira_bugs.py — filing findings as Jira Bugs (B6)
=====================================================
Locks in the gaps found by the first real filing attempt on a fresh Jira Cloud
team-managed project (2026-10-15): screen-aware payloads (no `priority` on a
screen that lacks it → no HTTP 400), fingerprint de-dup, an honest dry run that
shows the exact payload, and no Xray credentials needed for a plain Bug.

Run with:
    pytest tests/test_jira_bugs.py -v
"""

from unittest.mock import MagicMock

from core.jira_bugs import JiraBugFiler, finding_to_bug, fingerprint

_TEAM_MANAGED_SCREEN = ["issuetype", "project", "reporter", "summary", "description",
                        "labels", "assignee", "attachment"]          # no priority


class FakeJira:
    """Records calls; serves createmeta, JQL search and issue create."""

    def __init__(self, screen=_TEAM_MANAGED_SCREEN, has_bug=True, existing=None):
        self.screen, self.has_bug, self.existing = screen, has_bug, existing
        self.created = []
        self.posts = []

    def get(self, url, params=None, auth=None, headers=None, timeout=None):
        if url.endswith("/issuetypes"):
            types = [{"name": "Task", "id": "1"}] + ([{"name": "Bug", "id": "9"}] if self.has_bug else [])
            return MagicMock(status_code=200, json=lambda: {"issueTypes": types})
        if url.endswith("/issuetypes/9"):
            return MagicMock(status_code=200,
                             json=lambda: {"fields": [{"fieldId": f} for f in self.screen]})
        return MagicMock(status_code=404, json=lambda: {})

    def post(self, url, json=None, auth=None, headers=None, timeout=None, files=None):
        self.posts.append(url)
        if url.endswith("/attachments"):
            self.attached = getattr(self, "attached", []) + [(url, files, headers)]
            return MagicMock(status_code=200, json=lambda: [{"id": "1"}])
        if url.endswith("/search/jql"):
            issues = [{"key": self.existing}] if self.existing else []
            return MagicMock(status_code=200, json=lambda: {"issues": issues})
        if url.endswith("/rest/api/3/issue"):
            self.created.append(json)
            return MagicMock(status_code=201, json=lambda: {"key": f"SCRUM-{len(self.created)}"})
        return MagicMock(status_code=404, json=lambda: {}, text="nope")


def _filer(dry_run, jira):
    return JiraBugFiler(dry_run=dry_run, base_url="https://acme.atlassian.net",
                        email="me@acme.io", token="tok", project_key="SCRUM", session=jira)


_FINDING = {"title": "Host header reflected into a URL on GET /api/v1/admin/orgs",
            "severity": "P2", "probe": "host-header-injection",
            "target": "/api/v1/admin/orgs",
            "evidence": "A forged Host 'qax123.evil.invalid' was used to build a URL."}


def test_finding_to_bug_carries_severity_and_stable_fingerprint():
    b = finding_to_bug(_FINDING)
    # Owner's summary grammar: [Module] symptom (root-cause class), severity first.
    assert b["title"] == ("[P2] [Admin] Host header reflected into a URL on GET "
                          "/api/v1/admin/orgs (host-header-injection)")
    assert "severity-p2" in b["labels"] and "host-header-injection" in b["labels"]
    # Per-run evidence (random host) must NOT change the fingerprint.
    b2 = finding_to_bug({**_FINDING, "evidence": "A forged Host 'qax999.evil.invalid'…"})
    assert b["fingerprint"] == b2["fingerprint"] == fingerprint(_FINDING["title"], _FINDING["target"])


def test_payload_drops_priority_when_screen_lacks_it():
    jira = FakeJira()
    res = _filer(True, jira).file(finding_to_bug(_FINDING))
    fields = res["preview"]["fields"]
    assert "priority" not in fields and res["dropped_fields"] == ["priority"]
    assert fields["issuetype"] == {"name": "Bug"} and fields["project"] == {"key": "SCRUM"}
    assert any(l.startswith("qax-fp-") for l in fields["labels"])


def test_payload_keeps_priority_when_on_screen():
    jira = FakeJira(screen=_TEAM_MANAGED_SCREEN + ["priority"])
    res = _filer(True, jira).file(finding_to_bug(_FINDING))
    assert res["preview"]["fields"]["priority"] == {"name": "High"}       # P2 → High
    assert res["dropped_fields"] == []


def test_dry_run_never_creates_an_issue():
    jira = FakeJira()
    res = _filer(True, jira).file(finding_to_bug(_FINDING))
    assert res["success"] and res["jira_key"].startswith("DRY-RUN-BUG-")
    assert jira.created == []
    assert not any(u.endswith("/rest/api/3/issue") for u in jira.posts)


def test_live_files_bug_with_screen_safe_payload():
    jira = FakeJira()
    res = _filer(False, jira).file(finding_to_bug(_FINDING))
    assert res["success"] and res["jira_key"] == "SCRUM-1"
    assert "priority" not in jira.created[0]["fields"]


def test_duplicate_open_issue_is_not_filed_again():
    jira = FakeJira(existing="SCRUM-7")
    res = _filer(False, jira).file(finding_to_bug(_FINDING))
    assert res["success"] and res["duplicate_of"] == "SCRUM-7"
    assert jira.created == []


def test_live_refuses_when_project_has_no_bug_type():
    jira = FakeJira(has_bug=False)
    res = _filer(False, jira).file(finding_to_bug(_FINDING))
    assert not res["success"] and "Bug" in res["error"] and jira.created == []


def test_unconfigured_dry_run_uses_safe_fields_and_makes_no_calls():
    jira = FakeJira()
    f = JiraBugFiler(dry_run=True, base_url="https://your-domain.atlassian.net",
                     email="your-email@company.com", token="your-api-token",
                     project_key="SCRUM", session=jira)
    assert not f.configured()
    res = f.file(finding_to_bug(_FINDING))
    assert res["success"] and "priority" not in res["preview"]["fields"]
    assert jira.posts == []


def test_file_many_counts_new_vs_duplicates():
    jira = FakeJira(existing="SCRUM-7")
    out = _filer(False, jira).file_many([finding_to_bug(_FINDING)])
    assert out["duplicate_count"] == 1 and out["exported_count"] == 0 and out["success"]


def test_xray_client_push_bug_needs_no_xray_credentials(monkeypatch):
    # Plain Bug filing must not be blocked by missing XRAY_CLIENT_ID/SECRET.
    from core.xray_client import XrayClient
    monkeypatch.setenv("JIRA_PROJECT_KEY", "SCRUM")
    # Placeholder base URL → unconfigured → no network, even if the dev's .env has
    # real Jira creds (load_dotenv never overrides an already-set variable).
    monkeypatch.setenv("JIRA_BASE_URL", "https://your-domain.atlassian.net")
    monkeypatch.delenv("XRAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("XRAY_CLIENT_SECRET", raising=False)
    res = XrayClient(dry_run=True).push_bug(finding_to_bug(_FINDING))
    assert res["success"] and "preview" in res


# ── Compact security-bug format ──────────────────────────────────────────────

def _enriched():
    from services.remediation import qa_for, remediation_for
    b = dict(_FINDING)
    b["remediation"] = remediation_for(b)
    b["qa"] = qa_for(b)
    return b


def test_bug_follows_compact_format_sections():
    bug = finding_to_bug(_enriched(), environment="Gitea 28.1.0 @ http://127.0.0.1:3200")
    d = bug["description"]
    for section in ("Severity: P2 / High —", "Environment: Gitea 28.1.0", "Location: Admin — GET /api/v1/admin/orgs",
                    "Steps to Reproduce", "Expected Result", "MUST", "Actual Result",
                    "Fix Recommendation", "Regression Test", "Notes"):
        assert section in d, section
    assert "Read-only reproduction" in d                     # data-safety note (non-negotiable #7)
    assert "forged Host / X-Forwarded-Host" in d             # probe-specific repro condition
    assert bug["fingerprint"] in d                           # regression gate references it


def test_bug_adf_uses_headings_list_and_code_block():
    adf = finding_to_bug(_enriched())["adf"]
    kinds = [n["type"] for n in adf["content"]]
    assert "heading" in kinds and "orderedList" in kinds and "codeBlock" in kinds
    code = next(n for n in adf["content"] if n["type"] == "codeBlock")
    assert "evil.invalid" in code["content"][0]["text"]       # evidence verbatim in the block
    first_para = next(n for n in adf["content"] if n["type"] == "paragraph" and n["content"][0].get("marks"))
    bold = first_para["content"][0]
    assert bold["text"] == "Severity: " and bold["marks"] == [{"type": "strong"}]


def test_payload_sends_rich_adf_description():
    res = _filer(True, FakeJira()).file(finding_to_bug(_enriched()))
    kinds = [n["type"] for n in res["preview"]["fields"]["description"]["content"]]
    assert "paragraph" in kinds and "heading" in kinds


def test_mutating_probe_gets_disposable_records_note():
    from services.remediation import qa_for
    assert "disposable records" in qa_for({"probe": "bola"})["data_safety"]
    assert qa_for({"probe": "unknown-probe"}) == {}


def test_observation_finding_steps_have_no_bogus_control():
    from services.remediation import qa_for, remediation_for
    f = {"title": "Response security headers missing: Content-Security-Policy", "severity": "P3",
         "probe": "security-headers", "target": "/", "evidence": "did not set: CSP"}
    f["remediation"], f["qa"] = remediation_for(f), qa_for(f)
    d = finding_to_bug(f)["description"]
    assert "1. Send GET /." in d and "2. Inspect the response headers." in d
    assert "WITHOUT that condition" not in d


def test_observation_with_actor_keeps_actor_in_step():
    from services.remediation import qa_for
    f = {"title": "Excessive data on GET /api/users/me", "probe": "excessive-data", "target": "/api/users/me"}
    f["qa"] = qa_for(f)
    d = finding_to_bug(f)["description"]
    assert "1. Send GET /api/users/me as an ordinary user." in d
    assert "2. Inspect the response fields." in d


# ── The 13 mandatory fields, client-safe mode, evidence attachment ──

def test_bug_covers_the_13_fields_with_a_direct_url_step_one():
    bug = finding_to_bug(_enriched(), environment="Gitea 28.1.0 local", base_url="http://127.0.0.1:3200")
    d = bug["description"]
    for needed in ("Summary", "Severity: P2 / High —", "Environment: Gitea 28.1.0 local",
                   "Location: Admin — GET /api/v1/admin/orgs", "Reproducibility: Reproduced 1/1 attempts",
                   "Workaround: None known — not assessed", "Steps to Reproduce", "Expected Result",
                   "Actual Result", "Console Errors", "Screenshot/Attachments"):
        assert needed in d, needed
    assert "1. Send GET http://127.0.0.1:3200/api/v1/admin/orgs " in d      # step 1 = a direct URL


def test_reproducibility_states_only_what_was_measured():
    assert "Reproduced 1/1 attempts" in finding_to_bug(_enriched())["description"]
    measured = finding_to_bug(_enriched(), environment="E", reproduced=(3, 3))["description"]
    assert "Reproduced 3/3 attempts on E." in measured and "Not yet re-run" not in measured


def test_client_safe_names_no_tool_anywhere():
    bug = finding_to_bug(_enriched(), environment="staging, Chrome", base_url="http://app.test", client_safe=True)
    for blob in (bug["description"], " ".join(bug["labels"]), bug["fingerprint"], bug["title"]):
        assert "qax" not in blob.lower().replace("qax1", "").replace("qaxev", "") or "evil.invalid" in blob
    assert "Found by" not in bug["description"] and "re-checks this on every scan" not in bug["description"]
    assert bug["fingerprint"].startswith("fp-") and "qax" not in bug["labels"]
    # The default (non client-safe) ticket still carries the tool attribution.
    normal = finding_to_bug(_enriched())
    assert "Found by: QAX" in normal["description"] and "qax" in normal["labels"]


def test_client_safe_fingerprint_dedupes_within_its_own_mode():
    from core.jira_bugs import fingerprint
    a, b = fingerprint("T", "/x"), fingerprint("T", "/x", client_safe=True)
    assert a == "qax-fp-" + b[3:] and a != b


def test_live_filing_attaches_a_redacted_evidence_file():
    jira = FakeJira()
    f = _enriched(); f["evidence"] = "token Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdef leaked"
    res = _filer(False, jira).file(finding_to_bug(f))
    assert res["success"] and res["attachment"] is True
    url, files, headers = jira.attached[0]
    assert url.endswith("/rest/api/3/issue/SCRUM-1/attachments") and headers["X-Atlassian-Token"] == "no-check"
    name, payload, mime = files["file"]
    assert name.startswith("evidence-") and name.endswith(".txt") and mime == "text/plain"
    assert b"eyJhbGciOiJIUzI1NiJ9" not in payload            # the JWT was masked before upload


def test_failed_attachment_never_fails_the_filing():
    class _NoAttach(FakeJira):
        def post(self, url, **k):
            if url.endswith("/attachments"):
                raise RuntimeError("boom")
            return super().post(url, **k)
    res = _filer(False, _NoAttach()).file(finding_to_bug(_enriched()))
    assert res["success"] is True and res["jira_key"] == "SCRUM-1" and res["attachment"] is False


def test_dry_run_announces_the_attachment_but_uploads_nothing():
    jira = FakeJira()
    res = _filer(True, jira).file(finding_to_bug(_enriched()))
    assert res["would_attach"].startswith("evidence-") and not getattr(jira, "attached", [])


def test_same_sentence_is_not_repeated_across_sections():
    one = finding_to_bug(_enriched())["description"]                 # host-header: a ONE-sentence "why"
    assert "Security Impact" not in one                              # it is already the Severity reason
    assert one.count("so an attacker can poison password-reset emails") == 1   # only in the Severity reason
    two = {**_FINDING, "probe": "boolean-false", "title": "Boolean 'x' cannot be set to false on POST /things",
           "target": "/things", "evidence": "POST /things with x=true was accepted, but x=false was rejected."}
    from services.remediation import qa_for, remediation_for
    two["remediation"], two["qa"] = remediation_for(two), qa_for(two)
    d = finding_to_bug(two)["description"]
    assert "Security Impact" in d                                    # multi-sentence why → impact adds info


def test_client_safe_environment_never_names_the_tool():
    from cli.file_findings import environment_of
    r = {"target_url": "http://app.test", "scan_mode": "quick", "scanned_at": "2026-10-15 10:00 UTC"}
    assert environment_of(r, client_safe=True) == "http://app.test / 2026-10-15 10:00 UTC"
    assert "QAX" in environment_of(r)
    assert "qax" not in environment_of({}, client_safe=True).lower()


def test_ticket_carries_a_parseable_build_line():
    bug = finding_to_bug(_enriched(), environment="E", build={"id": "7f3a9c1b2d4e", "version": "28.1.0"})
    assert "Build: 7f3a9c1b2d4e (28.1.0)" in bug["description"]
    assert "Build:" not in finding_to_bug(_enriched(), environment="E")["description"]      # unknown → no line


def test_an_unreachable_duplicate_check_never_files_a_possible_duplicate(monkeypatch):
    """Engineering audit 2026-10-20: a 429/outage on the search used to mean "no duplicate"."""
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)

    class _S:
        def __init__(self):
            self.posts = []

        def post(self, url, **kw):
            self.posts.append(url)
            class R:
                status_code = 429
                text = "slow down"
                def json(self):
                    return {}
            return R()

        def get(self, url, **kw):
            class R:
                status_code = 200
                def json(self):
                    return {"issueTypes": [{"id": "1", "name": "Bug"}], "fields": [{"fieldId": "summary"}]}
            return R()
    s = _S()
    f = JiraBugFiler(dry_run=False, base_url="https://acme.atlassian.net", email="a@b.c", token="t",
                     project_key="QA", session=s)
    res = f.file({"test_case_id": "B1", "title": "t", "fingerprint": "qax-fp-1"})
    assert res["success"] is False and "duplicate check" in res["error"]
    assert not [u for u in s.posts if u.endswith("/rest/api/3/issue")]          # nothing filed
    assert len([u for u in s.posts if "search" in u]) == 3                         # retried
