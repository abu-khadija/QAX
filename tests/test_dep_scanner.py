"""test_dep_scanner.py — dependency CVE + license scanner (pure + mocked network)."""

from unittest.mock import MagicMock

from core.dep_scanner import (check_licenses, check_vulnerabilities, collect_dependencies,
                              parse_package_json, parse_requirements, scan_dependencies)


# ── parsing (pure) ───────────────────────────────────────────────────────────

def test_parse_requirements_pins_and_ranges():
    txt = (
        "# a comment\n"
        "requests==2.32.3\n"
        "flask>=3.0.0  # inline comment\n"
        "pydantic==2.*\n"                       # wildcard → not a hard pin
        "openpyxl[extra]===3.1.0\n"
        "pkg ; python_version<'3.12'\n"         # marker only, no version
        "-r other.txt\n"
        "git+https://example.com/x.git\n"
    )
    deps = {d["name"]: d["version"] for d in parse_requirements(txt)}
    assert deps["requests"] == "2.32.3"
    assert deps["flask"] is None                 # range, unpinned
    assert deps["pydantic"] is None              # wildcard
    assert deps["openpyxl"] == "3.1.0"           # === is a pin
    assert deps["pkg"] is None
    assert "x" not in deps and "git+https" not in deps


def test_parse_package_json_exact_vs_range():
    txt = '{"dependencies":{"left-pad":"1.3.0","react":"^18.0.0"},"devDependencies":{"jest":"29.0.0"}}'
    deps = {d["name"]: d["version"] for d in parse_package_json(txt)}
    assert deps["left-pad"] == "1.3.0"
    assert deps["react"] is None                 # caret range, unpinned
    assert deps["jest"] == "29.0.0"
    assert all(d["ecosystem"] == "npm" for d in parse_package_json(txt))


def test_parse_package_json_bad_input():
    assert parse_package_json("not json") == []


def test_collect_dependencies_reads_repo(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.32.3\nflask>=3.0.0\n")
    (tmp_path / "package.json").write_text('{"dependencies":{"left-pad":"1.3.0"}}')
    deps = collect_dependencies(str(tmp_path))
    names = {d["name"] for d in deps}
    assert {"requests", "flask", "left-pad"} <= names


def test_collect_missing_repo_is_empty():
    assert collect_dependencies("/no/such/path/here") == []


# ── OSV (mocked) ─────────────────────────────────────────────────────────────

def test_check_vulnerabilities_flags_known_cve():
    deps = [{"name": "requests", "version": "2.19.0", "ecosystem": "PyPI"},
            {"name": "flask", "version": None, "ecosystem": "PyPI"}]      # unpinned, skipped
    sess = MagicMock()
    sess.post.return_value = MagicMock(json=lambda: {"results": [{"vulns": [{"id": "GHSA-xxxx"}]}]})
    out = check_vulnerabilities(deps, session=sess)
    assert len(out) == 1 and out[0]["name"] == "requests" and out[0]["ids"] == ["GHSA-xxxx"]


def test_check_vulnerabilities_quiet_when_clean():
    deps = [{"name": "requests", "version": "2.32.3", "ecosystem": "PyPI"}]
    sess = MagicMock()
    sess.post.return_value = MagicMock(json=lambda: {"results": [{}]})
    assert check_vulnerabilities(deps, session=sess) == []


def test_check_vulnerabilities_network_error_captured():
    deps = [{"name": "requests", "version": "2.32.3", "ecosystem": "PyPI"}]
    sess = MagicMock()
    sess.post.side_effect = RuntimeError("no network")
    errs: list = []
    assert check_vulnerabilities(deps, session=sess, errors=errs) == []
    assert errs and "OSV" in errs[0]


# ── licenses (mocked) ────────────────────────────────────────────────────────

def test_check_licenses_flags_copyleft():
    deps = [{"name": "somegpl", "version": "1.0", "ecosystem": "PyPI"}]
    sess = MagicMock()
    sess.get.return_value = MagicMock(json=lambda: {"info": {"license": "GPLv3"}})
    out = check_licenses(deps, session=sess)
    assert len(out) == 1 and out[0]["kind"] == "copyleft"


def test_check_licenses_flags_noncommercial_classifier():
    deps = [{"name": "nc", "version": "1.0", "ecosystem": "PyPI"}]
    sess = MagicMock()
    sess.get.return_value = MagicMock(json=lambda: {"info": {
        "license": "", "classifiers": ["License :: Free for non-commercial use"]}})
    out = check_licenses(deps, session=sess)
    assert len(out) == 1 and out[0]["kind"] == "non-commercial"


def test_check_licenses_quiet_on_permissive():
    deps = [{"name": "mitpkg", "version": "1.0", "ecosystem": "PyPI"}]
    sess = MagicMock()
    sess.get.return_value = MagicMock(json=lambda: {"info": {"license": "MIT"}})
    assert check_licenses(deps, session=sess) == []


# ── orchestration ────────────────────────────────────────────────────────────

def test_scan_offline_only_parses(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.32.3\n")
    rep = scan_dependencies(str(tmp_path), online=False)
    assert rep["summary"]["total"] == 1 and rep["summary"]["pinned"] == 1
    assert rep["vulns"] == [] and rep["license_flags"] == []
