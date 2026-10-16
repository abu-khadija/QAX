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


def _all_probe_names() -> list[str]:
    """Kebab-case names of every probe a scan can run (from the probe modules)."""
    import inspect

    import execution.probes as P
    import execution.typed_probes as T
    names = []
    for mod in (P, T):
        for n, fn in vars(mod).items():
            if n.endswith("_probe") and not n.startswith("_") and inspect.isfunction(fn) \
                    and fn.__module__ == mod.__name__:
                names.append(n[:-len("_probe")].replace("_", "-"))
    return sorted(set(names))


def _scan_plan(url: str, auth, read_only: bool, spec_url: str | None = None) -> dict:
    """Preview the surface + the checks that will run. Read-only: discovers endpoints, attacks nothing."""
    import requests

    from execution.probes import MUTATING_PROBES
    from services.live_surface import (discover_endpoints, endpoints_from_spec, fetch_openapi,
                                        normalize_path_params)
    from services.results import AppModel

    sess = requests.Session()
    if auth and auth.get("cookie"):
        from execution.probes import seed_cookies
        seed_cookies(sess, auth.get("cookie"))
    base = url.rstrip("/")
    spec = fetch_openapi(base, session=sess, spec_url=spec_url)
    if spec:
        eps = endpoints_from_spec(spec)
        source = "OpenAPI/Swagger spec"
    else:
        eps = discover_endpoints(base, AppModel(root="", endpoints=[]), session=sess)
        source = "discovery (no spec found)"
    paths = sorted({normalize_path_params(str(e.get("path", ""))) for e in eps if e.get("path")})

    names = _all_probe_names()
    will_write = sorted(n for n in names if n in MUTATING_PROBES)
    skipped_write = will_write if read_only else []
    will_run = [n for n in names if not (read_only and n in MUTATING_PROBES)]

    missing = []
    has_token = bool(auth and (auth.get("token") or auth.get("cookie")))
    if not has_token:
        missing.append("No credential supplied (--token or --cookie): the authenticated probes "
                       "(JWT, forged-token, excessive-data on protected endpoints) will be skipped.")
    if not (auth and auth.get("token_b")):
        missing.append("No second user (--token-b): access-control checks between two users "
                       "(BOLA, BFLA, access-revocation) will be skipped.")
    if len(paths) < 5:
        missing.append(f"Only {len(paths)} endpoint(s) found — coverage will be low. "
                       "Point QAX at the app's OpenAPI spec, or pass --repo with its source.")

    return {
        "url": url, "read_only": read_only,
        "surface": {"source": source, "endpoint_count": len(paths), "sample": paths[:25]},
        "checks": {"total": len(names), "will_run": len(will_run),
                   "writes_data": will_write, "skipped_because_read_only": skipped_write},
        "missing_prerequisites": missing,
        "note": ("Read-only: QAX will send only GET/HEAD/OPTIONS requests." if read_only
                 else "Full scan: QAX will create small, clearly named test records and remove what it can."),
    }


_RUNS_DIR = os.path.join(os.getcwd(), "output", "web_runs")


def _save_run(payload: dict) -> str | None:
    """Persist a web scan payload so the UI can show run history and diffs. Best-effort; never raises."""
    try:
        from urllib.parse import urlparse
        os.makedirs(_RUNS_DIR, exist_ok=True)
        host = (urlparse(payload.get("url", "")).netloc or "app").replace(":", "_").replace(".", "-")
        rid = time.strftime("%Y%m%dT%H%M%S") + "_" + host
        payload = dict(payload, run_id=rid, scanned_at=time.strftime("%Y-%m-%d %H:%M"))
        with open(os.path.join(_RUNS_DIR, rid + ".json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        return rid
    except Exception:
        return None


@app.get("/api/runs")
def list_runs():
    """Run history: the most recent scans, newest first (summary only)."""
    out = []
    try:
        for name in sorted(os.listdir(_RUNS_DIR), reverse=True)[:100]:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(_RUNS_DIR, name), encoding="utf-8") as fh:
                    p = json.load(fh)
            except Exception:
                continue
            out.append({"run_id": p.get("run_id", name[:-5]), "url": p.get("url"),
                        "scanned_at": p.get("scanned_at"), "read_only": p.get("read_only"),
                        "claims": len(p.get("claims") or []), "leads": len(p.get("leads") or []),
                        "not_tested": len(p.get("coverage_gaps") or []),
                        "qax_commit": p.get("qax_commit")})
    except FileNotFoundError:
        pass
    return jsonify({"runs": out})


@app.get("/api/runs/<run_id>")
def get_run(run_id):
    """The full saved payload of one past scan (for review or diff)."""
    safe = os.path.basename(str(run_id))
    path = os.path.join(_RUNS_DIR, safe + ".json")
    if not os.path.isfile(path):
        return jsonify({"error": "no such run"}), 404
    with open(path, encoding="utf-8") as fh:
        return jsonify(json.load(fh))


@app.post("/api/plan")
def plan():
    """
    Preview what a scan WILL do before running it: the endpoints QAX can see, the checks it will run,
    which of them write data (skipped under read-only), and the prerequisites that are missing. Sends
    only read requests to discover the surface — it never attacks. So the operator approves before any
    active probe runs.
    """
    url, repo, auth, read_only, spec_url = _inputs()
    if not url:
        return jsonify({"error": "Please enter the URL of your running app."}), 400
    try:
        plan = _scan_plan(url, auth, read_only, spec_url)
    except Exception as exc:
        return jsonify({"error": f"Could not build a plan: {exc}"}), 502
    return jsonify(plan)


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
    """Normalise url/repo/token/cookie/read_only from the request (query for GET, JSON for POST)."""
    src = request.args if request.method == "GET" else (request.get_json(force=True, silent=True) or {})
    url = str(src.get("url", "")).strip()
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    repo = str(src.get("repo", "")).strip() or tempfile.mkdtemp(prefix="qax_web_")
    token = str(src.get("token", "")).strip()
    token_b = str(src.get("token_b", "")).strip()
    cookie = str(src.get("cookie", "")).strip()
    header = str(src.get("auth_header", "")).strip() or "Authorization"
    scheme = src.get("auth_scheme", "Bearer")
    scheme = "" if scheme == "" else (str(scheme).strip() or "Bearer")
    read_only = str(src.get("read_only", "")).strip().lower() in ("1", "true", "yes", "on")
    spec_url = str(src.get("spec", "")).strip() or None
    auth = None
    if token or token_b or cookie:
        auth = {"token": token, "token_b": token_b, "cookie": cookie, "header": header, "scheme": scheme}
    return url, repo, auth, read_only, spec_url


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


def _run_scan(url: str, repo: str, auth, *, read_only: bool = False, spec_url: str | None = None,
              progress=None) -> dict:
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
        # A read-only scan must not drive the UI tour either (it can submit forms), so the orchestrator
        # turns the tour off under read_only; passing tour=True is still honoured for a full scan.
        result = OrchestratorService(ctx).run(repo, url, deep=False, auth=auth, tour=not read_only,
                                              read_only=read_only, spec_url=spec_url,
                                              visual_baseline=visual_baseline,
                                              progress=progress)
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
    from core.version import run_record
    payload = {
        "url": url,
        "read_only": read_only,
        "claims": claims,
        "leads": leads,
        "screens": screens,
        # Honesty parity with the CLI: a quiet result is not a clean one.
        "not_tested": list(getattr(result, "not_tested", []) or []),
        "coverage_gaps": list(getattr(result, "coverage_gaps", []) or []),
        "left_on_target": list(getattr(result, "left_on_target", []) or []),
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
        **run_record(quick=True, read_only=read_only, tour=not read_only,
                     token_supplied=bool(auth and auth.get("token")),
                     cookie_supplied=bool(auth and auth.get("cookie"))),
    }
    _save_run(payload)
    return payload


@app.post("/api/scan")
def scan():
    url, repo, auth, read_only, spec_url = _inputs()
    if not url:
        return jsonify({"error": "Please enter the URL of your running app."}), 400
    if not _target_authorized():
        return jsonify({"error": _UNAUTHORIZED_TARGET}), 400
    payload = _run_scan(url, repo, auth, read_only=read_only, spec_url=spec_url)
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
    url, repo, auth, read_only, spec_url = _inputs()
    if not url:
        return jsonify({"error": "Please enter the URL of your running app."}), 400
    if not _target_authorized():
        return jsonify({"error": _UNAUTHORIZED_TARGET}), 400
    q: "queue.Queue" = queue.Queue()

    def work():
        try:
            payload = _run_scan(url, repo, auth, read_only=read_only, spec_url=spec_url, progress=_QueueProgress(q))
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
