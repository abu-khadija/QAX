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

# Words that mark an endpoint as worth probing first (account/recovery flows, files, URLs, search,
# free text, money). Used only to ORDER candidates — nothing is excluded.
_PRIORITY_HINTS = ("reset", "forgot", "password", "login", "auth", "token", "admin", "upload", "import",
                   "export", "file", "download", "url", "redirect", "fetch", "search", "query", "comment",
                   "message", "lookup", "user", "account", "order", "payment", "billing", "invoice")


def _probe_priority(e) -> int:
    """Security relevance of one endpoint: writes, auth, a hinting name, and user-controlled inputs."""
    if not isinstance(e, dict):
        return 0
    path = str(e.get("path", "")).lower()
    return ((2 if str(e.get("method", "")).upper() in ("POST", "PUT", "PATCH", "DELETE") else 0)
            + (1 if e.get("auth_required") else 0)
            + (2 if any(h in path for h in _PRIORITY_HINTS) else 0)
            + (1 if (e.get("query_params") or e.get("request_fields") or "{" in path) else 0))


def risk_ordered(model: AppModel) -> AppModel:
    """
    A copy of `model` whose endpoints are ordered most-security-relevant first (stable, so ties keep
    spec order). Every probe inspects only the first N candidates of its kind; with the spec's
    alphabetical order a large app's reset-link / upload / lookup endpoints fell outside those windows
    and were never probed. The caller's model is not mutated.
    """
    import dataclasses
    eps = getattr(model, "endpoints", None)
    if not isinstance(eps, list) or len(eps) < 2:
        return model
    try:
        return dataclasses.replace(model, endpoints=sorted(eps, key=_probe_priority, reverse=True))
    except Exception:
        return model


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
        cleanup: bool = True,
        progress: ProgressReporter | None = None,
        read_only: bool = False,
    ) -> ProbeResult:
        """
        Run the probes relevant to this app against the live target.

        `read_only=True` (PLAN_TO_V1 D9): the shared session refuses every write request
        (core.read_only.guard) — nothing but GET/HEAD/OPTIONS leaves QAX, and each probe that
        needed a write is listed in the not-tested ledger as kind "read-only".

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
        model = risk_ordered(model)
        if not base_url or not str(base_url).strip():
            return ProbeResult(error="No base_url to probe against.")

        from execution.probes import (_resolve_token, auth_error_probe, bola_probe,
                                       command_injection_probe, cookie_flags_probe, cors_probe,
                                       crash_fuzz_probe, debug_disclosure_probe, error_handling_probe,
                                       excessive_data_probe, forge_jwt_probe, injection_probe,
                                       jwt_flaws_probe, malformed_input_probe, mass_assignment_probe,
                                       nosql_probe, open_redirect_probe, path_traversal_probe,
                                       persistence_probe, rate_limit_probe, search_robustness_probe,
                                       security_headers_probe, sqli_probe, unpaginated_list_probe)

        from execution.probes import _skip
        prog.emit("probe", "start", "running active probes")
        findings = []
        # One shared HTTP session for all probes: reuses the connection pool, and carries
        # the registry of disposable records QAX creates so we can clean them up after.
        import requests
        import uuid
        shared = requests.Session()
        shared._qax_created = []  # probes append every test record they write here
        shared._qax_skips = []    # probes record WHY they could not run (not-tested ledger)
        if auth and (auth.get("cookie") or auth.get("headers")):
            from core.session_auth import apply_extras
            apply_extras(shared, auth)   # cookie session / CSRF nonce / static headers: every probe request carries them
        if read_only:
            from core.read_only import guard
            guard(shared)
        else:
            from execution.probes import track_writes
            track_writes(shared)      # every accepted creation is registered → cleanup + LEFT ON THE TARGET
        from core.throttle import guard as _throttle_guard
        _throttle_guard(shared)       # 429 -> wait Retry-After, retry, pace; past the budget: stop and say so

        # Resolve ONE shared "utility" user for the read-only probes that only need a
        # valid login (sqli, injection, path-traversal, ...), so they don't each
        # self-register. Probes that test the registration flow itself or need isolated
        # users (BOLA's two users, mass-assignment, rate-limit, ...) still get `auth`
        # unchanged and create their own. Only self-registering apps need this; when the
        # caller supplied a token we pass it straight through.
        read_auth = auth
        if not (auth or {}).get("token"):
            try:
                from core.auth_recipe import derive_auth_recipe
                util = _resolve_token(shared, base_url.rstrip("/"), derive_auth_recipe(model),
                                      model, f"qax_util_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
                if util:
                    read_auth = {**(auth or {}), "token": util}
            except Exception as exc:
                logger.error("utility token resolve failed: %s", exc)

        # Forge-JWT — only when the model found a hardcoded secret to forge with.
        if not model.exposed_secrets:
            _skip(shared, "forge-jwt", "no hardcoded JWT signing secret was found in the modelled source "
                                       "(a live-only scan has no source to read)", kind="not-applicable")
        if model.exposed_secrets:
            try:
                f = forge_jwt_probe(model, base_url, session=shared)
                if f is not None:
                    findings.append(f)
            except Exception as exc:
                logger.error("forge_jwt probe failed: %s", exc)
                _skip(shared, "forge-jwt", f"the probe crashed: {exc}", kind="error")

        # JWT validation flaws (alg=none, signature not verified) — needs a token.
        try:
            findings.extend(jwt_flaws_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("jwt_flaws probe failed: %s", exc)
            _skip(shared, "jwt-flaws", f"the probe crashed: {exc}", kind="error")

        # Forged-token replay: once a token is forgeable, sweep EVERY other protected
        # endpoint with a forged token (a flaw proven on one endpoint unlocks them all).
        try:
            from execution.probes import forged_token_replay_probe
            findings.extend(forged_token_replay_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("forged_token_replay probe failed: %s", exc)
            _skip(shared, "forged-token-replay", f"the probe crashed: {exc}", kind="error")

        # BFLA (OWASP API5) — an ordinary user reaching an admin-namespaced function.
        try:
            from execution.probes import bfla_probe
            findings.extend(bfla_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("bfla probe failed: %s", exc)
            _skip(shared, "bfla", f"the probe crashed: {exc}", kind="error")

        # SQLi (path params) — error-based, with a benign-vs-quote control.
        try:
            findings.extend(sqli_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("sqli probe failed: %s", exc)
            _skip(shared, "sqli", f"the probe crashed: {exc}", kind="error")

        # Injection via query params and request bodies (same control).
        try:
            findings.extend(injection_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("injection probe failed: %s", exc)
            _skip(shared, "injection", f"the probe crashed: {exc}", kind="error")

        # NoSQL (operator) injection on write endpoints whose body feeds a query.
        try:
            findings.extend(nosql_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("nosql probe failed: %s", exc)
            _skip(shared, "nosql", f"the probe crashed: {exc}", kind="error")

        # Malformed input → unhandled 500 / debug disclosure on write endpoints.
        try:
            findings.extend(malformed_input_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("malformed_input probe failed: %s", exc)
            _skip(shared, "malformed-input", f"the probe crashed: {exc}", kind="error")

        # Path traversal / local file read via a path param (proven by an OS-file signature).
        try:
            findings.extend(path_traversal_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("path_traversal probe failed: %s", exc)
            _skip(shared, "path-traversal", f"the probe crashed: {exc}", kind="error")

        # Command injection — a param that feeds a shell command (proven by echo/sleep).
        try:
            findings.extend(command_injection_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("command_injection probe failed: %s", exc)
            _skip(shared, "command-injection", f"the probe crashed: {exc}", kind="error")

        # SSRF — a URL param the server fetches (proven by an out-of-band callback).
        try:
            from execution.probes import ssrf_probe
            findings.extend(ssrf_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("ssrf probe failed: %s", exc)
            _skip(shared, "ssrf", f"the probe crashed: {exc}", kind="error")

        # Reflected XSS — markup reflected unencoded into an HTML response.
        try:
            from execution.probes import xss_probe
            findings.extend(xss_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("xss probe failed: %s", exc)
            _skip(shared, "xss", f"the probe crashed: {exc}", kind="error")

        # SSTI — a template expression in a query param evaluated server-side (RCE-class).
        try:
            from execution.probes import ssti_probe
            findings.extend(ssti_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("ssti probe failed: %s", exc)
            _skip(shared, "ssti", f"the probe crashed: {exc}", kind="error")

        # Host-header injection — forged Host used to build a URL (reset/cache poisoning).
        try:
            from execution.probes import host_header_injection_probe
            findings.extend(host_header_injection_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("host_header_injection probe failed: %s", exc)
            _skip(shared, "host-header-injection", f"the probe crashed: {exc}", kind="error")

        # XXE — external-entity file read on an XML endpoint (proven by /etc/passwd).
        try:
            from execution.probes import xxe_probe
            findings.extend(xxe_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("xxe probe failed: %s", exc)
            _skip(shared, "xxe", f"the probe crashed: {exc}", kind="error")

        # GraphQL introspection left enabled — full schema/attack-surface disclosure.
        try:
            from execution.probes import graphql_introspection_probe
            findings.extend(graphql_introspection_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("graphql_introspection probe failed: %s", exc)
            _skip(shared, "graphql-introspection", f"the probe crashed: {exc}", kind="error")

        # Unrestricted file upload — upload HTML, retrieve it, prove it serves as HTML.
        try:
            from execution.probes import file_upload_probe
            findings.extend(file_upload_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("file_upload probe failed: %s", exc)
            _skip(shared, "file-upload", f"the probe crashed: {exc}", kind="error")

        # Business logic — a negative quantity/amount accepted and stored on a create.
        try:
            from execution.probes import business_logic_probe
            findings.extend(business_logic_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("business_logic probe failed: %s", exc)
            _skip(shared, "business-logic", f"the probe crashed: {exc}", kind="error")

        # Stored XSS — markup written then rendered unencoded on read-back (P1).
        try:
            from execution.probes import stored_xss_probe
            findings.extend(stored_xss_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("stored_xss probe failed: %s", exc)
            _skip(shared, "stored-xss", f"the probe crashed: {exc}", kind="error")

        # Mass assignment — registration binds a client-supplied privileged field.
        try:
            findings.extend(mass_assignment_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("mass_assignment probe failed: %s", exc)
            _skip(shared, "mass-assignment", f"the probe crashed: {exc}", kind="error")

        # Open redirect — a redirect param bounces to an attacker-controlled host.
        try:
            findings.extend(open_redirect_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("open_redirect probe failed: %s", exc)
            _skip(shared, "open-redirect", f"the probe crashed: {exc}", kind="error")

        # Rate limiting — no brute-force throttling/lockout on login (P3 lead).
        try:
            findings.extend(rate_limit_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("rate_limit probe failed: %s", exc)
            _skip(shared, "rate-limit", f"the probe crashed: {exc}", kind="error")

        # BOLA — two-user control: can user B read user A's object by its key?
        try:
            findings.extend(bola_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("bola probe failed: %s", exc)
            _skip(shared, "bola", f"the probe crashed: {exc}", kind="error")

        # Access revocation — two-user control: after A revokes B, is B still in?
        try:
            from execution.probes import access_revocation_probe
            findings.extend(access_revocation_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("access_revocation probe failed: %s", exc)
            _skip(shared, "access-revocation", f"the probe crashed: {exc}", kind="error")

        # Spec-typed probes: boolean false, single-element array, date order,
        # very long text, duplicate rows. Each is control-gated and says why if skipped.
        try:
            from execution.typed_probes import (boolean_false_probe, date_order_probe,
                                                duplicate_rows_probe, long_input_probe,
                                                single_element_array_probe)
            for _typed in (boolean_false_probe, single_element_array_probe, date_order_probe,
                           long_input_probe, duplicate_rows_probe):
                try:
                    findings.extend(_typed(model, base_url, session=shared, auth=read_auth))
                except Exception as exc:
                    logger.error("%s failed: %s", _typed.__name__, exc)
                    _skip(shared, _typed.__name__.replace("_probe", "").replace("_", "-"),
                          f"the probe crashed: {exc}", kind="error")
        except Exception as exc:
            logger.error("typed probes unavailable: %s", exc)

        # Excessive data exposure — credentials/secrets returned in a response body.
        try:
            findings.extend(excessive_data_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("excessive_data probe failed: %s", exc)
            _skip(shared, "excessive-data", f"the probe crashed: {exc}", kind="error")

        # Crash-fuzz — oversized inputs on auth endpoints. Only when there is a real
        # API surface (an empty/UI-only model would fuzz a guessed path and misread
        # a static server's 501 as a crash).
        if getattr(model, "endpoints", None):
            try:
                findings.extend(crash_fuzz_probe(model, base_url, session=shared))
            except Exception as exc:
                logger.error("crash_fuzz probe failed: %s", exc)
                _skip(shared, "crash-fuzz", f"the probe crashed: {exc}", kind="error")

        # Error-handling — unknown routes should 404, not 5xx (control: a nonsense path).
        try:
            findings.extend(error_handling_probe(model, base_url, session=shared))
        except Exception as exc:
            logger.error("error_handling probe failed: %s", exc)
            _skip(shared, "error-handling", f"the probe crashed: {exc}", kind="error")

        # Cookie flags — a login cookie missing HttpOnly/Secure (no-ops on bearer APIs).
        try:
            findings.extend(cookie_flags_probe(model, base_url, session=shared))
        except Exception as exc:
            logger.error("cookie_flags probe failed: %s", exc)
            _skip(shared, "cookie-flags", f"the probe crashed: {exc}", kind="error")

        # Debug disclosure — an interactive debugger / stack trace on an error.
        try:
            findings.extend(debug_disclosure_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("debug_disclosure probe failed: %s", exc)
            _skip(shared, "debug-disclosure", f"the probe crashed: {exc}", kind="error")

        # CORS — does the app reflect an arbitrary Origin (trusts any site)?
        try:
            findings.extend(cors_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("cors probe failed: %s", exc)
            _skip(shared, "cors", f"the probe crashed: {exc}", kind="error")

        # Response security headers (HSTS / clickjacking / MIME-sniff / CSP).
        try:
            findings.extend(security_headers_probe(model, base_url, session=shared))
        except Exception as exc:
            logger.error("security_headers probe failed: %s", exc)
            _skip(shared, "security-headers", f"the probe crashed: {exc}", kind="error")

        # Persistence — a field saved with a 2xx "success" that is blank on read-back
        # (silent data loss, the QA field manual's highest-value class).
        try:
            findings.extend(persistence_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("persistence probe failed: %s", exc)
            _skip(shared, "persistence", f"the probe crashed: {exc}", kind="error")

        # Auth error handling — a forbidden request that 5xx's instead of a clean 401/403.
        try:
            findings.extend(auth_error_probe(model, base_url, session=shared, auth=auth))
        except Exception as exc:
            logger.error("auth_error probe failed: %s", exc)
            _skip(shared, "auth-error", f"the probe crashed: {exc}", kind="error")

        # Search robustness — a search/filter param that 405/5xx's on an ordinary character.
        try:
            findings.extend(search_robustness_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("search_robustness probe failed: %s", exc)
            _skip(shared, "search-robustness", f"the probe crashed: {exc}", kind="error")

        # Unpaginated list — a collection that returns an unbounded array (no pagination).
        try:
            findings.extend(unpaginated_list_probe(model, base_url, session=shared, auth=read_auth))
        except Exception as exc:
            logger.error("unpaginated_list probe failed: %s", exc)
            _skip(shared, "unpaginated-list", f"the probe crashed: {exc}", kind="error")

        # Clean up the disposable records we created (best-effort, only our own data).
        created = len(getattr(shared, "_qax_created", []) or [])
        cleaned = 0
        from execution.probes import leftover_artifacts
        if cleanup and created:
            try:
                from execution.probes import cleanup_artifacts
                cleaned = cleanup_artifacts(model, base_url, shared)
            except Exception as exc:
                logger.error("probe cleanup failed: %s", exc)
            prog.emit("probe", "info", f"test-data cleanup: removed {cleaned}/{created} record(s)")

        skipped = list(getattr(shared, "_qax_skips", []) or [])
        from core.throttle import ledger_entries as _throttle_ledger, summary as _throttle_summary
        _ts = _throttle_summary(shared)
        if _ts:
            skipped = skipped + _throttle_ledger([_ts])
        if read_only:
            from core.read_only import blocked, ledger_entries
            skipped = ledger_entries(blocked(shared), skipped)
        leftover = leftover_artifacts(shared, "cleanup was disabled") if created else []
        result = ProbeResult(findings=findings, created_records=created, cleaned_records=cleaned,
                             skipped=skipped, leftover=leftover)
        prog.emit("probe", "ok", result.summary())
        return result
