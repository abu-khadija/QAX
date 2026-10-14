"""S8: UI data access extracted to ui/data (no Streamlit; takes a root path)."""
import json

from ui.data import connection_status, list_projects, load_all_test_cases, load_latest_test_cases


def _write(root, rel, obj):
    p = root / rel; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj)); return p


def test_list_projects_from_dirs_else_env(tmp_path, monkeypatch):
    (tmp_path / "config" / "projects" / "beta").mkdir(parents=True)
    (tmp_path / "config" / "projects" / "alpha").mkdir()
    assert list_projects(tmp_path) == ["alpha", "beta"]           # sorted dir names
    monkeypatch.setenv("DEFAULT_PROJECT", "solo")
    assert list_projects(tmp_path / "empty") == ["solo"]          # no dir → env fallback


def test_load_all_and_latest(tmp_path):
    _write(tmp_path, "output/test_cases_20260101_000000.json", [{"test_case_id": "OLD"}])
    _write(tmp_path, "output/test_cases_20260102_000000.json", [{"test_case_id": "NEW"}])
    all_ids = {c["test_case_id"] for c in load_all_test_cases(tmp_path)}
    assert all_ids == {"OLD", "NEW"}
    assert load_latest_test_cases(tmp_path)[0]["test_case_id"] == "NEW"         # newest first
    assert load_latest_test_cases(tmp_path / "none") == []


def test_load_tolerates_corrupt_json(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "test_cases_1.json").write_text("{ not json")
    assert load_all_test_cases(tmp_path) == [] and load_latest_test_cases(tmp_path) == []


def test_connection_status_reads_env(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN",
              "XRAY_CLIENT_ID", "XRAY_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("DRY_RUN", "false")
    s = connection_status()
    assert s["claude"] is True and s["jira"] is False and s["xray"] is False and s["dry_run"] is False
