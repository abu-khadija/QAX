"""cli/demo.py + the key-free quick scan — ROADMAP Q2 (first confirmed finding on a clean machine)."""
from unittest.mock import MagicMock

import cli.demo as D


def test_scan_command_is_a_quick_authorized_scan_of_the_demo_url():
    cmd = D.scan_command("http://127.0.0.1:9", "/tmp/o.json", python="py")
    assert cmd[0] == "py" and cmd[2:4] == ["scan", "--url"] and "http://127.0.0.1:9" in cmd
    assert "--quick" in cmd and "--authorized" in cmd and cmd[cmd.index("--out") + 1] == "/tmp/o.json"
    assert "--token" not in cmd                         # nothing to configure: no credentials needed


def test_demo_app_is_bundled_and_free_port_is_usable():
    assert D.DEMO_APP.exists()
    p = D.free_port()
    assert isinstance(p, int) and 1024 < p < 65536


def test_wait_until_up_returns_true_once_health_answers_200():
    calls = {"n": 0}

    def opener(url, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("not yet")
        return MagicMock(status=200)
    assert D.wait_until_up("http://x", tries=10, delay=0, opener=opener) is True
    assert calls["n"] == 3 and calls["n"] < 10


def test_wait_until_up_gives_up_cleanly():
    def never(url, timeout=None):
        raise OSError("down")
    assert D.wait_until_up("http://x", tries=3, delay=0, opener=never) is False


def test_next_steps_point_at_real_commands_and_state_the_not_tested_rule():
    text = D.next_steps("/tmp/demo_scan.json")
    assert "file-findings --from /tmp/demo_scan.json --show-payload" in text
    assert "scan --url https://your-app.example --quick --authorized" in text
    assert "NOT TESTED" in text and "verify-fixes" in text


def test_a_quick_live_scan_needs_no_key_but_deep_or_repo_scans_do():
    import main
    assert main.scan_needs_key(quick=True, repo=None) is False        # the demo / a live-app quick scan
    assert main.scan_needs_key(quick=True, repo="/src") is True       # modelling source uses the AI
    assert main.scan_needs_key(quick=False, repo=None) is True        # deep = generation/red-team/critic
    assert main.scan_needs_key(quick=False, repo="/src") is True


def test_lean_requirements_cover_the_scanner_and_exclude_the_heavy_ui():
    from pathlib import Path
    names = {l.split(">")[0].split("=")[0].strip().lower()
             for l in (D.ROOT / "requirements-scan.txt").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")}
    assert {"requests", "flask", "python-dotenv", "jsonschema", "pydantic", "anthropic"} <= names
    assert not ({"streamlit", "pandas", "plotly"} & names)            # the slow installs stay out of the fast path


def test_web_demo_url_prefills_the_target_and_the_page_reads_it():
    from cli.demo import web_url
    assert web_url(5555, 8900) == "http://127.0.0.1:8900/app?url=http://127.0.0.1:5555"
    html = "".join((__import__("pathlib").Path(__file__).resolve().parent.parent / "web" / "static" / n).read_text(encoding="utf-8") for n in ("app.html", "app.js"))
    assert "URLSearchParams(location.search).get('url')" in html and "https?:" in html      # http(s) only, never javascript:
