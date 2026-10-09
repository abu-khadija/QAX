"""
web/server.py — QAX product front door (thin backend over the scan engine)
==========================================================================
Serves the single-page UI and one endpoint, POST /api/scan, that runs a QAX quick
scan against a URL the user points at and returns the report (confirmed P1/P2 vs
low-confidence leads, the scorecard, and per-layer attribution). Reuses the exact
engine the CLI uses — no logic forked here.

Run with the exec venv (has Flask + the engine + Playwright):
    ./.venv-exec/bin/python -m web.server        # from the repo root
then open http://127.0.0.1:8900
"""

from __future__ import annotations

import os
import tempfile
import time

from flask import Flask, jsonify, request, send_from_directory

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app = Flask(__name__, static_folder=_STATIC, static_url_path="")


@app.get("/")
def index():
    return send_from_directory(_STATIC, "index.html")


@app.post("/api/scan")
def scan():
    from services import scorecard, usage
    from services.context import QAXContext
    from services.orchestrator_service import OrchestratorService

    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get("url", "")).strip()
    if not url:
        return jsonify({"error": "Please enter the URL of your running app."}), 400
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    repo = str(data.get("repo", "")).strip() or tempfile.mkdtemp(prefix="qax_web_")
    token = str(data.get("token", "")).strip()
    auth = {"token": token, "header": "Authorization", "scheme": "Bearer"} if token else None

    try:
        ctx = QAXContext.from_env()
    except Exception as exc:
        return jsonify({"error": f"Config error: {exc}"}), 500

    usage.track()
    usage.reset()
    t0 = time.time()
    try:
        result = OrchestratorService(ctx).run(repo, url, deep=False, auth=auth)
    except Exception as exc:
        return jsonify({"error": f"Scan failed: {exc}"}), 500
    elapsed = time.time() - t0

    if result.error:
        return jsonify({"error": result.error}), 502

    rec = scorecard.score_run(result, repo=repo, url=url, mode="quick",
                              duration_sec=elapsed, usage=usage.snapshot())
    confirmed = list(result.confirmed or [])
    claims = [b for b in confirmed if str(b.get("severity", "")).upper() in ("P1", "P2")]
    leads = [b for b in confirmed if b not in claims]
    return jsonify({
        "url": url,
        "claims": claims,
        "leads": leads,
        "by_layer": rec.get("by_layer", {}),
        "coverage": result.coverage or {},
        "scorecard": {
            "duration_sec": rec.get("duration_sec"),
            "tests_run": rec.get("tests_run"),
            "end_to_end": rec.get("completed_end_to_end"),
            "cost": rec.get("cost", {}),
            "claims": len(claims),
            "leads": len(leads),
        },
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8900, debug=False)
