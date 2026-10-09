"""
services/probe_service.py — ProbeService (Phase 3: broaden execution)
=====================================================================
The Investigator can only verify what the HTTP/UI runner can express. Some
exploits need more — forging a token, crashing an endpoint with oversized input.
ProbeService runs those active probes against the live app, driven by what the
AppModel found (a hardcoded secret → try forging a JWT). A probe that succeeds is
proof on its own, so its findings come back already confirmed — no Critic needed.

Never prints, never calls sys.exit(); probe failures are captured, never raised.

Usage:
    from services.probe_service import ProbeService
    result = ProbeService(ctx).run(app_model, base_url="http://localhost:8000")
    for f in result.exploited:
        print(f.title, "—", f.evidence)
"""

from __future__ import annotations

import logging

from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import AppModel, ProbeResult

logger = logging.getLogger(__name__)


class ProbeService:
    """Run active exploit probes against a live app, driven by the AppModel."""

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Config/credentials. Built from the
                environment when None. (Probes make no model calls.)
        """
        self.context = context or QAXContext.from_env()

    def run(
        self,
        model: AppModel,
        base_url: str,
        *,
        auth: dict | None = None,
        progress: ProgressReporter | None = None,
    ) -> ProbeResult:
        """
        Run the probes relevant to this app against the live target.

        Args:
            model (AppModel): The whole-system model (selects which probes to run).
            base_url (str): The running app to probe.
            auth (dict | None): Supplied authentication for apps QAX cannot self-
                register into (external IdP, offline-signed JWT, API key). Keys:
                `token` (user A), `token_b` (user B, for IDOR/BOLA), `header`
                (default "Authorization"), `scheme` (default "Bearer"; "" = raw).
            progress (ProgressReporter | None): Progress sink.

        Returns:
            ProbeResult: Probe outcomes; the exploited ones are confirmed bugs.
                Never raises at runtime.
        """
        prog = progress or NullProgressReporter()
        if not isinstance(model, AppModel) or not model.success:
            return ProbeResult(error="No valid AppModel to drive probes.")
        if not base_url or not str(base_url).strip():
            return ProbeResult(error="No base_url to probe against.")

        from execution.probes import (bola_probe, cookie_flags_probe, cors_probe, crash_fuzz_probe,
                                       debug_disclosure_probe, error_handling_probe,
                                       excessive_data_probe, forge_jwt_probe, injection_probe,
                                       jwt_flaws_probe, malformed_input_probe, nosql_probe,
                                       path_traversal_probe, security_headers_probe, sqli_probe)

        prog.emit("probe", "start", "running active probes")
        findings = []

        # Forge-JWT — only when the model found a hardcoded secret to forge with.
        if model.exposed_secrets:
            try:
                f = forge_jwt_probe(model, base_url)
                if f is not None:
                    findings.append(f)
            except Exception as exc:
                logger.error("forge_jwt probe failed: %s", exc)

        # JWT validation flaws (alg=none, signature not verified) — needs a token.
        try:
            findings.extend(jwt_flaws_probe(model, base_url, auth=auth))
        except Exception as exc:
            logger.error("jwt_flaws probe failed: %s", exc)

        # SQLi (path params) — error-based, with a benign-vs-quote control.
        try:
            findings.extend(sqli_probe(model, base_url, auth=auth))
        except Exception as exc:
            logger.error("sqli probe failed: %s", exc)

        # Injection via query params and request bodies (same control).
        try:
            findings.extend(injection_probe(model, base_url, auth=auth))
        except Exception as exc:
            logger.error("injection probe failed: %s", exc)

        # NoSQL (operator) injection on write endpoints whose body feeds a query.
        try:
            findings.extend(nosql_probe(model, base_url, auth=auth))
        except Exception as exc:
            logger.error("nosql probe failed: %s", exc)

        # Malformed input → unhandled 500 / debug disclosure on write endpoints.
        try:
            findings.extend(malformed_input_probe(model, base_url, auth=auth))
        except Exception as exc:
            logger.error("malformed_input probe failed: %s", exc)

        # Path traversal / local file read via a path param (proven by an OS-file signature).
        try:
            findings.extend(path_traversal_probe(model, base_url, auth=auth))
        except Exception as exc:
            logger.error("path_traversal probe failed: %s", exc)

        # BOLA — two-user control: can user B read user A's object by its key?
        try:
            findings.extend(bola_probe(model, base_url, auth=auth))
        except Exception as exc:
            logger.error("bola probe failed: %s", exc)

        # Excessive data exposure — credentials/secrets returned in a response body.
        try:
            findings.extend(excessive_data_probe(model, base_url, auth=auth))
        except Exception as exc:
            logger.error("excessive_data probe failed: %s", exc)

        # Crash-fuzz — oversized inputs on auth endpoints. Only when there is a real
        # API surface (an empty/UI-only model would fuzz a guessed path and misread
        # a static server's 501 as a crash).
        if getattr(model, "endpoints", None):
            try:
                findings.extend(crash_fuzz_probe(model, base_url))
            except Exception as exc:
                logger.error("crash_fuzz probe failed: %s", exc)

        # Error-handling — unknown routes should 404, not 5xx (control: a nonsense path).
        try:
            findings.extend(error_handling_probe(model, base_url))
        except Exception as exc:
            logger.error("error_handling probe failed: %s", exc)

        # Cookie flags — a login cookie missing HttpOnly/Secure (no-ops on bearer APIs).
        try:
            findings.extend(cookie_flags_probe(model, base_url))
        except Exception as exc:
            logger.error("cookie_flags probe failed: %s", exc)

        # Debug disclosure — an interactive debugger / stack trace on an error.
        try:
            findings.extend(debug_disclosure_probe(model, base_url))
        except Exception as exc:
            logger.error("debug_disclosure probe failed: %s", exc)

        # CORS — does the app reflect an arbitrary Origin (trusts any site)?
        try:
            findings.extend(cors_probe(model, base_url, auth=auth))
        except Exception as exc:
            logger.error("cors probe failed: %s", exc)

        # Response security headers (HSTS / clickjacking / MIME-sniff / CSP).
        try:
            findings.extend(security_headers_probe(model, base_url))
        except Exception as exc:
            logger.error("security_headers probe failed: %s", exc)

        result = ProbeResult(findings=findings)
        prog.emit("probe", "ok", result.summary())
        return result
