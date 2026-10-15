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
    assert "confirmed blocking issue" in md.lower()
    assert "Blocking — confirmed, proven exploitable" in md
    # A deterministic P3 is CONFIRMED (just not blocking) — never mislabelled a "lead".
    assert "Confirmed — lower severity (does not block)" in md and "Leads — flagged" not in md
    assert "/users/{id}" in md
    # remediation for sqli gets pulled in (How to fix)
    assert "How to fix" in md


def test_format_clean_when_no_claims():
    md, claims, leads = format_comment("http://app", [
        {"severity": "P3", "probe": "security-headers", "title": "headers missing", "target": "/"}])
    assert claims == [] and len(leads) == 1
    assert "No blocking issues" in md and "1 lower-severity confirmed" in md


def test_format_handles_empty():
    md, claims, leads = format_comment("http://app", [])
    assert claims == [] and leads == [] and "No confirmed issues" in md


def test_main_no_url_does_not_block(tmp_path, monkeypatch):
    comment = tmp_path / "c.md"
    monkeypatch.setenv("QAX_TARGET_URL", "")
    monkeypatch.setenv("QAX_COMMENT_PATH", str(comment))
    assert main() == 0                      # never blocks when there's no target
    assert "skipping" in comment.read_text().lower()



# ── Q4: honesty + CI-grade behaviour ─────────────────────────────────────────────

import json as _json

import pytest

from ci import pr_scan as P


def test_ai_leads_are_separated_from_confirmed_by_source_not_severity():
    md, claims, leads = format_comment("http://app", [
        {"severity": "P1", "source": "probe", "probe": "bola", "title": "BOLA", "target": "/a"},
        {"severity": "P1", "source": "test", "title": "LLM thinks SQLi", "target": "/b"},     # P1 but an AI lead
        {"severity": "P3", "source": "probe", "probe": "security-headers", "title": "CSP missing", "target": "/"}])
    assert [c["title"] for c in claims] == ["BOLA"]                      # the AI P1 does NOT block
    assert "AI leads — not confirmed (1, may vary between runs)" in md and "LLM thinks SQLi" in md
    assert "Confirmed — lower severity (does not block)" in md


def test_a_clean_result_is_not_called_clean_while_checks_could_not_run():
    gaps = [{"probe": "bola", "kind": "missing-prerequisite", "needs": "token_b", "reason": "r"}]
    md, *_ = format_comment("http://app", [], not_tested=gaps)
    assert "not** a clean bill of health" in md and "held up" not in md
    assert "NOT TESTED" in md and "unknown*, not *safe" in md
    md2, *_ = format_comment("http://app", [])
    assert "held up" in md2 and "NOT TESTED" not in md2                    # no gaps → the clean wording


def test_not_tested_is_grouped_once_per_prerequisite_in_ci_wording():
    gaps = [{"probe": p, "kind": "missing-prerequisite", "needs": "token_b", "reason": "r"}
            for p in ("bfla", "bola", "access-revocation")]
    gaps.append({"probe": "jwt-flaws", "kind": "missing-prerequisite", "needs": "auth-token", "reason": "r"})
    gaps.append({"probe": "xxe", "kind": "not-applicable", "reason": "n/a"})          # not a gap
    md, *_ = format_comment("http://app", [], not_tested=gaps)
    assert "`access-revocation`, `bfla`, `bola`" in md and md.count("QAX_TOKEN_B") == 1
    assert "`QAX_TOKEN` secret" in md and "xxe" not in md


def test_gaps_are_flagged_even_when_there_are_confirmed_findings():
    md, *_ = format_comment("http://app", [{"severity": "P3", "probe": "x", "title": "t", "target": "/"}],
                            not_tested=[{"probe": "bola", "kind": "error", "reason": "crashed"}])
    assert "1 check could not run" in md and "NOT TESTED" in md


def test_unstable_leftover_build_and_reproduced_are_shown():
    md, *_ = format_comment(
        "http://app", [{"severity": "P1", "probe": "bola", "title": "BOLA", "target": "/a", "reproduced": [3, 3]}],
        unstable=[{"probe": "long-input", "target": "/t", "reason": "fired in 1 of 3 passes"}],
        left=[{"collection": "/c", "key": "k1", "why": "no DELETE endpoint"}],
        build={"id": "abc123def456", "version": "28.1.0"}, created=2, cleaned=1)
    assert "⟨3/3⟩" in md and "build `abc123def456` (28.1.0)" in md
    assert "Unstable — 1 hit(s) did not reproduce" in md and "fired in 1 of 3 passes" in md
    assert "Left on the target" in md and "`/c` `k1` — no DELETE endpoint" in md


def test_required_missing_only_reports_required_probes_that_did_not_run():
    nt = [{"probe": "bola", "kind": "missing-prerequisite"}, {"probe": "xxe", "kind": "not-applicable"},
          {"probe": "bfla", "kind": "error"}, {"probe": "long-input", "kind": "unstable"}]
    assert P.required_missing({"bola", "bfla", "sqli"}, nt) == ["bfla", "bola"]      # sqli ran (no entry)
    assert P.required_missing({"xxe"}, nt) == ["xxe"]                              # n/a means it didn't run either
    assert P.required_missing({"long-input"}, nt) == []                           # unstable fired, so it ran
    assert P.required_missing(set(), nt) == []


# ── main(): wiring (fake orchestrator, no network) ───────────────────────────────

class _Ctx:
    def __init__(self, key): self.anthropic_key = key
    def require_anthropic_key(self):
        if not self.anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")


class _FakeOrch:
    seen: dict = {}
    result = None

    def __init__(self, ctx): pass

    def run(self, repo, url, **kw):
        type(self).seen = {"repo": repo, "url": url, **kw}
        return type(self).result


def _setup(monkeypatch, tmp_path, result, key="k", **env):
    import core.build_fingerprint as BF
    import services.context as C
    import services.orchestrator_service as O
    monkeypatch.setattr(C.QAXContext, "from_env", staticmethod(lambda **k: _Ctx(key)))
    monkeypatch.setattr(O, "OrchestratorService", _FakeOrch)
    monkeypatch.setattr(BF, "build_fingerprint", lambda url, **k: {"id": "abc123def456", "version": "1.0"})
    _FakeOrch.result = result
    for k in ("QAX_REPO", "QAX_TOKEN", "QAX_TOKEN_B", "QAX_AUTH_SCHEME", "QAX_AUTH_HEADER", "QAX_FAIL_ON",
              "QAX_REQUIRE_PROBES", "QAX_REVERIFY", "QAX_DEEP"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("QAX_TARGET_URL", "http://preview.test")
    monkeypatch.setenv("QAX_COMMENT_PATH", str(tmp_path / "c.md"))
    monkeypatch.setenv("QAX_JSON_PATH", str(tmp_path / "o.json"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return tmp_path / "c.md", tmp_path / "o.json"


def _res(confirmed=(), not_tested=(), **kw):
    from services.results import OrchestratorResult
    return OrchestratorResult(confirmed=list(confirmed), not_tested=list(not_tested), **kw)


_BLOCK = {"severity": "P1", "source": "probe", "probe": "bola", "title": "BOLA", "target": "/a"}


def test_main_scans_live_only_by_default_so_no_api_key_is_needed(monkeypatch, tmp_path):
    c, _ = _setup(monkeypatch, tmp_path, _res(), key="")                 # NO key at all
    assert P.main() == 0
    assert _FakeOrch.seen["url"] == "http://preview.test" and _FakeOrch.seen["deep"] is False
    assert _FakeOrch.seen["repo"] and "qax_ci_" in _FakeOrch.seen["repo"]  # an empty temp dir, not "."
    assert "held up" in c.read_text()


def test_main_needs_a_key_only_for_deep_or_a_repo(monkeypatch, tmp_path):
    c, _ = _setup(monkeypatch, tmp_path, _res(), key="", QAX_REPO="./src")
    assert P.main() == 0 and "Config error" in c.read_text()             # reported, not blocking
    c, _ = _setup(monkeypatch, tmp_path, _res(), key="", QAX_DEEP="1")
    assert P.main() == 0 and "Config error" in c.read_text()


def test_main_passes_tokens_scheme_and_reverify_to_the_scan(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, _res(), QAX_TOKEN="T", QAX_TOKEN_B="TB", QAX_AUTH_SCHEME="token", QAX_REVERIFY="2")
    P.main()
    kw = _FakeOrch.seen
    assert kw["auth"] == {"token": "T", "token_b": "TB", "header": "Authorization", "scheme": "token"}
    assert kw["reverify"] == 2


def test_main_without_tokens_passes_no_auth(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, _res())
    P.main()
    assert _FakeOrch.seen["auth"] is None and _FakeOrch.seen["reverify"] == 0


def test_main_blocks_on_a_confirmed_blocking_finding_only(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, _res(confirmed=[_BLOCK]))
    assert P.main() == 1
    ai_only = {"severity": "P1", "source": "test", "title": "LLM suspicion", "target": "/b"}
    _setup(monkeypatch, tmp_path, _res(confirmed=[ai_only]))
    assert P.main() == 0                                                  # an AI lead never fails the build


def test_main_fail_on_is_configurable(monkeypatch, tmp_path):
    low = {"severity": "P3", "source": "probe", "probe": "security-headers", "title": "CSP", "target": "/"}
    _setup(monkeypatch, tmp_path, _res(confirmed=[low]))
    assert P.main() == 0
    _setup(monkeypatch, tmp_path, _res(confirmed=[low]), QAX_FAIL_ON="P1,P2,P3")
    assert P.main() == 1


def test_main_fails_when_a_required_probe_did_not_run(monkeypatch, tmp_path):
    gap = {"probe": "bola", "kind": "missing-prerequisite", "needs": "token_b", "reason": "r"}
    c, j = _setup(monkeypatch, tmp_path, _res(not_tested=[gap]), QAX_REQUIRE_PROBES="bola")
    assert P.main() == 1                                                  # green must never mean "didn't run"
    assert "Required check(s) did not run: `bola`" in c.read_text()
    assert _json.loads(j.read_text())["required_missing"] == ["bola"]
    c, _ = _setup(monkeypatch, tmp_path, _res(not_tested=[gap]))          # not required → warns but passes
    assert P.main() == 0 and "NOT TESTED" in c.read_text()


def test_main_writes_a_json_with_the_honesty_ledgers(monkeypatch, tmp_path):
    left = [{"collection": "/c", "key": "k", "why": "w"}]
    _, j = _setup(monkeypatch, tmp_path, _res(confirmed=[_BLOCK], left_on_target=left,
                                              not_tested=[{"probe": "xxe", "kind": "not-applicable", "reason": "n/a"}]))
    P.main()
    out = _json.loads(j.read_text())
    assert out["build"]["id"] == "abc123def456" and out["left_on_target"] == left
    assert [c["title"] for c in out["claims"]] == ["BOLA"] and out["not_tested"][0]["probe"] == "xxe"


def test_main_scan_errors_never_block_the_pr(monkeypatch, tmp_path):
    class _Boom(_FakeOrch):
        def run(self, *a, **k): raise RuntimeError("target down")
    import services.orchestrator_service as O
    c, _ = _setup(monkeypatch, tmp_path, _res())
    monkeypatch.setattr(O, "OrchestratorService", _Boom)
    assert P.main() == 0 and "Scan error" in c.read_text()
