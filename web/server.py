"""
web/server.py — QAX product front door (thin backend over the scan engine)
==========================================================================
Serves the product homepage (GET /), the app UI (GET /app) and the scan endpoints:
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


@app.before_request
def _require_api_token():
    """
    S3 — auth gate for the JSON API. When `QAX_API_TOKEN` is set, every `/api/*`
    request must carry a matching `X-QAX-Token` header; otherwise 401. When it is NOT
    set, the API is open (local-dev default) — so exposing the server beyond loopback
    means setting the token first. Static pages and files are not gated.
    """
    import hmac
    if not request.path.startswith("/api/"):
        return None
    expected = os.getenv("QAX_API_TOKEN", "")
    if not expected:
        return None   # no token configured → local-dev open mode
    presented = request.headers.get("X-QAX-Token", "")
    if not (presented and hmac.compare_digest(presented, expected)):
        return jsonify({"error": "unauthorized — set a valid X-QAX-Token header"}), 401
    return None


@app.get("/")
def index():
    """Product homepage — what QAX is, how it works, and the evidence behind it.
    Falls back to the app UI until the homepage (static/index.html) exists."""
    page = "index.html" if os.path.exists(os.path.join(_STATIC, "index.html")) else "app.html"
    return send_from_directory(_STATIC, page)


@app.get("/app")
def app_ui():
    """The working app: scan a running app, generate test cases, file to Jira/Xray."""
    return send_from_directory(_STATIC, "app.html")


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


_UNAUTHORIZED_TARGET = ("Confirm you own this app or are authorized to test it — QAX sends "
                        "adversarial input and may create test records on the target.")


def _target_authorized() -> bool:
    """
    S4 — authorized-target gate, same rule as the CLI's --authorized: active probing
    sends adversarial input and can write to the target, so a scan runs only when the
    caller explicitly confirms they are allowed to test it (`authorized=true`).
    """
    src = request.args if request.method == "GET" else (request.get_json(force=True, silent=True) or {})
    return str(src.get("authorized", "")).strip().lower() in ("1", "true", "yes", "on")


def _supply_chain_leads(repo: str) -> list:
    """
    Run the dependency scanner over the repo and return its findings as P3 lead
    dicts (with remediation). Best-effort: a missing repo or no network yields []
    and never blocks the scan.
    """
    try:
        from core.dep_scanner import scan_dependencies
        from services.remediation import remediation_for
    except Exception:
        return []
    out = []
    try:
        rep = scan_dependencies(repo, online=True)
    except Exception:
        return []
    for v in rep.get("vulns", []):
        b = {"severity": "P3", "source": "supply-chain", "probe": "dep-cve",
             "title": f"Vulnerable dependency: {v['name']} {v['version']}",
             "target": f"{v['ecosystem']}:{v['name']}",
             "evidence": f"{v['name']} {v['version']} has known advisories: {', '.join(v['ids'][:6])}."}
        b["remediation"] = remediation_for(b)
        out.append(b)
    for lic in rep.get("license_flags", []):
        b = {"severity": "P3", "source": "supply-chain", "probe": "dep-license",
             "title": f"{lic['kind'].title()} license: {lic['name']} ({lic['license']})",
             "target": f"PyPI:{lic['name']}",
             "evidence": f"{lic['name']} {lic['version']} is licensed '{lic['license']}' ({lic['kind']})."}
        b["remediation"] = remediation_for(b)
        out.append(b)
    return out


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
    for b in (result.confirmed or []):
        b["remediation"] = remediation_for(b)
    # B1 — separate by TRUST, not severity: the CONFIRMED block is the deterministic,
    # reproducible, zero-false-positive layer (probes + self-proving templates) in a
    # canonical order; the LEADS block is the AI Investigator / UI tour (may vary run
    # to run) plus supply-chain hygiene. A buyer can re-run and get the same CONFIRMED.
    claims = list(result.confirmed_deterministic)
    leads = list(result.leads_ai)
    # Supply-chain angle: known-CVE and copyleft/non-commercial dependencies. Real but
    # not proven-exploitable on the live app, so they ride in leads (hygiene), never
    # the confirmed block — in keeping with "don't cry wolf".
    leads.extend(_supply_chain_leads(repo))
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
        "housekeeping": {
            "created": getattr(result, "created_records", 0),
            "cleaned": getattr(result, "cleaned_records", 0),
        },
    }


@app.post("/api/scan")
def scan():
    url, repo, auth = _inputs()
    if not url:
        return jsonify({"error": "Please enter the URL of your running app."}), 400
    if not _target_authorized():
        return jsonify({"error": _UNAUTHORIZED_TARGET}), 400
    payload = _run_scan(url, repo, auth)
    return (jsonify(payload), 502) if payload.get("error") else jsonify(payload)


@app.post("/api/generate")
def generate():
    """
    Generate test cases from a user story and/or source code — the other half of the
    platform (no running app needed). Returns {"tests": [...]} or {"error": ...}.
    """
    data = request.get_json(force=True, silent=True) or {}
    story = str(data.get("story", "")).strip()
    code = str(data.get("code", "")).strip()
    if not story and not code:
        return jsonify({"error": "Enter a user story or paste some source code."}), 400
    try:
        from core.claude_client import ClaudeClient
        from services.context import QAXContext
        proj = getattr(QAXContext.from_env(), "project", "") or os.getenv("DEFAULT_PROJECT", "") or "example"
        claude = ClaudeClient(project=proj)
        # The story adapter feeds the story text to Claude AS the content to analyze;
        # when real code is given we use that instead and keep the story as context.
        content = code or story
        cases = claude.analyze_code(code=content, file_path="input.py" if code else "story.txt",
                                    user_story=story or None, use_cache=True)
    except Exception as exc:
        return jsonify({"error": f"Generation failed: {exc}"}), 502
    return jsonify({"tests": cases or []})


_EXPORT_TARGETS = ("xray", "zephyr", "excel", "csv", "memory")


def _finding_to_testcase(b: dict, i: int) -> dict:
    """Map a security finding to the bug shape filed to Jira (see core.jira_bugs)."""
    from core.jira_bugs import finding_to_bug
    from services.remediation import qa_for, remediation_for
    b = dict(b)
    b.setdefault("remediation", remediation_for(b))
    b.setdefault("qa", qa_for(b))
    return finding_to_bug(b, i)


@app.post("/api/export")
def export():
    """
    File test cases or security findings to Jira/Xray/Zephyr/Excel via the existing
    export adapters. Defaults to DRY_RUN (previews what would be created) so nothing is
    filed until the caller opts in and credentials are configured. Never raises.
    """
    data = request.get_json(force=True, silent=True) or {}
    items = data.get("items") or []
    kind = str(data.get("kind", "tests")).lower()
    target = str(data.get("target", "xray")).lower()
    project = str(data.get("project", "")).strip() or None
    # S3 — export live-gate: a caller can NEVER force a live push. A live export
    # happens only when the operator opted in server-side (QAX_ALLOW_LIVE_EXPORT=true)
    # AND the caller did not ask for dry-run. Otherwise it is always a dry run.
    allow_live = os.getenv("QAX_ALLOW_LIVE_EXPORT", "false").lower() == "true"
    dry_run = True if not allow_live else (data.get("dry_run", True) is not False)
    if not items:
        return jsonify({"error": "Nothing to file."}), 400

    # Findings are filed as Jira BUGS (standard Jira issue API), not Xray Tests.
    if kind == "findings":
        bugs = [_finding_to_testcase(b, i) for i, b in enumerate(items) if isinstance(b, dict)]
        try:
            # Jira-only filer: no Xray credentials needed to file a Bug. S1: the
            # dry-run flag is request-scoped, no env mutation.
            from core.jira_bugs import JiraBugFiler
            result = JiraBugFiler(dry_run=dry_run, project_key=project).file_many(bugs)
        except Exception as exc:
            result = {"success": False, "exported_count": 0, "failed_count": len(bugs),
                      "references": [], "error": f"Bug filing failed: {exc}"}
        result = dict(result or {})
        result.update({"dry_run": dry_run, "target": "jira", "kind": "findings"})
        return jsonify(result)

    # Test cases go to the chosen export adapter (Xray/Zephyr/Excel/…).
    if target not in _EXPORT_TARGETS:
        return jsonify({"error": f"Unknown target '{target}'. One of: {', '.join(_EXPORT_TARGETS)}."}), 400
    try:
        from core.adapters import get_adapter
        result = get_adapter(target).export(items, project=project, dry_run=dry_run)
    except Exception as exc:
        return jsonify({"error": f"Export failed: {exc}"}), 502
    result = dict(result or {})
    result.update({"dry_run": dry_run, "target": target, "kind": "tests"})
    return jsonify(result)


@app.get("/api/scan/stream")
def scan_stream():
    url, repo, auth = _inputs()
    if not url:
        return jsonify({"error": "Please enter the URL of your running app."}), 400
    if not _target_authorized():
        return jsonify({"error": _UNAUTHORIZED_TARGET}), 400
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
