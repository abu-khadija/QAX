"""
web/server.py — QAX product front door (thin backend over the scan engine)
==========================================================================
Serves the single-page UI and the scan endpoints:
  • GET  /api/scan/stream — Server-Sent Events: streams live progress events as the
    scan runs (model → ground → probe → tour → prove), then the final report.
  • POST /api/scan        — same scan, single JSON response (no streaming).

Both reuse the exact engine the CLI uses; the report separates confirmed (P1/P2)
from low-confidence leads and carries the scorecard + per-layer attribution.

Run with the exec venv (has Flask + the engine + Playwright):
    ./.venv-exec/bin/python -m web.server        # from the repo root
then open http://127.0.0.1:8900
"""

from __future__ import annotations

import json
import os
import queue
import tempfile
import threading
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from services.progress import ProgressReporter

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app = Flask(__name__, static_folder=_STATIC, static_url_path="")


@app.get("/")
def index():
    return send_from_directory(_STATIC, "index.html")


@app.get("/files/<path:p>")
def files(p):
    """Serve tour screenshots saved under ./output (relative to the server cwd)."""
    return send_from_directory(os.path.join(os.getcwd(), "output"), p)


class _QueueProgress(ProgressReporter):
    """Tees every engine progress event into a queue for live streaming."""

    def __init__(self, q: "queue.Queue") -> None:
        super().__init__()
        self._q = q

    def emit(self, step: str, status: str = "info", detail: str = "") -> None:
        super().emit(step, status, detail)
        try:
            self._q.put({"type": "progress", "step": step, "status": status, "detail": detail})
        except Exception:
            pass


def _inputs():
    """Normalise url/repo/token from the request (query for GET, JSON for POST)."""
    src = request.args if request.method == "GET" else (request.get_json(force=True, silent=True) or {})
    url = str(src.get("url", "")).strip()
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    repo = str(src.get("repo", "")).strip() or tempfile.mkdtemp(prefix="qax_web_")
    token = str(src.get("token", "")).strip()
    auth = {"token": token, "header": "Authorization", "scheme": "Bearer"} if token else None
    return url, repo, auth


def _run_scan(url: str, repo: str, auth, progress=None) -> dict:
    """Run a quick scan and return the UI payload, or {'error': ...}."""
    from services import scorecard, usage
    from services.context import QAXContext
    from services.orchestrator_service import OrchestratorService

    try:
        ctx = QAXContext.from_env()
    except Exception as exc:
        return {"error": f"Config error: {exc}"}
    from urllib.parse import urlparse
    host = (urlparse(url).netloc or "app").replace(":", "_").replace(".", "-")
    visual_baseline = os.path.join("output", "tour_baseline", host)  # seeded on 1st scan, diffed after
    usage.track()
    usage.reset()
    t0 = time.time()
    try:
        result = OrchestratorService(ctx).run(repo, url, deep=False, auth=auth, tour=True,
                                              visual_baseline=visual_baseline, progress=progress)
    except Exception as exc:
        return {"error": f"Scan failed: {exc}"}
    if result.error:
        return {"error": result.error}
    rec = scorecard.score_run(result, repo=repo, url=url, mode="quick",
                              duration_sec=time.time() - t0, usage=usage.snapshot())
    from services.remediation import remediation_for
    confirmed = list(result.confirmed or [])
    for b in confirmed:
        b["remediation"] = remediation_for(b)
    claims = [b for b in confirmed if str(b.get("severity", "")).upper() in ("P1", "P2")]
    leads = [b for b in confirmed if b not in claims]
    screens = []
    for s in (getattr(result, "ui_screens", []) or []):
        shot = str(s.get("screenshot", ""))
        if not shot:
            continue
        rel = shot.split("output/", 1)[-1] if "output/" in shot else os.path.basename(shot)
        screens.append({
            "url": s.get("url"), "title": s.get("title"),
            "screenshot_url": "/files/" + rel,
            "errors": [e for e in (s.get("errors") or []) if "warning" not in str(e).lower()],
            "net_errors": s.get("net_errors") or [],
        })
    return {
        "url": url,
        "claims": claims,
        "leads": leads,
        "screens": screens,
        "by_layer": rec.get("by_layer", {}),
        "coverage": result.coverage or {},
        "scorecard": {
            "duration_sec": rec.get("duration_sec"), "tests_run": rec.get("tests_run"),
            "end_to_end": rec.get("completed_end_to_end"), "cost": rec.get("cost", {}),
            "claims": len(claims), "leads": len(leads),
        },
    }


@app.post("/api/scan")
def scan():
    url, repo, auth = _inputs()
    if not url:
        return jsonify({"error": "Please enter the URL of your running app."}), 400
    payload = _run_scan(url, repo, auth)
    return (jsonify(payload), 502) if payload.get("error") else jsonify(payload)


@app.get("/api/scan/stream")
def scan_stream():
    url, repo, auth = _inputs()
    if not url:
        return jsonify({"error": "Please enter the URL of your running app."}), 400
    q: "queue.Queue" = queue.Queue()

    def work():
        try:
            payload = _run_scan(url, repo, auth, progress=_QueueProgress(q))
            q.put({"type": "error", "error": payload["error"]} if payload.get("error")
                  else {"type": "result", "payload": payload})
        except Exception as exc:
            q.put({"type": "error", "error": str(exc)})
        finally:
            q.put({"type": "done"})

    threading.Thread(target=work, daemon=True).start()

    def gen():
        yield "retry: 3000\n\n"
        while True:
            try:
                ev = q.get(timeout=20)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            yield "data: " + json.dumps(ev) + "\n\n"
            if ev.get("type") == "done":
                break

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8900, debug=False, threaded=True)
