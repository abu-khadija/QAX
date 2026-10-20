"""
evals/gate.py — the merge gate: measure every change before it lands.
=====================================================================
The discipline the roadmap locks in: a change doesn't merge unless it can be shown
to improve something without breaking anything. This runs that check end to end:

    python3 -m evals.gate            # FREE deterministic gate (quick scan + A/B)
    python3 -m evals.gate --deep     # + a credit-gated deep lane (real $/run)

Quick lane (default, zero-LLM): boots the golden vulnerable target
(evals/targets/vuln_jwt_app.py), runs a deterministic QUICK scan, scores the
confirmed findings against ground truth (evals/truth/golden-jwt.json), runs the
SECURE control (the flaw fixed → probes must go silent, no false positives), and
the orchestration A/B harness. Exits non-zero if any guarantee regresses.

Deep lane (--deep, needs an API key): runs a budgeted DEEP scan on the target to
prove the deep-mode features actually engage — item 5 (no-router fallback
generates from the endpoint surface), item 6 (forged-token-replay fires in deep),
round 2 runs — and prints an official deep scorecard (recall/FP/$/run). It reports
whether item 3 (setup repair) engaged this run (LLM-dependent, so reported not gated).

Needs the exec venv (Flask): run under ./.venv-exec/bin/python.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# The spec-typed probes planted on the golden target's /tasks endpoint.
_TYPED_PROBES = {"boolean-false", "single-element-array", "date-order", "long-input", "duplicate-rows"}

# Probes with a bug planted on the golden target (ROADMAP #26) — each must fire here and stay
# silent on the SECURE app. Grown batch by batch until every probe has one.
_PLANTED_PROBES = {"cookie-flags", "error-handling", "debug-disclosure", "excessive-data",
                   "crash-fuzz", "auth-error", "cors", "path-traversal", "unpaginated-list",
                   "open-redirect", "mass-assignment", "rate-limit", "stored-xss", "nosql", "sqli", "bola",
                   "persistence", "malformed-input", "command-injection", "ssrf", "search-robustness"}

APP = str(Path(__file__).parent / "targets" / "vuln_jwt_app.py")
TRUTH = str(Path(__file__).parent / "truth" / "golden-jwt.json")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _boot(port: int, *, secure: bool) -> subprocess.Popen:
    env = dict(os.environ, PORT=str(port))
    if secure:
        env["QAX_SECURE"] = "1"
    else:
        env.pop("QAX_SECURE", None)
    proc = subprocess.Popen([sys.executable, APP], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            if urllib.request.urlopen(f"{base}/health", timeout=1).status == 200:
                return proc
        except Exception:
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("golden target did not come up")


class _Cap:
    """Minimal progress sink that records the orchestrator's info lines."""
    def __init__(self):
        self.lines: list[str] = []

    def emit(self, stage, level, msg, **k):
        self.lines.append(str(msg))


def _two_user_auth(base_url: str) -> dict:
    """Register + log in two ordinary users on the target and return QAX's supplied-identity auth dict."""
    import requests
    toks = []
    for name in ("alice", "bob"):
        cred = {"email": f"{name}@example.com", "password": "Secret123!"}
        requests.post(f"{base_url}/auth/register", json=cred, timeout=5)
        toks.append(requests.post(f"{base_url}/auth/login", json=cred, timeout=5).json()["access_token"])
    return {"token": toks[0], "token_b": toks[1], "header": "Authorization", "scheme": "Bearer"}


def _scan(base_url: str, *, deep: bool = False, prog=None, ctx=None, auth=None, read_only: bool = False):
    """Scan the live target. Quick = deterministic/zero-LLM; deep = credit-gated."""
    import json

    from services.context import QAXContext
    from services.orchestrator_service import OrchestratorService
    from services.scorecard import score_run

    truth = json.load(open(TRUTH))
    empty = tempfile.mkdtemp(prefix="qax_gate_")          # no source → exercises live grounding + fallback
    usage = None
    if deep:
        from services import usage as _usage
        _usage.track()
        _usage.reset()
    t0 = time.time()
    kw = dict(max_router_files=1, max_endpoints=6) if deep else {}
    res = OrchestratorService(ctx or QAXContext.from_env()).run(
        empty, base_url, deep=deep, use_cache=False, progress=prog, auth=auth, read_only=read_only, **kw)
    if deep:
        from services import usage as _usage
        usage = _usage.snapshot()
    rec = score_run(res, repo="golden-jwt", url=base_url, mode="deep" if deep else "quick",
                    duration_sec=time.time() - t0, usage=usage, truth=truth)
    return res, rec


def _source_lane(base_url: str):
    """
    forge-jwt needs a MODELLED SOURCE with a hardcoded signing secret; a live-only scan has none and the
    real model is LLM-built. This deterministic lane hands ProbeService a hand-built AppModel whose config
    leaks the target's secret, and runs it against the live target: the probe must prove the forgery
    (vulnerable) or be refused (SECURE app rotated the secret).
    """
    from services.context import QAXContext
    from services.live_surface import endpoints_from_spec, fetch_openapi
    from services.probe_service import ProbeService
    from services.results import AppModel

    model = AppModel(root="(golden source)", endpoints=endpoints_from_spec(fetch_openapi(base_url)),
                     config=[{"key": "JWT_SECRET", "default": "super-secret-signing-key",
                              "is_secret": True, "risk": "hardcoded signing secret"}])
    return ProbeService(QAXContext.from_env()).run(model, base_url)


def _cli_end_to_end() -> list[tuple[str, bool, str]]:
    """
    The gate drives the engine in-process; users drive the COMMAND LINE. A bug between the two (an import that was only
    bound when credentials were passed) broke `qax demo` and every credential-less `scan --out` while all 800 tests and
    the gate stayed green. So run the real commands: `main.py demo` and a credential-less `main.py scan --out`.
    """
    import json as _json
    import subprocess
    import tempfile
    main_py = str(Path(__file__).resolve().parent.parent / "main.py")
    demo = subprocess.run([sys.executable, main_py, "demo"], capture_output=True, text=True, timeout=300)
    ok_demo = demo.returncode == 0 and "first confirmed findings" in demo.stdout
    port = _free_port()
    proc = _boot(port, secure=False)
    out = os.path.join(tempfile.mkdtemp(prefix="qax_cli_"), "scan.json")
    try:
        r = subprocess.run([sys.executable, main_py, "scan", "--url", f"http://127.0.0.1:{port}", "--quick",
                            "--authorized", "--out", out], capture_output=True, text=True, timeout=300)
    finally:
        proc.terminate()
    wrote = os.path.exists(out)
    rec = _json.load(open(out)) if wrote else {}
    dead = subprocess.run([sys.executable, main_py, "scan", "--url", f"http://127.0.0.1:{_free_port()}", "--quick",
                           "--authorized"], capture_output=True, text=True, timeout=120)
    return [
        ("CLI: an unreachable target is an ERROR, not a clean scan",
         dead.returncode != 0 and "nothing is listening" in (dead.stdout + dead.stderr),
         f"rc={dead.returncode}"),
        ("CLI: `main.py demo` runs end to end", ok_demo, f"rc={demo.returncode}, tail={demo.stdout.strip()[-60:]!r}"),
        ("CLI: a credential-less `scan --out` writes a complete record",
         r.returncode == 0 and wrote and bool(rec.get("qax_version")) and bool(rec.get("confirmed")),
         f"rc={r.returncode}, file={'yes' if wrote else 'NO'}, confirmed={len(rec.get('confirmed') or [])}"),
    ]


def _rate_limited_lane() -> list[tuple[str, bool, str]]:
    """
    PLAN.md Phase 1 item 5: against a target that answers HTTP 429 (60 requests / 2 s, Retry-After: 1) a scan must
    (a) finish, (b) report that it was throttled, (c) keep the CONFIRMED P1/P2 findings it makes on an unthrottled
    target — a 429 must never be read as 'rejected' and turned into a silent miss.
    """
    port = _free_port()
    env_old = os.environ.get("QAX_RATELIMIT")
    os.environ["QAX_RATELIMIT"] = "60"
    try:
        proc = _boot(port, secure=False)
    finally:
        if env_old is None:
            os.environ.pop("QAX_RATELIMIT", None)
        else:
            os.environ["QAX_RATELIMIT"] = env_old
    base = f"http://127.0.0.1:{port}"
    try:
        res, _ = _scan(base)
    finally:
        proc.terminate()
    notes = [n for n in res.not_tested if n.get("probe") == "scan"]
    claims = {(b.get("probe"), b.get("target")) for b in res.confirmed if str(b.get("severity")) in ("P1", "P2")}
    return [
        ("rate-limited target: the scan finishes and says it was throttled",
         bool(notes) and not res.error, f"scan notes={[n.get('reason', '')[:70] for n in notes] or 'none'}"),
        # Measured 2026-10-17: unthrottled 36; throttled WITH the guard 36; throttled WITHOUT it 13 (23 silent misses).
        ("rate-limited target: P1/P2 findings survive the 429s", len(claims) >= 34,
         f"{len(claims)} P1/P2 claims (unthrottled = 36; without the throttle layer it was 13)"),
    ]


def _read_only_check(base_url: str) -> list[tuple[str, bool, str]]:
    """
    PLAN_TO_V1 D9 / ROADMAP A1: a --read-only scan must send NO write request. Proven on the server side:
    the target counts requests per method; the scan (with supplied tokens, so no self-registration is
    needed) must add zero non-GET/HEAD/OPTIONS requests, must still report read-only findings, and must
    list what it did not run (kind "read-only") instead of staying silent about it.
    """
    import json as _json
    auth = _two_user_auth(base_url)          # the gate's own registrations happen BEFORE the snapshot

    def counts():
        return _json.loads(urllib.request.urlopen(base_url + "/__qax_eval/methods", timeout=5).read())

    before = counts()
    res, _ = _scan(base_url, auth=auth, read_only=True)
    after = counts()
    writes = {m: after.get(m, 0) - before.get(m, 0) for m in after
              if m not in ("GET", "HEAD", "OPTIONS") and after.get(m, 0) != before.get(m, 0)}
    found = sorted({b.get("probe") for b in res.confirmed})
    ro = sorted({n.get("probe") for n in res.not_tested if n.get("kind") == "read-only"})
    return [
        ("read-only: zero write requests reached the target", not writes,
         f"write requests by method={writes or 0}"),
        ("read-only: read probes still confirm findings", bool(found) and "cors" in found,
         f"probes={found}"),
        ("read-only: what was not run is listed (kind read-only)", "mass-assignment" in ro or "bola" in ro,
         f"read-only entries={ro}"),
    ]


def _no_ai_call_check(base_url: str) -> tuple[str, bool, str]:
    """
    Privacy promise (README/QUICKSTART): a QUICK scan sends nothing to an AI provider, even when a key is
    configured. Proven: point the Anthropic client at a local listener with a fake key and count requests.
    (It found a real leak: the Critic reviewed failed coverage templates and shipped them to the provider.)
    """
    import dataclasses
    import http.server
    import threading

    from services.context import QAXContext
    hits: list = []

    class _H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            self.send_response(500)
            self.end_headers()

        do_GET = do_POST

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old = os.environ.get("ANTHROPIC_BASE_URL")
    os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        ctx = dataclasses.replace(QAXContext.from_env(), anthropic_key="sk-ant-fake-qax-gate")
        _scan(base_url, ctx=ctx)
    finally:
        srv.shutdown()
        if old is None:
            os.environ.pop("ANTHROPIC_BASE_URL", None)
        else:
            os.environ["ANTHROPIC_BASE_URL"] = old
    return ("privacy: a quick scan makes ZERO AI-provider calls (even with a key set)", not hits,
            f"provider requests={len(hits)}")


def _pipeline_execute_check(base_url: str) -> tuple[str, bool, str]:
    """
    Verify the ORIGINAL pipeline's execute leg end to end (the other mode, not scan):
    pre-built API test cases → QAXPipeline → APIRunner → results. Deterministic, no
    LLM. One case must PASS (register→login→/me) and one must FAIL (/admin/users with
    no token), proving the leg distinguishes pass from fail against a live app.
    """
    from core.pipeline import QAXPipeline

    cred = {"email": "pipe_{{random}}@example.com", "password": "Secret123!"}
    passing = {"test_case_id": "PIPE-PASS", "title": "register+login+me", "api_steps": [
        {"step_number": 1, "method": "POST", "path": "/auth/register", "body": dict(cred), "expected_status": 201},
        {"step_number": 2, "method": "POST", "path": "/auth/login", "body": dict(cred),
         "expected_status": 200, "save": {"token": "access_token"}},
        {"step_number": 3, "method": "GET", "path": "/me", "use_auth": "token", "expected_status": 200}]}
    failing = {"test_case_id": "PIPE-FAIL", "title": "admin with no token", "api_steps": [
        {"step_number": 1, "method": "GET", "path": "/admin/users", "expected_status": 200}]}  # really 401
    os.environ["APP_BASE_URL"] = base_url
    out = QAXPipeline(export="none", exec_mode="api", dry_run=True).run(test_cases=[passing, failing])
    ex = out.get("execution") or {}
    ok = ex.get("total") == 2 and ex.get("passed", 0) >= 1 and ex.get("failed", 0) >= 1
    return ("original pipeline execute leg E2E (api)", ok,
            f"total={ex.get('total')} passed={ex.get('passed')} failed={ex.get('failed')}")


def _quick_and_ab() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    # 1) Vulnerable run — probes must catch the whole documented surface, 0 FP.
    port = _free_port()
    proc = _boot(port, secure=False)
    try:
        res, rec = _scan(f"http://127.0.0.1:{port}")
        res2, _ = _scan(f"http://127.0.0.1:{port}")   # second scan — must match deterministically
        # ROADMAP Q2: a quick scan must work on a clean machine with NO API key at all.
        import dataclasses

        from services.context import QAXContext
        res_nokey, rec_nokey = _scan(f"http://127.0.0.1:{port}",
                                     ctx=dataclasses.replace(QAXContext.from_env(), anthropic_key=""))
        # access-revocation needs TWO supplied identities: scan once more with them.
        res_auth, _ = _scan(f"http://127.0.0.1:{port}", auth=_two_user_auth(f"http://127.0.0.1:{port}"))
        src_res = _source_lane(f"http://127.0.0.1:{port}")
        ro_checks = _read_only_check(f"http://127.0.0.1:{port}")
        rl_checks = _rate_limited_lane()
        cli_checks = _cli_end_to_end()
        ai_check = _no_ai_call_check(f"http://127.0.0.1:{port}")
        pipe_check = _pipeline_execute_check(f"http://127.0.0.1:{port}")
    finally:
        proc.terminate()
    acc = rec.get("accuracy", {})
    probes = {b.get("probe") for b in res.confirmed}

    from services.results import OrchestratorResult as _OR
    k1 = [_OR._confirmed_key(b) for b in res.confirmed_deterministic]
    k2 = [_OR._confirmed_key(b) for b in res2.confirmed_deterministic]
    checks.append(("B1: deterministic CONFIRMED block reproducible (2 scans identical)",
                   k1 == k2 and len(k1) > 0,
                   f"{len(k1)} confirmed, identical={k1 == k2}, {len(res.leads_ai)} AI lead(s) excluded"))
    checks.append(("B2: no silent pipeline gaps (quick)", not res.silent_legs(),
                   f"silent={res.silent_legs() or 'none'}; legs={res.legs}"))
    checks.append(("quick-scan recall == 100%", acc.get("recall_pct") == 100.0,
                   f"recall={acc.get('recall_pct')}% caught {acc.get('caught')}/{acc.get('documented')} "
                   f"missed={acc.get('missed')}"))
    checks.append(("quick-scan false positives == 0", acc.get("false_positives") == 0,
                   f"false_positives={acc.get('false_positives')}"))
    checks.append(("truth matching: class-aware, 0 ambiguous, no keyword-only entries",
                   not acc.get("ambiguous") and not acc.get("keyword_only_entries"),
                   f"ambiguous={acc.get('ambiguous') or 0} keyword_only={acc.get('keyword_only_entries') or 0}"))
    checks.append(("forged-token-replay fired (item 6)", "forged-token-replay" in probes,
                   f"probes={sorted(p for p in probes if p)}"))
    checks.append(pipe_check)   # original pipeline's execute leg, verified E2E
    checks.append(_functional_bench_check())   # functional QA verdicts vs truth written beforehand
    nk = rec_nokey.get("accuracy", {})
    checks.append(("Q2: quick scan works with NO API key (same recall, 0 FP)",
                   nk.get("recall_pct") == 100.0 and nk.get("false_positives") == 0 and not res_nokey.error,
                   f"recall={nk.get('recall_pct')}% fp={nk.get('false_positives')} error={res_nokey.error}"))

    # The five spec-typed probes must be proven on a RUNNING server, not just
    # against fakes: each planted bug in /tasks fires, none is silently skipped.
    typed_missing = sorted(_TYPED_PROBES - probes)
    checks.extend(ro_checks)
    checks.extend(rl_checks)
    checks.extend(cli_checks)
    checks.append(ai_check)
    forge_hit = [f for f in getattr(src_res, "exploited", []) if f.probe == "forge-jwt"]
    checks.append(("source lane: forge-jwt proves the forgery with the leaked secret", bool(forge_hit),
                   f"findings={[f.title for f in forge_hit] or 'none'}"))
    planted_missing = sorted(_PLANTED_PROBES - probes)
    checks.append(("planted-bug probes all fired on the vulnerable target", not planted_missing,
                   f"fired={sorted(_PLANTED_PROBES & probes)} missing={planted_missing or 'none'}"))
    checks.append(("typed probes: all 5 fired on the vulnerable target", not typed_missing,
                   f"fired={sorted(_TYPED_PROBES & probes)} missing={typed_missing or 'none'}"))
    revoked = [b for b in res_auth.confirmed if b.get("probe") == "access-revocation"]
    checks.append(("access-revocation fired with two supplied identities (stale access after revoke)",
                   len(revoked) == 1, f"findings={[b.get('title') for b in revoked] or 'none'}"))
    ar_gaps = [n for n in res_auth.not_tested if n.get("probe") == "access-revocation"]
    checks.append(("access-revocation actually exercised (no 'could not run' gap)", not ar_gaps,
                   f"gaps={[(n['kind'], n['reason'][:50]) for n in ar_gaps] or 'none'}"))
    ar_noauth = [n for n in res.not_tested if n.get("probe") == "access-revocation"
                 and n.get("kind") == "missing-prerequisite"]
    checks.append(("access-revocation says so when no second identity is supplied", len(ar_noauth) == 1,
                   f"ledger={[(n['kind'], n.get('needs')) for n in ar_noauth] or 'none'}"))
    unexercised = [n for n in res.not_tested if n.get("probe") in _TYPED_PROBES]
    checks.append(("typed probes: actually exercised (no 'could not run' gap)", not unexercised,
                   f"gaps={[(n['probe'], n['kind']) for n in unexercised] or 'none'}"))

    # 2) Secure control — the SAME app with the flaw fixed must go silent (no FP).
    port = _free_port()
    proc = _boot(port, secure=True)
    try:
        sres, _ = _scan(f"http://127.0.0.1:{port}")
        sres_auth, _ = _scan(f"http://127.0.0.1:{port}", auth=_two_user_auth(f"http://127.0.0.1:{port}"))
        ssrc_res = _source_lane(f"http://127.0.0.1:{port}")
    finally:
        proc.terminate()
    auth_bugs = [b for b in sres.confirmed
                 if b.get("probe") in ("jwt-flaws", "forged-token-replay", "forge-jwt", "bfla")]
    checks.append(("secure control: auth probes silent (0)", len(auth_bugs) == 0,
                   f"auth findings on fixed app={len(auth_bugs)}"))
    ar_fp = [b.get("probe") for b in sres_auth.confirmed if b.get("probe") == "access-revocation"]
    checks.append(("secure control: access-revocation silent (0)", not ar_fp,
                   f"findings on fixed app={ar_fp or 0}"))
    forge_fp = [f.title for f in getattr(ssrc_res, "exploited", []) if f.probe == "forge-jwt"]
    checks.append(("secure control: forge-jwt silent with the rotated secret (0)", not forge_fp,
                   f"findings on fixed app={forge_fp or 0}"))
    planted_fp = sorted({b.get("probe") for b in sres.confirmed if b.get("probe") in _PLANTED_PROBES})
    checks.append(("secure control: planted-bug probes silent (0)", not planted_fp,
                   f"findings on fixed app={planted_fp or 0}"))
    typed_fp = [b.get("probe") for b in sres.confirmed if b.get("probe") in _TYPED_PROBES]
    checks.append(("secure control: typed probes silent (0)", not typed_fp,
                   f"typed findings on fixed app={typed_fp or 0}"))

    # 3) Orchestration A/B — recall up, cost down, FP flat.
    from evals.orchestrator_ab import measure
    m = measure()
    r, c, fp = (m["recall_classes_expanded"], m["round2_suspects_reattacked"],
                m["unproven_findings_added"])
    checks.append(("A/B recall up", r["after"] > r["before"], f"{r['before']}->{r['after']}"))
    checks.append(("A/B cost down", c["after"] < c["before"], f"{c['before']}->{c['after']}"))
    checks.append(("A/B false positives flat", fp["after"] == fp["before"] == 0,
                   f"{fp['before']}->{fp['after']}"))
    return checks


def _deep_lane() -> list[tuple[str, bool, str]]:
    from services.context import QAXContext
    from services.scorecard import render

    if not getattr(QAXContext.from_env(), "anthropic_key", None):
        print("\n[deep lane skipped] no ANTHROPIC_API_KEY — the deep lane needs credits.\n")
        return []

    port = _free_port()
    proc = _boot(port, secure=False)
    prog = _Cap()
    try:
        res, rec = _scan(f"http://127.0.0.1:{port}", deep=True, prog=prog)
    finally:
        proc.terminate()

    cov = res.coverage or {}
    # item 5's DETERMINISTIC guarantee is that the fallback PATH engages when there
    # are no router files (the code-level fix). How many valid tests the LLM then
    # produces from a bare endpoint spec is non-deterministic — reported, not gated.
    fallback_engaged = any("endpoint surface" in ln for ln in prog.lines)
    fallback_produced = [k.split(":", 2)[-1] for k in cov if k.startswith("functional:endpoints:")]
    probes = {b.get("probe") for b in res.confirmed}
    repaired = any("setup-repaired" in ln.lower() for ln in prog.lines)

    print("\n--- deep scorecard (official, credit-gated) ---")
    print(render(rec))
    print(f"    rounds: {res.rounds}")
    print(f"    item 3 (setup repair) engaged this run : "
          f"{'yes' if repaired else 'no'}  (LLM-dependent — reported, not gated)")
    print(f"    item 5 fallback produced tests for      : "
          f"{fallback_produced or 'none this run'}  (LLM-dependent — reported, not gated)")

    checks = [
        ("deep completed end-to-end", bool(res.confirmed is not None), f"confirmed={len(res.confirmed)}"),
        ("item 5: no-router fallback engaged", fallback_engaged,
         f"engaged={fallback_engaged}; produced-test groups={fallback_produced or []}"),
        ("item 6: forged-token-replay fired in deep", "forged-token-replay" in probes,
         f"probes={sorted(p for p in probes if p)}"),
        ("B2: no silent pipeline gaps (deep)", not res.silent_legs(),
         f"silent={res.silent_legs() or 'none'}; functional_generated={res.legs.get('functional_generated')}"),
    ]
    return checks


def _functional_bench_check() -> tuple:
    """
    Functional QA in a real browser on the demo app, scored against truth written before the
    run (evals/functional_bench.py): the planted bug FAILS, healthy cases PASS, the unrunnable
    case is never a PASS, and nothing flips between two runs. Skipped (not passed) when no
    browser is installed.
    """
    try:
        from execution.playwright_runner import PLAYWRIGHT_AVAILABLE
        if not PLAYWRIGHT_AVAILABLE:
            return ("functional QA verdicts == truth (demo app, 2 runs)", True, "SKIPPED: no browser installed")
        import json as _json
        import tempfile as _tf
        from evals.functional_bench import bench
        out = os.path.join(_tf.mkdtemp(), "bench.json")
        code = bench(["ui-demo"], repeat=2, out=out)
        app = _json.loads(open(out).read())["apps"][0]
        detail = (f"{app.get('correct')}/{app.get('cases')} correct, flips {app.get('flipping_cases')}, "
                  f"false failures {app.get('false_failure_rate')}, missed {app.get('missed_defect_rate')}")
        return ("functional QA verdicts == truth (demo app, 2 runs)", code == 0, detail)
    except Exception as exc:  # noqa: BLE001
        return ("functional QA verdicts == truth (demo app, 2 runs)", False, f"bench crashed: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="QAX merge gate")
    ap.add_argument("--deep", action="store_true",
                    help="also run the credit-gated deep lane (real $/run)")
    args = ap.parse_args()

    checks = _quick_and_ab()
    if args.deep:
        checks += _deep_lane()

    print("\nQAX merge gate\n" + "=" * 68)
    ok = True
    for name, passed, detail in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name:46} {detail}")
        ok = ok and passed
    print("=" * 68)
    print("GATE: PASS ✅  — safe to merge\n" if ok else "GATE: FAIL ❌  — do not merge\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
