"""
services/remediation.py — actionable fix guidance per finding
=============================================================
Turns a finding into "why it matters" + "how to fix it". This is what makes a
report worth paying for — not just "here's a bug" but "here's exactly what to do".
Keyed by the deterministic probe id (or the finding's source/title), so it's stable
and needs no model call. Returns {} when there's no specific guidance.
"""

from __future__ import annotations

_MAP = {
    "forge-jwt": {
        "why": "The JWT signing secret is weak or hardcoded, so anyone who reads the "
               "source can forge a valid token and impersonate any user.",
        "fix": "Load a long random secret from an environment variable / secrets manager "
               "(never commit it), rotate it, and invalidate old tokens.",
    },
    "jwt-flaws": {
        "why": "The server accepts tampered tokens (alg=none or an unverified signature), "
               "so an attacker can mint a valid session without the secret.",
        "fix": "Verify the signature and expiry, and PIN the algorithm — jwt.decode(token, "
               "key, algorithms=['HS256']); never allow 'none'. Use a vetted JWT library.",
    },
    "sqli": {
        "why": "User input reaches a raw SQL query, allowing data theft, auth bypass, or "
               "data destruction.",
        "fix": "Use parameterized queries / ORM bindings (e.g. WHERE x = ?) — never build "
               "SQL by string concatenation or f-strings with user input.",
    },
    "nosql": {
        "why": "A request field is used as a document query, so an operator object "
               "(e.g. {\"$ne\": null}) can bypass filters and read other records.",
        "fix": "Validate/cast every field to its expected scalar type before querying; "
               "reject dict/operator values; enforce a request schema.",
    },
    "bola": {
        "why": "Objects are returned by id/key with no ownership check, so any logged-in "
               "user can read or change another user's data (IDOR/BOLA).",
        "fix": "On every object access, verify it belongs to the current user "
               "(resource.owner_id == current_user.id) and deny by default (404/403).",
    },
    "excessive-data": {
        "why": "The response returns credentials/secrets (e.g. password hashes) that "
               "should never leave the server.",
        "fix": "Serialize with an explicit output schema / DTO that whitelists safe fields; "
               "never return password, secret, token, or internal fields.",
    },
    "malformed-input": {
        "why": "A type-violating value is parsed without a guard, crashing the request "
               "(HTTP 500) — and with debug mode on, the error page leaks source, stack "
               "frames, and an interactive console (remote code execution risk).",
        "fix": "Validate and parse inputs defensively (try/except → 400/422), and TURN OFF "
               "debug mode in any deployed environment (generic error pages only).",
    },
    "debug-disclosure": {
        "why": "An error exposes an interactive debugger / stack trace, leaking source and "
               "internals — the console can lead to remote code execution.",
        "fix": "Disable debug mode in deployed environments and return generic error "
               "responses; log details server-side only.",
    },
    "input-robustness": {
        "why": "Malformed input triggers an unhandled server error instead of a clean "
               "rejection — fragile, and often the first step toward a deeper bug.",
        "fix": "Validate input and reject bad values with a 4xx; wrap parsers in try/except.",
    },
    "error-handling": {
        "why": "Unknown routes return a server error (5xx) instead of 404, which pollutes "
               "monitoring and hides real faults.",
        "fix": "Add proper 404/405 handlers so unknown paths and methods return a clean 4xx.",
    },
    "cookie-flags": {
        "why": "A session cookie is missing HttpOnly/Secure, so it can be stolen via XSS "
               "or leaked over plain HTTP.",
        "fix": "Set HttpOnly, Secure, and SameSite on session cookies.",
    },
    "path-traversal": {
        "why": "A path parameter is used to build a filesystem path without sanitisation, "
               "so an attacker can read files outside the web root (e.g. /etc/passwd, "
               "secrets, source) — and sometimes write them.",
        "fix": "Never pass user input to file paths directly: map it to an allow-list of "
               "known names/ids, reject '..' and absolute paths, and resolve+confirm the "
               "final path stays under the intended base directory.",
    },
    "cors": {
        "why": "The app reflects any caller's Origin in Access-Control-Allow-Origin, so a "
               "malicious website can make cross-origin requests to this API — and with "
               "credentials enabled, read the victim's authenticated responses.",
        "fix": "Allow only an explicit allow-list of trusted origins; never reflect the "
               "request Origin, and never combine a wildcard/reflected origin with "
               "Access-Control-Allow-Credentials: true.",
    },
    "security-headers": {
        "why": "Standard response security headers are missing, removing defence-in-depth "
               "against clickjacking, MIME-sniffing, protocol downgrade and XSS.",
        "fix": "Set Strict-Transport-Security (HTTPS), X-Content-Type-Options: nosniff, "
               "X-Frame-Options/CSP frame-ancestors, and a Content-Security-Policy — e.g. "
               "via a security-headers middleware.",
    },
    "dep-cve": {
        "why": "A dependency is pinned to a version with a known, published vulnerability "
               "(CVE/GHSA), so the app inherits that flaw regardless of its own code.",
        "fix": "Bump the package to the patched release named in the advisory, re-run the "
               "dependency scan to confirm it clears, and keep deps updated (Dependabot/renovate).",
    },
    "dep-license": {
        "why": "A dependency carries a copyleft or non-commercial license, which can force "
               "source disclosure or forbid commercial use of the product that ships it.",
        "fix": "Review the license against your distribution model; replace the package with a "
               "permissively-licensed alternative, or obtain a commercial license, before shipping.",
    },
    "mass-assignment": {
        "why": "The endpoint binds the whole request body to the model, so a client can set "
               "privileged fields it should never control (admin, role, balance) — "
               "privilege escalation.",
        "fix": "Bind only an explicit allow-list of fields (a DTO/input schema); never auto-map "
               "the raw request body, and set privileged fields server-side only.",
    },
    "open-redirect": {
        "why": "A redirect target is taken from a request parameter without validation, so an "
               "attacker can send victims to a site they control (phishing, OAuth token theft).",
        "fix": "Redirect only to an allow-list of known internal paths/hosts; reject absolute "
               "external URLs, or map a redirect key to a server-side URL table.",
    },
    "ssrf": {
        "why": "The server fetches a URL supplied by the client, so an attacker can make "
               "it reach internal-only services, cloud metadata endpoints (credentials), "
               "or arbitrary hosts — a pivot into the internal network.",
        "fix": "Validate the URL against an allow-list of hosts/schemes, resolve and block "
               "private/link-local IP ranges (incl. redirects), disable unused URL schemes "
               "(file/gopher), and send outbound fetches through an egress proxy.",
    },
    "command-injection": {
        "why": "User input reaches an OS shell, so an attacker can run arbitrary commands "
               "on the server — full remote code execution and host compromise.",
        "fix": "Never pass user input to a shell. Call the program directly with an argument "
               "array (no shell), validate against a strict allow-list, and drop shell "
               "metacharacters; avoid system()/exec with a composed string.",
    },
    "rate-limit": {
        "why": "The login endpoint has no throttling or lockout, so credentials can be "
               "brute-forced or sprayed at speed.",
        "fix": "Add rate limiting and progressive lockout per account/IP (e.g. 429 after N "
               "failures), plus CAPTCHA/MFA on repeated failures; alert on spikes.",
    },
    "persistence": {
        "why": "A field is accepted with a 2xx success response but is blank when the record is "
               "read back — silent data loss the user never sees (commonly a nested/dotted or "
               "optional field that fails to bind on the server).",
        "fix": "Bind and validate every field server-side; return an error when a value cannot be "
               "saved instead of a success. Add a persistence test that writes, reloads and "
               "re-reads each field rather than trusting the save response.",
    },
    "auth-error": {
        "why": "A forbidden request crashes with a 5xx instead of a clean 401/403, leaking "
               "internals and making real faults indistinguishable from unauthorized traffic.",
        "fix": "Handle authentication/authorization failures explicitly: return 401 (not "
               "authenticated) or 403 (not permitted) before any work that can throw, and never "
               "let the auth layer fall through to an unhandled 500.",
    },
    "search-robustness": {
        "why": "A search/filter input returns a raw 405/5xx on an ordinary character, so real "
               "queries (emails, slash-separated terms) fail — an unsanitised query-operator "
               "syntax is rejecting input it should escape.",
        "fix": "Escape or parameterise user input before it reaches the search/query engine, and "
               "return 0 results (not an error) for a term that matches nothing.",
    },
    "unpaginated-list": {
        "why": "A collection endpoint returns its whole table in one response and ignores a limit "
               "parameter — the payload and client DOM grow without bound as data accumulates, a "
               "performance and denial-of-service risk.",
        "fix": "Paginate every collection endpoint (limit/offset or cursor) with a sane default "
               "and a hard maximum page size; never return an unbounded array.",
    },
}

_SOURCE = {
    "ui-console": {
        "why": "The page throws JavaScript errors in the browser — broken functionality "
               "users will hit.",
        "fix": "Open the browser console on that screen, fix the uncaught error, and add a "
               "regression test for the flow.",
    },
    "ui-network": {
        "why": "A request the page makes fails (4xx/5xx), so a feature is silently broken.",
        "fix": "Check the failing endpoint; fix the server error or the client call, and "
               "handle the failure in the UI.",
    },
    "visual-regression": {
        "why": "A screen's rendering changed from the baseline — possibly an unintended UI "
               "regression.",
        "fix": "Open the diff; if the change is unintended, fix it; if intended, re-baseline.",
    },
    "auth-bypass": {
        "why": "A protected endpoint can be reached without authentication.",
        "fix": "Require a valid session/token on the endpoint and deny unauthenticated requests (401).",
    },
    "validation": {
        "why": "The endpoint accepts an empty/invalid value for a required field.",
        "fix": "Enforce field validation (required + non-empty / min length) and reject with a 422.",
    },
}


def remediation_for(finding: dict) -> dict:
    """Return {'why','fix'} for a finding, or {} when there's no specific guidance."""
    if not isinstance(finding, dict):
        return {}
    probe = str(finding.get("probe", "")).lower()
    src = str(finding.get("source", "")).lower()
    title = str(finding.get("title", "")).lower()

    if probe in _MAP:
        return _MAP[probe]
    if "debugger" in title or "stack trace" in title or "debug mode" in title:
        return _MAP["debug-disclosure"]
    if src == "ui-tour":
        return _SOURCE["ui-network"] if any(k in title for k in ("request", "server error", "client error", "network")) \
            else _SOURCE["ui-console"]
    if src == "visual-regression":
        return _SOURCE["visual-regression"]
    if "requires authentication" in title:
        return _SOURCE["auth-bypass"]
    if "rejects empty" in title or "validation" in title:
        return _SOURCE["validation"]
    if "object-level authorization" in title or "idor" in title or "blocks another user" in title:
        return _MAP["bola"]
    return {}
