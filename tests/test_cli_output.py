"""S8: extracted save/find helpers (cli/output) + sessions formatter."""
import json

from cli.output import find_test_case_in_output, save_results_json
from cli.summaries import render_sessions


def test_save_json_autonames_and_roundtrips(tmp_path):
    path = save_results_json([{"test_case_id": "TC-A-0001", "title": "t"}], out_dir=str(tmp_path))
    assert path.startswith(str(tmp_path)) and path.endswith(".json")
    assert json.load(open(path))[0]["test_case_id"] == "TC-A-0001"


def test_find_test_case_searches_saved_files(tmp_path):
    save_results_json([{"test_case_id": "TC-A-0002"}],
                      output_path=str(tmp_path / "test_cases_1.json"))
    assert find_test_case_in_output("TC-A-0002", out_dir=str(tmp_path))["test_case_id"] == "TC-A-0002"
    assert find_test_case_in_output("TC-NOPE", out_dir=str(tmp_path)) is None
    assert find_test_case_in_output("x", out_dir=str(tmp_path / "missing")) is None


def test_render_sessions_empty_and_filled():
    assert "No sessions found" in render_sessions([])
    out = render_sessions([{"mode": "smoke", "session_id": "S1", "timestamp": "2026-10-14T10:00:00",
                            "files_processed": 2, "total_valid": 5, "output_file": "o.json"}])
    assert "SMOKE" in out and "S1" in out and "Valid: 5" in out and "o.json" in out
