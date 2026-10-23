"""
cli/scan_cmd.py — `qax scan`: the security scan of a running app.
Moved verbatim out of main.py (which re-exports every name), so main.py shrinks
toward argument parsing and dispatch.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from core.session_manager import SessionManager
from core.xray_client import XrayClient

logger = logging.getLogger("qax")


def scan_needs_key(quick: bool, repo: str | None) -> bool:
    """
    Whether a scan needs an Anthropic key: a DEEP scan (generation, red-team, critic) or any
    scan that models a --repo's source with the AI. A quick scan of a live app (probes +
    deterministic coverage templates) makes no model call and must run without one.
    """
    return (not quick) or bool(repo)


def _load_examples_file(path: str | None) -> dict:
    """Parse the --examples file; a missing/unreadable/invalid file is reported and ignored."""
    if not path:
        return {}
    try:
        from services.live_surface import parse_examples
        ex = parse_examples(Path(path).read_text(encoding="utf-8"))
        print(f"  (using {len(ex)} example body(ies) from {path})\n" if ex else
              f"  ! no usable example in {path} (expected JSON like {{\"POST /api/x\": {{...}}}}) — ignoring it\n")
        return ex
    except OSError as exc:
        print(f"  ! cannot read --examples file {path}: {exc} — continuing without it\n")
        return {}


def _load_endpoints_file(path: str | None) -> list:
    """Parse the --endpoints file; a missing/unreadable file is reported and ignored (the scan still runs)."""
    if not path:
        return []
    try:
        from services.live_surface import parse_endpoint_lines
        eps = parse_endpoint_lines(Path(path).read_text(encoding="utf-8"))
        print(f"  (using {len(eps)} endpoint(s) from {path})\n")
        return eps
    except OSError as exc:
        print(f"  ! cannot read --endpoints file {path}: {exc} — continuing without it\n")
        return []


def run_scan_command() -> None:
    """
    Handle the 'scan' subcommand — a deep, autonomous security + QA scan.

    Runs the whole agent team over a repository against a live app in one call:
    whole-system model → detectors → active probes → generation + red-team →
    coverage loop → execute → critic → investigator. Prints the confirmed bugs
    (from the Investigator and the probes) and the coverage it guaranteed.

    Called when sys.argv[1] == "scan".
    """
    parser = argparse.ArgumentParser(
        prog="qax scan",
        description="Deep autonomous scan: model the whole app, hunt and PROVE bugs.")
    parser.add_argument("--repo", default=None,
                        help="Path to the repository to scan. Optional — omit for a UI-only / "
                             "no-source target; QAX then scans the live app only (grounding + "
                             "probes + --tour).")
    parser.add_argument("--url", required=True,
                        help="Base URL of the running app (e.g. http://localhost:8000).")
    parser.add_argument("--project", default=None, help="QAX project name (optional).")
    parser.add_argument("--max-router-files", type=int, default=4,
                        help="Hard ceiling on router files for functional generation.")
    parser.add_argument("--max-endpoints", type=int, default=40,
                        help="Functional-generation budget in endpoints (routers chosen "
                             "highest-risk-first until spent, bounded by --max-router-files).")
    parser.add_argument("--quick", action="store_true",
                        help="Quick scan — skip the LLM-heavy passes (knowledge, functional "
                             "generation, red-team). Uses probes + deterministic coverage only; "
                             "near-free, still proves JWT forgery/crashes and the security classes.")
    parser.add_argument("--refresh", action="store_true",
                        help="Ignore the cached app model and rebuild it.")
    parser.add_argument("--out", default=None, help="Write the full result to this JSON file.")
    parser.add_argument("--truth", default=None,
                        help="Ground-truth JSON for the target (recall + false-positive scoring).")
    parser.add_argument("--token", default=None,
                        help="A valid auth token for apps QAX can't self-register into "
                             "(external IdP, offline-signed JWT, API key). Enables auth-gated probes.")
    parser.add_argument("--token-b", default=None,
                        help="A second user's token, for IDOR/BOLA (userB reaching userA's data).")
    parser.add_argument("--auth-header", default="Authorization",
                        help="Header name for the token (default: Authorization).")
    parser.add_argument("--auth-scheme", default="Bearer",
                        help="Scheme prefix for the token (default: Bearer; use '' for a raw value / API key).")
    parser.add_argument("--accepted", default=None, metavar="FILE",
                        help="Known / by-design findings that must not be counted again (reason + expiry "
                             "required per entry; they stay listed under ACCEPTED). Default: "
                             ".qax/accepted.json when present.")
    parser.add_argument("--reverify", type=int, default=0, metavar="N",
                        help="Re-run the probes N more times and CONFIRM only findings that fire "
                             "in every pass (the rest are reported as unstable). Gives the bug "
                             "report a measured 'Reproduced N+1/N+1'. Default 0 (off); 1-2 is typical.")
    parser.add_argument("--tour", action="store_true",
                        help="Also tour the live UI in a real browser and fold what it sees "
                             "(uncaught JS errors, failed API calls) into the findings. Needs "
                             "Playwright — run this scan under the exec venv (.venv-exec, Python 3.12).")
    parser.add_argument("--visual-baseline", default=None,
                        help="Directory of baseline screenshots for visual regression (with --tour). "
                             "First run seeds it; later runs flag screens whose rendering changed.")
    parser.add_argument("--dry-run", action="store_true",
                        help="No external side effects (exports); the scan still runs.")
    parser.add_argument("--spec", default=None, metavar="URL|PATH|FILE",
                        help="Where the app's OpenAPI/Swagger document is, when it is not at a standard path "
                             "(a URL, a path on the app like /apispec.json, or a local file).")
    parser.add_argument("--endpoints", default=None, metavar="FILE",
                        help="A hand-written endpoint list for apps with no OpenAPI spec whose routes cannot be "
                             "guessed. One per line: `GET /api/things`, `POST /api/things title!,body`. "
                             "See QUICKSTART.md.")
    parser.add_argument("--examples", default=None, metavar="FILE",
                        help="A JSON file of VALID request bodies for endpoints whose creates need data QAX "
                             "cannot guess: {\"POST /api/pages\": {\"title\": \"T {{unique}}\", ...}}. "
                             "`{{unique}}` is replaced per request.")
    parser.add_argument("--insecure", action="store_true",
                        help="Do not verify the target's TLS certificate (a staging server with a self-signed or "
                             "internal-CA certificate). Use ONLY for a target you control.")
    parser.add_argument("--header", action="append", default=[], metavar="\"Name: value\"",
                        help="An extra header sent with EVERY request (repeatable): a CSRF nonce for a cookie "
                             "session (`--header \"CSRF-Token: <nonce>\"`), a tenant id, an API version.")
    parser.add_argument("--cookie", default=None, metavar="\"name=value; ...\"",
                        help="Authenticate with a session cookie instead of a token (copy it from your "
                             "browser's DevTools). Carried on every request. Best with --read-only; a full "
                             "scan of a CSRF-protected app from a cookie is not supported yet.")
    parser.add_argument("--read-only", action="store_true",
                        help="Send NO write request (only GET/HEAD/OPTIONS): probes and tests that need to "
                             "create data, register users or log in are not run and are listed under NOT "
                             "TESTED. Use it for a first scan of a staging you do not own. Works best with "
                             "--token (no self-registration needed).")
    parser.add_argument("--authorized", action="store_true",
                        help="Confirm you OWN or are explicitly permitted to actively test this "
                             "target. A scan runs active, data-MUTATING probes (it creates test "
                             "records and sends adversarial input). Required — a scan refuses "
                             "without it. Never run against a target you are not authorized to test.")
    args = parser.parse_args(sys.argv[2:])

    # S4 — authorized-target gate: active probing mutates the target, so require an
    # explicit ownership/permission confirmation before anything runs.
    if not args.authorized:
        from execution.probes import MUTATING_PROBES
        print("\n  Refusing to scan: an active scan runs data-MUTATING probes "
              f"({', '.join(sorted(MUTATING_PROBES))})\n  — it creates test records and sends "
              "adversarial input to the target.\n  Re-run with --authorized to confirm you own "
              "or are permitted to test this target.\n")
        sys.exit(2)

    from services import scorecard as _scorecard
    from services import usage as _usage
    from services.context import QAXContext
    from services.orchestrator_service import OrchestratorService
    from services.progress import PrintProgressReporter

    if args.repo:
        if not Path(args.repo).exists():
            print(f"\n  Repo path not found: {args.repo}\n")
            sys.exit(1)
        repo_path = args.repo
    else:
        # No repo — UI-only / live-app-only scan. Use an empty dir so the model
        # degrades cleanly to live-app grounding + probes + tour.
        import tempfile
        repo_path = tempfile.mkdtemp(prefix="qax_nosrc_")

    try:
        ctx = QAXContext.from_env(project=args.project or "", dry_run=args.dry_run)
        # A key is needed only where an LLM is actually used: a DEEP scan, or modelling a
        # --repo's source. A quick scan of a live app (probes + coverage templates) makes no
        # model call, so it must run on a clean machine with no key (ROADMAP Q2).
        if scan_needs_key(args.quick, args.repo):
            ctx.require_anthropic_key()
        elif not ctx.anthropic_key:
            print("\n  (no ANTHROPIC_API_KEY — a quick scan of a live app needs none; the AI "
                  "verification of failing generated tests is simply skipped)")
    except Exception as exc:
        print(f"\n  {exc}\n")
        sys.exit(1)

    mode = "quick" if args.quick else "deep"
    print(f"\n  QAX {mode} scan — repo: {args.repo or '(none — live app only)'}  ·  app: {args.url}\n")

    # Instrument this run: count every LLM call (for $/run) and time it.
    _usage.track()
    _usage.reset()
    _auth = None
    from core.session_auth import parse_headers      # used below (--out run record) even with no credentials
    if args.token or args.token_b or args.cookie or args.header or args.insecure:
        _auth = {"token": args.token, "token_b": args.token_b,
                 "header": args.auth_header, "scheme": args.auth_scheme, "cookie": args.cookie,
                 "headers": parse_headers(args.header), "insecure": bool(args.insecure)}
        _how = "a session cookie" if (args.cookie and not args.token) else f"a supplied token via '{args.auth_header}'"
        print(f"  (authenticating with {_how})\n")
    _t0 = time.time()
    result = OrchestratorService(ctx).run(
        repo_path, args.url,
        deep=not args.quick,
        max_router_files=args.max_router_files,
        max_endpoints=args.max_endpoints,
        use_cache=not args.refresh,
        auth=_auth,
        tour=args.tour,
        visual_baseline=args.visual_baseline,
        reverify=max(0, int(args.reverify or 0)),
        read_only=args.read_only,
        spec_url=args.spec,
        extra_endpoints=_load_endpoints_file(args.endpoints),
        examples=_load_examples_file(args.examples),
        progress=PrintProgressReporter())
    _elapsed = time.time() - _t0

    if result.error:
        print(f"\n  Scan failed: {result.error}\n")
        sys.exit(1)

    # Known / by-design findings (core.accepted): split out BEFORE rendering so they are neither
    # counted nor filed, but always listed with their reason.
    _acc_text = ""
    try:
        from core import accepted as _acc
        _loaded = _acc.load(args.accepted)
        if _loaded["entries"] or _loaded["error"] or _loaded["invalid"]:
            _info = _acc.split(list(result.confirmed or []), _loaded["entries"])
            result.confirmed = _info["active"]
            result.accepted = [{**a["finding"], "accepted": a["entry"]} for a in _info["accepted"]]
            _acc_text = _acc.render(_info, _loaded)
    except Exception as exc:
        logger.warning("accepted-findings handling failed (nothing suppressed): %s", exc)

    from cli.scan_render import render_scan_result
    print(render_scan_result(result))   # S8: extracted, B1-aligned (deterministic vs AI leads)
    if _acc_text:
        print("\n" + _acc_text + "\n")

    # Scorecard — the honest, trackable number for this run.
    _truth = None
    if args.truth:
        try:
            _truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read --truth file: %s", exc)
    try:
        _rec = _scorecard.score_run(
            result, repo=(args.repo or args.url), url=args.url, mode=mode,
            duration_sec=_elapsed, usage=_usage.snapshot(), truth=_truth,
            read_only=bool(getattr(args, "read_only", False)))
        _path = _scorecard.write_run(_rec)
        print(_scorecard.render(_rec))
        if _path:
            print(f"     saved: {_path}")
        print()
    except Exception as exc:
        logger.warning("scorecard failed (scan result is unaffected): %s", exc)

    if args.out:
        try:
            # Record WHAT was scanned, so a finding filed later (file-findings) can
            # state its environment — a bug report without one is not reproducible.
            from core.build_fingerprint import build_fingerprint
            from core.version import run_record
            from services.live_surface import authed_session
            _out = {**result.to_dict(), "target_url": args.url,
                    "scan_mode": "quick" if args.quick else "deep",
                    # PLAN.md §6 run record: what produced this result (never the tokens themselves).
                    **run_record(quick=bool(args.quick), read_only=bool(args.read_only),
                                 reverify=int(args.reverify or 0), tour=bool(args.tour),
                                 repo_supplied=bool(args.repo), token_supplied=bool(args.token),
                                 token_b_supplied=bool(args.token_b), cookie_supplied=bool(args.cookie), insecure=bool(args.insecure),
                                 extra_headers=sorted(parse_headers(args.header)),
                                 auth_header=args.auth_header,
                                 auth_scheme=args.auth_scheme,
                                 accepted_file=args.accepted or None),
                    "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                    # Which build was scanned — so a later verify-fixes can tell "not fixed" from
                    # "not redeployed" (None = no stable component, i.e. unknown).
                    "build": build_fingerprint(args.url, session=authed_session(_auth))}
            Path(args.out).write_text(json.dumps(_out, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
            print(f"  Full result written to {args.out}\n")
        except Exception as exc:
            logger.warning("Could not write --out file: %s", exc)
