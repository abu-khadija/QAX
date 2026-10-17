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


def test_run_record_has_version_commit_and_no_secrets():
    from core.version import __version__, run_record
    rec = run_record(read_only=True, token_supplied=True)
    assert rec["qax_version"] == __version__ and "options" in rec
    assert rec["options"] == {"read_only": True, "token_supplied": True}
    assert rec["qax_commit"] is None or len(rec["qax_commit"]) >= 7


def test_cli_scan_without_credentials_still_writes_the_out_file(tmp_path):
    """Regression: `parse_headers` was imported inside `if <credentials>:` and used after it, so a scan with no token
    could not write --out (and `qax demo` reported 'scan failed'). Runs the REAL command line."""
    import json
    import socket
    import subprocess
    import sys
    import threading
    import wsgiref.simple_server as ws
    from pathlib import Path

    def app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/html")]); return [b"<html>hi</html>"]

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = ws.make_server("127.0.0.1", port, app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = tmp_path / "scan.json"
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "main.py"), "scan",
                            "--url", f"http://127.0.0.1:{port}", "--quick", "--authorized", "--read-only",
                            "--out", str(out)], capture_output=True, text=True, timeout=120)
    finally:
        srv.shutdown()
    assert out.exists(), r.stdout[-600:] + r.stderr[-600:]
    d = json.loads(out.read_text())
    assert d["qax_version"] and d["options"]["token_supplied"] is False and d["options"]["read_only"] is True
