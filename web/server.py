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
import logging
import os
import queue
import tempfile
import threading
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from services.progress import ProgressReporter

logger = logging.getLogger("qax.web")

from core.public_error import clean, public_error  # noqa: E402

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app = Flask(__name__, static_folder=_STATIC, static_url_path="")


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _cross_site_block():
    """
    The local API can start scans, browser runs, git clones and Jira writes, so a web
    page the operator happens to visit must not be able to drive it (CSRF / DNS rebinding):
    - a browser request marked cross-site (Sec-Fetch-Site) is refused;
    - an Origin that is not this server is refused;
    - a write must be JSON (a cross-site page cannot send that without a CORS preflight,
      which this server never answers);
    - with no QAX_API_TOKEN, only a loopback Host is served (QAX_ALLOWED_HOSTS adds names).
    Returns a Flask response to refuse with, or None. CLI clients (no browser headers) pass.
    """
    from urllib.parse import urlparse
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return jsonify({"error": "cross-site request refused"}), 403
    origin = request.headers.get("Origin", "")
    if origin and origin != "null" and urlparse(origin).netloc != request.host:
        return jsonify({"error": "cross-origin request refused"}), 403
    if origin == "null":
        return jsonify({"error": "cross-origin request refused"}), 403
    if not os.getenv("QAX_API_TOKEN", ""):
        host = (request.host or "").rsplit(":", 1)[0] if not (request.host or "").startswith("[") \
            else (request.host or "").split("]")[0] + "]"
        allowed = _LOCAL_HOSTS | {h.strip() for h in os.getenv("QAX_ALLOWED_HOSTS", "").split(",") if h.strip()}
        if host not in allowed:
            return jsonify({"error": "this host is not allowed — set QAX_API_TOKEN (and QAX_ALLOWED_HOSTS) "
                                     "to serve beyond loopback"}), 403
    if request.method not in ("GET", "HEAD", "OPTIONS") and not request.is_json:
        return jsonify({"error": "send JSON (Content-Type: application/json)"}), 415
    return None


@app.after_request
def _security_headers(resp):
    """Clickjacking, MIME sniffing and referrer leaks are off; API answers are never cached."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.before_request
def _require_api_token():
    """
    S3 — auth gate for the JSON API. When `QAX_API_TOKEN` is set, every `/api/*`
    request must carry a matching `X-QAX-Token` header; otherwise 401. When it is NOT
    set, the API is open (local-dev default) — so exposing the server beyond loopback
    means setting the token first. Static pages and files are not gated.
    """
    import hmac
    if not request.path.startswith(("/api/", "/files/")):
        return None
    blocked = _cross_site_block()
    if blocked is not None:
        return blocked
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


def _scan_plan(url: str, auth, read_only: bool, spec_url: str | None = None, extras: dict | None = None) -> dict:
    """Preview the surface + the checks that will run. Read-only: discovers endpoints, attacks nothing."""
    import requests

    from execution.probes import MUTATING_PROBES
    from services.live_surface import (discover_endpoints, endpoints_from_spec, fetch_openapi,
                                        normalize_path_params)
    from services.results import AppModel

    from services.live_surface import authed_session
    sess = authed_session(auth)
    base = url.rstrip("/")
    from core.preflight import check_reachable
    unreachable = check_reachable(base, session=sess)
    if unreachable:
        raise RuntimeError(unreachable)
    spec = fetch_openapi(base, session=sess, spec_url=spec_url)
    if spec:
        eps = endpoints_from_spec(spec)
        source = "OpenAPI/Swagger spec"
    else:
        eps = discover_endpoints(base, AppModel(root="", endpoints=[]), session=sess)
        source = "discovery (no spec found)"
    if (extras or {}).get("endpoints"):
        from services.live_surface import apply_supplied_endpoints
        eps = apply_supplied_endpoints(base, extras["endpoints"], eps, sess)
        source += " + your endpoint list"
    if (extras or {}).get("examples"):
        from services.live_surface import apply_examples
        eps = apply_examples(eps, extras["examples"])
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
        from datetime import datetime as _dt
        rid = _dt.now().strftime("%Y%m%dT%H%M%S%f") + "_" + host     # two scans in one second no longer collide
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


def _diff_shape(run: dict) -> dict:
    """A saved web scan in the shape cli.diff.compare reads (the CLI's run record). Web scans carry no build id."""
    return {"confirmed": list(run.get("claims") or []), "not_tested": list(run.get("not_tested") or []),
            "target_url": run.get("url"), "scan_mode": "quick", "build": None, "accepted": []}


def _load_run(run_id) -> dict | None:
    """One saved scan by id (never a path), or None."""
    rid = os.path.basename(str(run_id or ""))
    path = os.path.join(_RUNS_DIR, rid + ".json")
    if not rid or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


@app.post("/api/diff")
def diff_runs():
    """What changed between two saved scans, by the rule of `qax diff`: a finding that is gone is NOT called
    fixed when its check did not run (it is listed as not re-checked) or when the build did not change."""
    data = request.get_json(silent=True) or {}
    old, new = _load_run(data.get("old")), _load_run(data.get("new"))
    if old is None or new is None:
        return jsonify({"error": "Pick two saved scans from the list."}), 404
    from cli.diff import ORDER, compare
    rep = compare(_diff_shape(old), _diff_shape(new))
    rows = [{"bucket": r["bucket"], "severity": r["finding"].get("severity", "?"),
             "title": r["finding"].get("title", ""), "target": r["finding"].get("target", ""), "why": r["why"]}
            for r in rep["rows"]]
    counts = {b: sum(1 for r in rows if r["bucket"] == b) for b in ORDER}
    return jsonify({"rows": rows, "counts": counts, "warnings": rep["warnings"], "ai": rep["ai"],
                    "old": {"run_id": old.get("run_id"), "scanned_at": old.get("scanned_at")},
                    "new": {"run_id": new.get("run_id"), "scanned_at": new.get("scanned_at")}})


@app.post("/api/plan")
def plan():
    """
    Preview what a scan WILL do before running it: the endpoints QAX can see, the checks it will run,
    which of them write data (skipped under read-only), and the prerequisites that are missing. Sends
    only read requests to discover the surface — it never attacks. So the operator approves before any
    active probe runs.
    """
    url, repo, auth, read_only, spec_url, extras = _inputs()
    if not url:
        return jsonify({"error": "Please enter the URL of your running app."}), 400
    try:
        plan = _scan_plan(url, auth, read_only, spec_url, extras)
    except Exception as exc:
        return jsonify({"error": public_error("Could not build a plan", exc, logger)}), 502
    return jsonify(plan)


@app.get("/")
def index():
    """Product homepage — what QAX is, how it works, and the evidence behind it.
    Falls back to the app UI until the homepage (static/index.html) exists."""
    page = "index.html" if os.path.exists(os.path.join(_STATIC, "index.html")) else "app.html"
    return send_from_directory(_STATIC, page)


@app.get("/how-we-measure")
def how_we_measure():
    """Every number QAX claims, its truth, its n and the command that repeats it."""
    return send_from_directory(_STATIC, "measure.html")


@app.get("/setup")
def setup_page():
    """Five steps to put the QAX release check on a team's pull requests."""
    return send_from_directory(_STATIC, "setup.html")


@app.get("/app")
def app_ui():
    """The working app — two tracks: the security scan, and functional QA (generate or load test
    cases, run them in a browser or over HTTP); both file to Jira/Xray."""
    return send_from_directory(_STATIC, "app.html")


@app.get("/files/<path:p>")
def files(p):
    """Serve tour screenshots saved under ./output (relative to the server cwd) — images only
    (output/ also holds reports, cloned repositories and run records, which are not served)."""
    if not p.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) or p.split("/", 1)[0] == "repos":
        return jsonify({"error": "not found"}), 404
    return send_from_directory(os.path.join(os.getcwd(), "output"), p)


class ScanCancelled(BaseException):
    """The viewer left: stop the scan at its next progress point (BaseException so the
    engine's `except Exception` guards do not swallow it)."""


# One scan at a time: scans hit the target hard, and the AI-usage counter is per process.
_SCAN_LOCK = threading.Lock()


class _QueueProgress(ProgressReporter):
    """Tees every engine progress event into a queue for live streaming."""

    def __init__(self, q: "queue.Queue") -> None:
        super().__init__()
        self._q = q
        self.cancelled = False

    def emit(self, step: str, status: str = "info", detail: str = "") -> None:
        if self.cancelled:
            raise ScanCancelled()
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
    insecure = str(src.get("insecure", "")).strip().lower() in ("1", "true", "yes", "on")
    spec_url = str(src.get("spec", "")).strip() or None
    from core.session_auth import parse_headers
    from services.live_surface import parse_endpoint_lines, parse_examples
    raw_headers = src.get("headers") or ""
    headers = parse_headers(raw_headers if isinstance(raw_headers, list) else str(raw_headers).splitlines())
    extras = {"endpoints": parse_endpoint_lines(str(src.get("endpoints", "") or "")),
              "examples": parse_examples(str(src.get("examples", "") or ""))}
    auth = None
    if token or token_b or cookie or headers or insecure:
        auth = {"token": token, "token_b": token_b, "cookie": cookie, "header": header, "scheme": scheme,
                "headers": headers, "insecure": insecure}
    return url, repo, auth, read_only, spec_url, extras


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
              extras: dict | None = None, progress=None) -> dict:
    """Run a quick scan and return the UI payload, or {'error': ...}. The empty temporary
    folder used when no repository was given is removed afterwards (they used to pile up)."""
    try:
        return _run_scan_inner(url, repo, auth, read_only=read_only, spec_url=spec_url,
                               extras=extras, progress=progress)
    finally:
        if repo and os.path.basename(repo.rstrip("/")).startswith("qax_web_") \
                and os.path.dirname(os.path.abspath(repo)) == os.path.abspath(tempfile.gettempdir()):
            import shutil
            shutil.rmtree(repo, ignore_errors=True)


def _run_scan_inner(url: str, repo: str, auth, *, read_only: bool = False, spec_url: str | None = None,
                    extras: dict | None = None, progress=None) -> dict:
    """The scan itself (see _run_scan)."""
    from services import scorecard, usage
    from services.context import QAXContext
    from services.orchestrator_service import OrchestratorService

    try:
        ctx = QAXContext.from_env()
    except Exception as exc:
        return {"error": public_error("Config error", exc, logger)}
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
                                              extra_endpoints=(extras or {}).get("endpoints") or None,
                                              examples=(extras or {}).get("examples") or None,
                                              visual_baseline=visual_baseline,
                                              progress=progress)
    except Exception as exc:
        return {"error": public_error("Scan failed", exc, logger)}
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
    url, repo, auth, read_only, spec_url, extras = _inputs()
    if not url:
        return jsonify({"error": "Please enter the URL of your running app."}), 400
    if not _target_authorized():
        return jsonify({"error": _UNAUTHORIZED_TARGET}), 400
    if not _SCAN_LOCK.acquire(blocking=False):
        return jsonify({"error": "A scan is already running — wait for it to finish."}), 409
    try:
        payload = _run_scan(url, repo, auth, read_only=read_only, spec_url=spec_url, extras=extras)
    finally:
        _SCAN_LOCK.release()
    return (jsonify(payload), 502) if payload.get("error") else jsonify(payload)


@app.post("/api/generate")
def generate():
    """
    Generate test cases from a user story and/or source code — step 1 of the functional QA
    track (no running app needed to generate; running them is /api/execute). Same stack as the CLI (GenerationService:
    SmartChecker → Claude → schema validation), so the UI never shows unvalidated cases.
    Returns {"tests": [...], "invalid": n, "summary": {...}} or {"error": ...}.
    """
    data = request.get_json(force=True, silent=True) or {}
    story = str(data.get("story", "")).strip()
    code = str(data.get("code", "")).strip()
    if not story and not code:
        return jsonify({"error": "Enter a user story or paste some source code."}), 400
    try:
        from services.context import QAXContext
        from services.exceptions import QAXConfigError
        from services.generation_service import GenerationOptions, GenerationService
        from services.progress import NullProgressReporter
        ctx = QAXContext.from_env()
        # Virtual source ids: nothing is read from disk and nothing is cached by name.
        source = "code://web-input" if code else "story://web-input"
        from core.app_mapper import load_map
        app_map = load_map(_clean_project(data.get("project")))   # read with "Read the app"
        opts = GenerationOptions(user_story=story or None, content=code or story,
                                 test_type=str(data.get("test_type") or "") or None,
                                 app_map=app_map)
        try:
            res = GenerationService(ctx).generate(source, options=opts,
                                                  progress=NullProgressReporter())
        except QAXConfigError as exc:
            return jsonify({"error": clean(str(exc))}), 400
    except Exception as exc:
        return jsonify({"error": _ai_error_message(exc)}), 502
    if res.error:
        return jsonify({"error": _ai_error_message(res.error)}), 502
    return jsonify({"tests": res.test_cases or [], "invalid": len(res.invalid or []),
                    "summary": res.summary or {},
                    "app_map_pages": len(app_map.pages) if app_map else 0})


def _clean_project(value) -> str:
    """A project name usable as a folder (letters, digits, - and _ only)."""
    return "".join(ch for ch in str(value or "") if ch.isalnum() or ch in "-_")[:64]


def _ai_error_message(err) -> str:
    """Turn an AI-provider error into a sentence the user can act on (the raw text stays in the log)."""
    raw = str(err or "")
    low = raw.lower()
    hint = " You can still load a test-cases file (Login, project and more inputs → Load a test-cases file) and run it."
    if "credit balance is too low" in low:
        return "Generation failed: the AI provider account has no credit left — add credit to the Anthropic account." + hint
    if "invalid x-api-key" in low or "authentication_error" in low or "api key" in low and "missing" in low:
        return "Generation failed: the Anthropic API key is missing or not valid (ANTHROPIC_API_KEY in .env)." + hint
    if "rate_limit" in low or "429" in low:
        return "Generation failed: the AI provider is rate-limiting this key — try again in a minute." + hint
    if "overloaded" in low or "529" in low:
        return "Generation failed: the AI provider is overloaded — try again shortly." + hint
    import logging
    logging.getLogger(__name__).warning("QAX generation error: %s", raw[:500])
    return "Generation failed: " + clean(raw, 300)


_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _grounding_block(tc: dict) -> str:
    """The executor's grounding gate, for the plan preview (no Playwright import needed)."""
    try:
        from execution.test_executor import grounding_block
        return grounding_block(tc)
    except Exception:  # noqa: BLE001
        return ""


def _execute_plan(tests: list, url: str, read_only: bool, engine: str = "api") -> dict:
    """
    What /api/execute would run.

    api engine: a case is runnable only when it has api_steps; in read-only mode only
    cases whose steps are all GET/HEAD/OPTIONS. ui (browser) engine: a case is runnable
    when it has automation or manual steps; it clicks and submits forms, so it never runs
    in read-only mode. Everything else is listed as not run, with the reason — never passed.
    """
    runnable, not_run = [], []
    for i, tc in enumerate(tests):
        if not isinstance(tc, dict):
            continue
        tc.setdefault("test_case_id", f"TC-WEB-{i + 1:03d}")
        if engine == "ui":
            has_steps = tc.get("automation_steps") or tc.get("manual_steps") or tc.get("steps")
            if not has_steps:
                not_run.append({"test_case_id": tc["test_case_id"], "title": tc.get("title", ""),
                                "reason": "no steps"})
            elif read_only:
                not_run.append({"test_case_id": tc["test_case_id"], "title": tc.get("title", ""),
                                "reason": "read-only is on: the browser engine clicks and submits forms — untick read-only to run it (staging only)"})
            elif _grounding_block(tc):
                not_run.append({"test_case_id": tc["test_case_id"], "title": tc.get("title", ""),
                                "reason": _grounding_block(tc)})
            else:
                runnable.append(tc)
            continue
        steps = tc.get("api_steps") or []
        if not steps:
            not_run.append({"test_case_id": tc["test_case_id"], "title": tc.get("title", ""),
                            "reason": "browser case (no API steps) — choose the Browser engine to run it"})
            continue
        writes = [str(s.get("method") or "GET").upper() for s in steps
                  if str(s.get("method") or "GET").upper() not in _SAFE_METHODS]
        if read_only and writes:
            not_run.append({"test_case_id": tc["test_case_id"], "title": tc.get("title", ""),
                            "reason": f"read-only: would send {', '.join(sorted(set(writes)))}"})
            continue
        runnable.append(tc)
    return {"url": url, "read_only": read_only, "engine": engine,
            "runnable": [{"test_case_id": t["test_case_id"], "title": t.get("title", ""),
                          "steps": len(t.get("api_steps") or t.get("automation_steps")
                                       or t.get("manual_steps") or [])} for t in runnable],
            "not_run": not_run, "_cases": runnable}


@app.post("/api/execute")
def execute_tests():
    """
    Run generated test cases against the running app and return per-case verdicts with
    evidence. engine "api" = HTTP requests; engine "ui" = a real headless browser
    (Playwright), with selectors from config/projects/<project>/selectors.json. Same gates
    as a scan: the caller must confirm authorization; read-only is honoured (the browser
    engine does not run in read-only); the API runner keeps every request on the target
    origin. AI healing stays off (no page content leaves the machine). `preview: true`
    returns the plan only (nothing is sent).
    """
    data = request.get_json(force=True, silent=True) or {}
    tests = data.get("tests") or []
    url = str(data.get("url", "")).strip().rstrip("/")
    read_only = str(data.get("read_only", "true")).lower() not in ("0", "false", "no", "off")
    engine = "ui" if str(data.get("engine", "api")).lower() in ("ui", "browser") else "api"
    project = "".join(ch for ch in str(data.get("project", "")) if ch.isalnum() or ch in "-_")
    if not tests:
        return jsonify({"error": "Generate or load test cases first."}), 400
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "Enter the URL of your running app (http:// or https://)."}), 400
    selection = None
    if str(data.get("changed_since", "")).strip():
        # run only what a change can affect (core.change_selection) — git diff of a local checkout
        from core.app_mapper import CONFIG_DIR
        from core.change_selection import changed_files, select
        repo_dir = str(data.get("repo", "")).strip()
        from pathlib import Path as _P
        if not repo_dir or not _P(repo_dir).expanduser().is_dir() or \
                any(x.startswith(".") for x in _P(repo_dir).expanduser().resolve().parts[1:]):
            return jsonify({"error": "Give the app's local git checkout (a folder) to compare changes."}), 400
        files, err = changed_files(str(_P(repo_dir).expanduser()), str(data["changed_since"]).strip(), "HEAD")
        if err:
            return jsonify({"error": f"Could not read the change: {err}"}), 400
        selection = select(tests, files, str(_P(repo_dir).expanduser()), CONFIG_DIR / (project or "_none_"))
        selection["files"] = len(files)
        keep = {x["id"] for x in selection["selected"]}
        tests = [t for t in tests if str(t.get("test_case_id", "?")) in keep]
    plan = _execute_plan(tests, url, read_only, engine)
    if selection is not None:
        plan["selection"] = selection
    cases = plan.pop("_cases")
    if data.get("preview"):
        return jsonify({"plan": plan})
    if not _target_authorized():
        return jsonify({"error": _UNAUTHORIZED_TARGET, "plan": plan}), 400
    if not cases:
        return jsonify({"plan": plan, "results": [], "total": 0, "passed": 0, "failed": 0,
                        "not_run": len(plan["not_run"])})
    from services import usage as _usage
    _t0 = time.monotonic()
    _meter_cm = _usage.measure()
    _meter = _meter_cm.__enter__()
    try:
        from services.execution_service import ExecutionService
        if engine == "ui":
            on = lambda v: str(v).lower() in ("1", "true", "yes", "on")  # noqa: E731
            try:
                workers = int(data.get("workers") or 1)
            except (TypeError, ValueError):
                workers = 1
            res = _run_browser(cases, url, project, insecure=on(data.get("insecure", "")),
                               watch=on(data.get("watch", "")), trace=on(data.get("trace", "")),
                               browser=str(data.get("browser", "")), device=str(data.get("device", "")),
                               workers=workers, visual=on(data.get("visual", "")),
                               a11y=str(data.get("a11y", "basic")))
        else:
            token = str(data.get("token", "")).strip()
            res = ExecutionService().execute(cases, mode="api", base_url=url,
                                             auth={"token": token} if token else None)
    except BrowserBusy:
        raise
    except Exception as exc:
        return jsonify({"error": public_error("Execution failed", exc, logger), "plan": plan}), 502
    finally:
        _meter_cm.__exit__(None, None, None)
    # measured for this run only: wall time and AI calls (healing is off in the UI, so normally 0)
    cost = {"duration_s": round(time.monotonic() - _t0, 1), "cases": len(cases), **_meter}
    raw = res.raw or {}
    out = {"plan": plan, "results": raw.get("results", []), "total": raw.get("total", 0),
           "passed": raw.get("passed", 0), "failed": raw.get("failed", 0),
           "skipped": raw.get("skipped", 0), "errors": raw.get("errors", 0),
           "not_run": len(plan["not_run"]), "writes_sent": raw.get("writes_sent", 0),
           "error": res.error, "cost": cost}
    if out["results"] and not res.error:
        # per-project memory: what changed since the last run (regressions, fixes, flaky cases)
        from core.run_history import compare, history, save_run
        hist_project = project or _clean_project(url.split("//", 1)[-1]) or "default"
        from core.run_history import quarantine as _q
        quarantined = _q(hist_project)
        for r in out["results"]:
            if str(r.get("test_case_id")) in quarantined:
                r["quarantined"] = True      # still run and shown — never filed as a bug
        out["changes"] = compare(history(hist_project), out["results"])
        save_run(hist_project, url, out["results"], cost=cost, selection=selection)
    return (jsonify(out), 502) if res.error else jsonify(out)


_BROWSER_LOCK = __import__("threading").Lock()


class BrowserBusy(Exception):
    """Another browser job (a run or an app read) holds the browser for longer than the wait."""


def _browser_wait() -> float:
    """Seconds a request waits for the browser before answering 409 busy (QAX_BROWSER_WAIT, default 5)."""
    try:
        return max(0.0, min(float(os.getenv("QAX_BROWSER_WAIT", "5")), 600.0))
    except ValueError:
        return 5.0


@__import__("contextlib").contextmanager
def _browser_lock():
    """
    Hold the browser lock, waiting at most QAX_BROWSER_WAIT seconds. A request that cannot get it
    gets a clear 409 ("busy — try again") instead of hanging behind a run that can take minutes.
    """
    if not _BROWSER_LOCK.acquire(timeout=_browser_wait()):
        raise BrowserBusy()
    try:
        yield
    finally:
        _BROWSER_LOCK.release()


@app.errorhandler(BrowserBusy)
def _browser_busy(_exc):
    """409 with a sentence the user can act on."""
    return jsonify({"error": "QAX is already using the browser for another run or app read — "
                             "try again when it finishes.", "busy": True}), 409


def _run_browser(cases: list, url: str, project: str, insecure: bool = False,
                 watch: bool = False, trace: bool = False, browser: str = "", device: str = "",
                 workers: int = 1, visual: bool = False, a11y: str = "basic"):
    """
    One browser run at a time: the UI engine reads the target and the selector project
    from the process environment, so they are set — and restored — under a lock.
    AI healing is forced off for web runs.
    """
    from services.execution_service import ExecutionService
    keys = ("APP_BASE_URL", "DEFAULT_PROJECT", "QAX_AI_HEAL", "AUTO_BUG_REPORT", "QAX_INSECURE",
            "QAX_SLOW_MO_MS", "QAX_STEP_SCREENSHOTS", "QAX_TRACE", "QAX_BROWSER", "QAX_DEVICE", "QAX_WORKERS", "QAX_VISUAL",
            "QAX_A11Y", "QAX_PAGE_QUALITY")
    with _browser_lock():
        saved = {k: os.environ.get(k) for k in keys}
        try:
            os.environ["APP_BASE_URL"] = url
            os.environ["QAX_AI_HEAL"] = "false"
            os.environ["AUTO_BUG_REPORT"] = "false"
            os.environ["QAX_INSECURE"] = "true" if insecure else "false"
            # every step is photographed in the web UI (the results show them); watch = a visible,
            # slowed-down browser on this machine; trace = a Playwright trace per case
            os.environ["QAX_STEP_SCREENSHOTS"] = "true"
            os.environ["QAX_SLOW_MO_MS"] = "400" if watch else "0"
            os.environ["QAX_TRACE"] = "true" if trace else "false"
            os.environ["QAX_BROWSER"] = browser if browser in ("chromium", "firefox", "webkit") else "chromium"
            os.environ["QAX_DEVICE"] = device if device in ("desktop", "mobile", "mobile-ios", "tablet") else "desktop"
            os.environ["QAX_WORKERS"] = str(max(1, min(int(workers or 1), 4))) if not watch else "1"
            os.environ["QAX_VISUAL"] = "true" if visual else "false"
            # page checks on every page a case visits: off, basic (built in) or axe (full WCAG A/AA)
            a11y = a11y if a11y in ("off", "basic", "axe") else "basic"
            os.environ["QAX_PAGE_QUALITY"] = "false" if a11y == "off" else "true"
            os.environ["QAX_A11Y"] = "axe" if a11y == "axe" else "basic"
            if project:
                os.environ["DEFAULT_PROJECT"] = project
            return ExecutionService().execute(cases, mode="ui", headless=not watch)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _local_checkout(path: str) -> str:
    """A local git checkout given in the UI: an existing folder, no hidden folders on the way ("" otherwise)."""
    from pathlib import Path as _P
    try:
        p = _P(str(path or "").strip()).expanduser()
        if not str(path or "").strip() or not p.is_dir() or any(x.startswith(".") for x in p.resolve().parts[1:]):
            return ""
        return str(p)
    except Exception:  # noqa: BLE001
        return ""


@app.post("/api/release-check")
def release_check_route():
    """
    One decision for a change: the cases it can affect in a real browser + the quick security scan →
    GO / NO-GO / NOT PROVEN with the report (services.release_check_service). Same gates as a run and a scan.
    """
    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get("url", "")).strip()
    tests = [t for t in (data.get("tests") or []) if isinstance(t, dict)]
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "Enter the URL of your running app (http:// or https://)."}), 400
    if not tests:
        return jsonify({"error": "Load or write the test cases first."}), 400
    if not _target_authorized():
        return jsonify({"error": _UNAUTHORIZED_TARGET}), 400
    repo = _local_checkout(data.get("repo", ""))
    since = str(data.get("changed_since", "")).strip()
    if since and not repo:
        return jsonify({"error": "Give the app's local git checkout (a folder) to compare changes."}), 400
    if not _SCAN_LOCK.acquire(blocking=False):
        return jsonify({"error": "A scan is already running — wait for it to finish.", "busy": True}), 409
    try:
        with _browser_lock():
            from services.release_check_service import run_release_check
            d = run_release_check(tests, url, _clean_project(data.get("project")), repo=repo, changed_since=since,
                                  scan=data.get("scan", True) is not False,
                                  scan_read_only=not bool(data.get("scan_writes")),
                                  fail_on=str(data.get("fail_on") or "P1").upper()[:2],
                                  a11y=str(data.get("a11y") or "basic"),
                                  a11y_fail_on=str(data.get("a11y_fail_on") or ""))
    finally:
        _SCAN_LOCK.release()
    return jsonify(d)


@app.post("/api/fix-brief")
def fix_brief_route():
    """A failed case as a Markdown brief for a coding agent (core.fix_brief). Nothing is run."""
    from core.fix_brief import build
    data = request.get_json(force=True, silent=True) or {}
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    if not result:
        return jsonify({"error": "Give the failed case's result."}), 400
    case = data.get("case") if isinstance(data.get("case"), dict) else None
    md = build(result, case, base_url=str(data.get("url", ""))[:300], project=_clean_project(data.get("project")),
               repo=_local_checkout(data.get("repo", "")))
    return jsonify({"markdown": md, "test_case_id": result.get("test_case_id")})


@app.post("/api/import-recording")
def import_recording_route():
    """A Chrome Recorder JSON export or a Playwright codegen script → one QAX case (core.recording_import)."""
    from core.recording_import import convert
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get("text", ""))
    if not text.strip() or len(text) > 2_000_000:
        return jsonify({"error": "Paste or load a recording (Chrome Recorder JSON or Playwright codegen)."}), 400
    r = convert(text, case_id=str(data.get("case_id", ""))[:40], title=str(data.get("title", ""))[:120])
    return (jsonify(r), 400) if not r["case"] else jsonify(r)


@app.post("/api/reproduce")
def reproduce_route():
    """
    A bug ticket (text, or a Jira key read with JIRA_* — read-only) → its steps run in the browser →
    REPRODUCED / NOT REPRODUCED / COULD NOT RUN / NOT DECIDABLE, with the steps, why, and a fix brief.
    """
    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "Enter the URL of your running app (http:// or https://)."}), 400
    text = str(data.get("text", ""))[:100_000]
    key = str(data.get("jira", "")).strip()
    if not text.strip() and key:
        import re as _re
        if not _re.fullmatch(r"[A-Z][A-Z0-9_]+-\d+", key):
            return jsonify({"error": "A Jira key looks like SHOP-123."}), 400
        from cli.reproduce_cmd import _jira_text
        text = _jira_text(key)
        if not text:
            return jsonify({"error": f"Could not read {key} from Jira (check JIRA_* in .env), or paste the ticket."}), 400
    if not text.strip():
        return jsonify({"error": "Paste the ticket (title, steps to reproduce, expected, actual) or give its Jira key."}), 400
    if not _target_authorized():
        return jsonify({"error": _UNAUTHORIZED_TARGET}), 400
    with _browser_lock():
        from core.ticket_repro import ai_writer, reproduce
        project = _clean_project(data.get("project"))
        writer = ai_writer(url, project) if str(data.get("ai_writer", "")).lower() in ("1", "true", "yes", "on") else None
        r = reproduce(text, url, project, repo=_local_checkout(data.get("repo", "")), writer=writer)
    return jsonify(r)


@app.post("/api/junit")
def junit_route():
    """The results of a run as JUnit XML (for CI dashboards). The results come from the page; nothing is
    run or stored."""
    from core.junit import to_junit
    data = request.get_json(force=True, silent=True) or {}
    results = [r for r in (data.get("results") or []) if isinstance(r, dict)][:2000]
    return Response(to_junit({"results": results}), mimetype="application/xml",
                    headers={"Content-Disposition": "attachment; filename=qax-junit.xml"})


@app.get("/api/artifact")
def artifact():
    """
    A screenshot or trace a run saved under output/ (the web UI shows step screenshots).
    Only files inside output/screenshots and output/traces are served.
    """
    from pathlib import Path

    from flask import send_file
    from core.paths import output_dir
    root = output_dir()
    raw = str(request.args.get("path", ""))
    try:
        target = (Path(raw) if Path(raw).is_absolute() else (root.parent / raw)).resolve()
        allowed = [(root / "screenshots").resolve(), (root / "traces").resolve(), (root / "network").resolve()]
        if not any(target.is_relative_to(a) for a in allowed) or not target.is_file():
            return jsonify({"error": "not found"}), 404
        return send_file(target)
    except Exception:  # noqa: BLE001
        return jsonify({"error": "not found"}), 404


@app.get("/api/doctor")
def doctor_route():
    """Setup status for the UI's Setup panel (core.doctor): names and states only, never a value."""
    from core.doctor import run_checks
    return jsonify({"checks": run_checks()})


@app.get("/api/config")
def ui_config():
    """What the UI may offer: whether live export is enabled server-side (never a secret)."""
    try:
        from execution.playwright_runner import PLAYWRIGHT_AVAILABLE
    except Exception:  # noqa: BLE001
        PLAYWRIGHT_AVAILABLE = False
    return jsonify({"live_export": os.getenv("QAX_ALLOW_LIVE_EXPORT", "false").lower() == "true",
                    "token_required": bool(os.getenv("QAX_API_TOKEN", "")),
                    "browser": bool(PLAYWRIGHT_AVAILABLE),
                    "project": os.getenv("DEFAULT_PROJECT", "")})


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
                      "references": [], "error": public_error("Bug filing failed", exc, logger)}
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
        return jsonify({"error": public_error("Export failed", exc, logger)}), 502
    result = dict(result or {})
    result.update({"dry_run": dry_run, "target": target, "kind": "tests"})
    return jsonify(result)


@app.route("/api/scan/stream", methods=["GET", "POST"])
def scan_stream():
    """
    Live scan as Server-Sent Events. The UI POSTs a JSON body (a session cookie, tokens and a CSRF nonce must never
    travel in a URL: they would land in logs and history). GET still works for curl and old callers — but never
    from a browser page: an <img src> on any localhost page is "same-site" and sends no Origin, so a browser GET
    must come from QAX's own page (Sec-Fetch-Site: same-origin) or from no page at all (none / absent).
    """
    if request.method == "GET" and request.headers.get("Sec-Fetch-Site", "none").lower() not in ("same-origin", "none"):
        return jsonify({"error": "starting a scan with a GET from another page is refused — POST JSON"}), 403
    url, repo, auth, read_only, spec_url, extras = _inputs()
    if not url:
        return jsonify({"error": "Please enter the URL of your running app."}), 400
    if not _target_authorized():
        return jsonify({"error": _UNAUTHORIZED_TARGET}), 400
    if not _SCAN_LOCK.acquire(blocking=False):
        return jsonify({"error": "A scan is already running — wait for it to finish."}), 409
    q: "queue.Queue" = queue.Queue()
    prog = _QueueProgress(q)

    def work():
        try:
            payload = _run_scan(url, repo, auth, read_only=read_only, spec_url=spec_url, extras=extras, progress=prog)
            q.put({"type": "error", "error": payload["error"]} if payload.get("error")
                  else {"type": "result", "payload": payload})
        except ScanCancelled:
            logger.info("QAX: scan of %s stopped — the viewer left", url)
        except Exception as exc:
            q.put({"type": "error", "error": public_error("Scan failed", exc, logger)})
        finally:
            _SCAN_LOCK.release()
            q.put({"type": "done"})

    threading.Thread(target=work, daemon=True).start()

    def gen():
        # no "retry:" — a reconnecting EventSource would start a NEW scan
        try:
            while True:
                try:
                    ev = q.get(timeout=20)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield "data: " + json.dumps(ev) + "\n\n"
                if ev.get("type") == "done":
                    break
        finally:
            prog.cancelled = True        # the viewer left (or the scan ended): stop sending to the target

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


# The functional-QA (E2E) routes live in web/functional.py
import sys as _sys  # noqa: E402
from web.functional import register as _register_functional  # noqa: E402

_register_functional(app, _sys.modules[__name__])


def serve() -> None:
    """Start the web UI (the `qax-web` command and `python -m web.server`)."""
    from core.console import force_utf8_output
    force_utf8_output()
    # Loopback by default. QAX_WEB_HOST=0.0.0.0 is for a container (publish the port on the
    # host's loopback only); anywhere else, set QAX_API_TOKEN first.
    _host = os.getenv("QAX_WEB_HOST", "127.0.0.1") or "127.0.0.1"
    if _host not in ("127.0.0.1", "localhost", "::1") and not os.getenv("QAX_API_TOKEN") \
            and not os.getenv("QAX_IN_CONTAINER"):
        raise SystemExit("QAX: refusing to listen on %s without QAX_API_TOKEN." % _host)
    app.run(host=_host, port=int(os.getenv("QAX_WEB_PORT", "8900") or 8900), debug=False, threaded=True)


if __name__ == "__main__":
    serve()
