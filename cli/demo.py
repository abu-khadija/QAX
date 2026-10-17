"""
cli/demo.py — `qax demo`: the first confirmed finding in about a minute, with no setup
======================================================================================
ROADMAP Q2 (time-to-first-confirmed-finding <= 10 min on a clean machine). A new user should
not need an API key, a Jira account, a target app, or a config file to SEE what QAX does:

    python main.py demo

boots QAX's bundled, deliberately-vulnerable demo app on a free local port, runs a quick scan
(deterministic probes — zero LLM calls, so no API key is needed), prints the confirmed
findings, and tells you the exact next commands (a Jira preview, scanning your own app).
The demo app only ever listens on 127.0.0.1 and is stopped when the demo ends.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_APP = ROOT / "evals" / "targets" / "vuln_jwt_app.py"


def free_port() -> int:
    """A free TCP port on localhost."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_until_up(url: str, tries: int = 60, delay: float = 0.25, opener=urllib.request.urlopen) -> bool:
    """Poll `<url>/health` until it answers 200 (True) or the tries run out (False)."""
    for _ in range(tries):
        try:
            if opener(f"{url}/health", timeout=1).status == 200:
                return True
        except Exception:
            time.sleep(delay)
    return False


def scan_command(url: str, out_json: str, python: str | None = None) -> list[str]:
    """The `qax scan` command the demo runs. --authorized is right here: QAX booted this app itself."""
    return [python or sys.executable, str(ROOT / "main.py"), "scan", "--url", url,
            "--quick", "--authorized", "--out", out_json]


def next_steps(out_json: str) -> str:
    """What to do after the demo (pure text)."""
    return "\n".join([
        "",
        "  That was QAX's bundled demo app. Next:",
        "",
        "   • See how a finding becomes a Jira bug (preview only — nothing is filed):",
        f"       python main.py file-findings --from {out_json} --show-payload",
        "",
        "   • Scan YOUR app (only apps you are allowed to test):",
        "       python main.py scan --url https://your-app.example --quick --authorized \\",
        "           --token <a valid token> --token-b <a second, ordinary user's token>",
        "     Checks that can't run (e.g. no second token) are listed under NOT TESTED — a quiet",
        "     result there means \"unknown\", not \"safe\".",
        "",
        "   • Later: python main.py verify-fixes --from <a fresh scan>   (are the filed bugs fixed?)",
        "",
    ])


def web_url(app_port: int, ui_port: int) -> str:
    """The web UI address with the demo app's URL pre-filled (the page reads ?url=)."""
    return f"http://127.0.0.1:{ui_port}/app?url=http://127.0.0.1:{app_port}"


def run_web(app_port: int, ui_port: int = 8900) -> int:
    """
    `qax demo --web`: the bundled vulnerable app AND the web interface, so a person can click Preview plan and Run scan
    instead of reading a terminal. Both listen on 127.0.0.1 only; Ctrl-C stops both. Returns an exit code.
    """
    url = f"http://127.0.0.1:{app_port}"
    print(f"\n  QAX demo (web) — bundled vulnerable app on {url}, web UI on port {ui_port} (both local only) …", flush=True)
    app = subprocess.Popen([sys.executable, str(DEMO_APP)], env=dict(os.environ, PORT=str(app_port)),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ui = subprocess.Popen([sys.executable, "-m", "web.server"], cwd=str(ROOT),
                          env=dict(os.environ, QAX_WEB_PORT=str(ui_port)),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_until_up(url):
            print("\n  The demo app did not start in time.\n")
            return 1
        ok = False
        for _ in range(60):
            try:
                ok = urllib.request.urlopen(f"http://127.0.0.1:{ui_port}/app", timeout=1).status == 200
                break
            except Exception:
                time.sleep(0.25)
        if not ok:
            print("\n  The web interface did not start (is port %d free? is Flask installed?).\n" % ui_port)
            return 1
        print(f"\n  Open this in your browser:\n\n      {web_url(app_port, ui_port)}\n", flush=True)
        print("  Tick the authorization box, click 'Preview plan' (nothing is attacked), then 'Run scan'.", flush=True)
        print("  Press Ctrl-C to stop.\n", flush=True)
        try:
            ui.wait()
        except KeyboardInterrupt:
            print("\n  Stopped.\n")
        return 0
    finally:
        for pr in (ui, app):
            try:
                pr.terminate()
            except Exception:
                pass


def run(argv: list[str]) -> int:
    """Entry point for `qax demo`. Returns a process exit code."""
    if not DEMO_APP.exists():
        print(f"\n  The demo app is missing ({DEMO_APP}).\n")
        return 1
    try:
        import flask  # noqa: F401  (the demo app needs it)
    except ImportError:
        print("\n  The demo app needs Flask:  pip install flask\n")
        return 1

    if "--web" in argv:
        return run_web(free_port())

    t0 = time.time()
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    out_json = os.path.join(tempfile.mkdtemp(prefix="qax_demo_"), "demo_scan.json")
    print(f"\n  QAX demo — starting the bundled vulnerable app on {url} (local only) …")
    proc = subprocess.Popen([sys.executable, str(DEMO_APP)], env=dict(os.environ, PORT=str(port)),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_until_up(url):
            print("\n  The demo app did not start in time.\n")
            return 1
        print("  Running a quick scan (deterministic probes — no LLM calls, no API key needed) …\n")
        rc = subprocess.run(scan_command(url, out_json)).returncode
        if rc != 0 or not os.path.exists(out_json):
            print(f"\n  The demo scan failed (exit {rc}).\n")
            return 1
        print(f"  ⏱  first confirmed findings in {time.time() - t0:.0f}s "
              f"(app start + scan; install time not included)")
        print("  (The demo app is thrown away when this ends, so any 'LEFT ON THE TARGET' records "
              "above don't matter here. On a real app QAX removes what it can and lists the rest.)")
        print(next_steps(out_json))
        return 0
    finally:
        proc.terminate()
