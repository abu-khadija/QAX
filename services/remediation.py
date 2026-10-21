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
    "host-header-injection": {
        "why": "The app builds URLs (reset links, redirects, cache keys) from the incoming "
               "Host header, so an attacker can poison password-reset emails and the web cache.",
        "fix": "Never trust the Host/X-Forwarded-Host header for URL generation. Use a "
               "configured canonical base URL, and validate Host against an allowlist.",
    },
    "business-logic": {
        "why": "A negative quantity/amount is accepted and stored, so an attacker can "
               "order -N items or set a negative price/total to abuse refunds, credits, or "
               "inventory.",
        "fix": "Validate business invariants server-side: reject non-positive quantities "
               "and amounts (and other out-of-range values) before persisting; never trust "
               "client-supplied totals.",
    },
    "file-upload": {
        "why": "The app stores an uploaded file under an attacker-chosen name/extension and "
               "serves it back executable (HTML/JS in-origin), giving stored XSS and often RCE.",
        "fix": "Allowlist content types/extensions, store uploads off the web root or on a "
               "separate origin, assign server-generated names, and serve with a safe "
               "Content-Type + Content-Disposition: attachment (never text/html).",
    },
    "graphql-introspection": {
        "why": "GraphQL introspection is enabled, so anyone can download the full schema — "
               "every type, field, and mutation — which hands an attacker the whole API map.",
        "fix": "Disable introspection in production (e.g. validation rule / "
               "introspection: false), and require auth on the GraphQL endpoint.",
    },
    "xxe": {
        "why": "The XML parser resolves external entities, so an attacker's document can "
               "read local files and reach internal services (SSRF).",
        "fix": "Disable DTDs and external entities in the parser (e.g. defusedxml, or set "
               "FEATURE_SECURE_PROCESSING / disallow-doctype-decl). Never parse untrusted XML "
               "with entity resolution enabled.",
    },
    "ssti": {
        "why": "User input is concatenated into a server-side template and evaluated, so "
               "an attacker can run template/engine code — typically a path to RCE.",
        "fix": "Never build templates from user input. Render a fixed template and pass "
               "user data as bound variables/context only; if dynamic templates are truly "
               "needed, use a sandboxed engine and a strict allowlist.",
    },
    "bfla": {
        "why": "A privileged, admin-namespaced endpoint enforces no role/function check, "
               "so any authenticated ordinary user can invoke admin functions (OWASP API5).",
        "fix": "Enforce function-level authorization on every privileged route in one place "
               "(middleware/decorator): check the caller's role/claim before the handler runs; "
               "deny by default and allow only the intended roles.",
    },
    "forged-token-replay": {
        "why": "Because the JWT signature is not verified, a forged token is accepted on "
               "every protected endpoint — one forgery grants access across the whole "
               "authenticated surface, not just where it was first proven.",
        "fix": "Fix the root cause once, centrally: verify the signature and expiry and pin "
               "the algorithm in the auth middleware so EVERY route enforces it — "
               "jwt.decode(token, key, algorithms=['HS256']); never allow 'none'.",
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
    "boolean-false": {
        "why": "A checkbox/flag that can be turned ON but not OFF: the framework treats a non-"
               "nullable boolean as implicitly 'required', so `false` is rejected (or crashes). The "
               "setting becomes permanent and every other edit in the same save is lost.",
        "fix": "Make the boolean optional/defaulted or suppress the implicit-required rule for value "
               "types (e.g. a nullable bool, or `[JsonRequired]`/`Required` off), and bind `false` "
               "explicitly. Check sibling boolean fields — the same binding rule applies.",
    },
    "single-element-array": {
        "why": "A multi-select that works with two values fails with exactly one: the single value "
               "arrives as a scalar and is not bound to the collection. Users hit it the first time "
               "they pick only one option.",
        "fix": "Bind the field as a collection regardless of how many values arrive (accept a "
               "scalar and wrap it, or enforce an array in the client). Add a one-value regression "
               "test for every multi-select.",
    },
    "date-order": {
        "why": "An end date earlier than its start date is accepted and stored, producing "
               "nonsensical intervals that break scheduling, reporting and any duration maths.",
        "fix": "Validate server-side that end >= start (return 422 with a clear message) — do not "
               "rely on the date-picker UI. Apply the same rule to every start/end pair.",
    },
    "long-input": {
        "why": "A very long value either crashes the server (unhandled overflow, a 5xx) or is "
               "silently cut while the app reports success — the user loses text without knowing.",
        "fix": "Enforce a documented max length (reject with a 4xx and a clear message, or truncate "
               "WITH a visible notice). Align the DB column size, the API validation and the UI.",
    },
    "duplicate-rows": {
        "why": "The same record appears more than once in a collection (usually a join fan-out), "
               "so lists, counts and 'assigned to me' views are wrong and users act on duplicates.",
        "fix": "De-duplicate at the query (DISTINCT / group by the entity id, or an EXISTS "
               "sub-query instead of a join) and add a test that a record with N related rows is "
               "listed once.",
    },
    "access-revocation": {
        "why": "Revoking a user's access does not actually remove it — the app deletes the "
               "membership row but the user still reaches the resource (cached/stale permission). "
               "A removed collaborator or ex-employee keeps reading private data.",
        "fix": "On revoke, invalidate every derived permission: drop cached ACLs, revoke the "
               "user's sessions/tokens for that resource, and re-check ownership on each request "
               "rather than trusting a prior grant.",
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
    "xss": {
        "why": "User input is reflected into the page without encoding, so an attacker can "
               "run script in victims' browsers — session theft, account takeover, and "
               "actions performed as the victim.",
        "fix": "Context-aware output encoding (HTML/attribute/JS) on every reflected value, "
               "a strict Content-Security-Policy, and a template engine that auto-escapes; "
               "never build HTML by string concatenation of raw input.",
    },
    "stored-xss": {
        "why": "User input is stored and later rendered to OTHER users without encoding, so "
               "injected script runs in every viewer's browser — the most damaging XSS "
               "(mass session theft, wormable account takeover).",
        "fix": "HTML-encode stored values on output (context-aware), add a strict "
               "Content-Security-Policy, and sanitise rich text server-side with a vetted "
               "allow-list library; never mark stored input as safe/raw HTML.",
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


# ── QA bug-report fields (compact format) ─────────────────────────────────────
# Per probe: `how` — the condition the reproduction applies (completes "Send
# <METHOD> <path> …"); `expected` — the Expected Result stated as a MUST rule.
# Additive to _MAP: remediation_for() is unchanged.
_QA = {
    "forge-jwt": ("with a JWT signed by the guessed/hardcoded secret instead of a real login token",
                  "The server MUST reject any token not signed with its private, high-entropy secret (401)."),
    "jwt-flaws": ("with a token whose alg is 'none' or whose signature is altered",
                  "The server MUST verify the signature with a pinned algorithm and reject unsigned/tampered tokens (401)."),
    "forged-token-replay": ("replaying a forged token against sibling protected endpoints",
                            "Every protected endpoint MUST reject a forged token (401), not only the one first tested."),
    "host-header-injection": ("with a forged Host / X-Forwarded-Host header (e.g. Host: attacker.example)",
                              "The server MUST build absolute URLs from its configured base URL, never from the request's Host header."),
    "business-logic": ("with a non-positive quantity/amount (e.g. -1 or 0)",
                       "The server MUST reject business-invalid values (422) and never store them."),
    "file-upload": ("uploading a file with an executable/attacker-chosen name or extension",
                    "Uploads MUST be stored under a server-generated name with an allow-listed type, outside any executable path."),
    "graphql-introspection": ("with a GraphQL introspection query (__schema)",
                              "Introspection MUST be disabled in production (or require an admin identity)."),
    "xxe": ("with an XML body declaring an external entity (file:///etc/passwd)",
            "The XML parser MUST disable DTDs/external entities; the response MUST NOT contain resolved entity content."),
    "ssti": ("with a template expression (e.g. {{7*7}}) in a text field",
             "User input MUST be rendered as data — the response MUST contain the literal expression, never its evaluated result."),
    "bfla": ("as an ORDINARY (non-admin) user, against an admin-namespaced function",
             "An admin function MUST return 403 to a non-admin user."),
    "boolean-false": ("with a boolean field set to true, then the identical request with it set to false",
                      "A boolean MUST accept both true and false; unticking a flag MUST save."),
    "single-element-array": ("with a multi-value field holding 2 items, then the identical request with exactly 1",
                             "A multi-value field MUST accept any number of items within its limits, including one."),
    "date-order": ("with an ordered date pair, then with the end date BEFORE the start date",
                   "The server MUST reject an end date earlier than the start date (422)."),
    "long-input": ("with a short value, then a 5,000-character value in the same free-text field",
                   "Oversized input MUST be rejected with a clean 4xx or stored in full — never a 5xx and never silently cut."),
    "duplicate-rows": ("and compare the ids in the returned list",
                       "Every record MUST appear exactly once in a collection response."),
    "access-revocation": ("as user B whose access user A just REVOKED, re-requesting the resource",
                          "After access is revoked, the user MUST be blocked (403/404) — the prior grant must not linger."),
    "bola": ("as user B, reading/modifying an object created by user A",
             "A user MUST NOT read or modify another user's object (403/404)."),
    "sqli": ("with SQL metacharacters / a boolean or time-based payload in a parameter",
             "Parameters MUST be bound (parameterised queries); behaviour MUST NOT change with injected SQL."),
    "nosql": ("with a NoSQL operator object (e.g. {\"$ne\": null}) in place of a value",
              "Operators in user input MUST be rejected or treated as literals."),
    "excessive-data": ("as an ordinary user and inspect the response fields",
                       "Responses MUST return only the fields the caller is entitled to (no secrets, hashes or other users' data)."),
    "malformed-input": ("with a malformed / wrongly-typed body",
                        "Malformed input MUST return a handled 4xx, never an unhandled 5xx or a stack trace."),
    "debug-disclosure": ("triggering an error",
                         "Production errors MUST NOT expose stack traces, debuggers or internal paths."),
    "input-robustness": ("with boundary / unusual input",
                         "Unusual input MUST be validated and answered with a handled 4xx."),
    "error-handling": ("triggering an error path",
                       "Errors MUST return a handled status with a safe, generic message."),
    "cookie-flags": ("and inspect the Set-Cookie headers",
                     "Session cookies MUST carry Secure, HttpOnly and SameSite."),
    "path-traversal": ("with ../ sequences in a path/file parameter",
                       "File access MUST be confined to the intended directory; traversal MUST be rejected."),
    "cors": ("with an Origin header of an untrusted site",
             "CORS MUST NOT reflect arbitrary origins together with credentials."),
    "security-headers": ("and inspect the response headers",
                         "Every HTML response MUST set the missing security header(s) listed under Actual Result."),
    "mass-assignment": ("with extra privileged fields in the body (e.g. is_admin, role)",
                        "The server MUST ignore or reject fields the caller may not set."),
    "open-redirect": ("with a redirect parameter pointing to an external site",
                      "Redirects MUST be limited to an allow-list of internal destinations."),
    "xss": ("with an HTML/script payload in a reflected parameter",
            "User input MUST be output-encoded; the payload MUST NOT appear unescaped in the response."),
    "stored-xss": ("storing an HTML/script payload, then reading it back",
                   "Stored user input MUST be output-encoded when rendered."),
    "ssrf": ("with a URL parameter pointing at an internal/callback address",
             "The server MUST NOT fetch arbitrary user-supplied URLs (allow-list destinations)."),
    "command-injection": ("with shell metacharacters in a parameter",
                          "User input MUST never reach a shell; behaviour MUST NOT change with injected commands."),
    "rate-limit": ("repeating the request rapidly",
                   "Sensitive endpoints MUST throttle repeated requests (429)."),
    "persistence": ("writing a value, then reading it back",
                    "A successful write MUST persist exactly what was accepted."),
    "auth-error": ("with missing/invalid credentials",
                   "Authentication errors MUST return 401 with a generic message."),
    "search-robustness": ("with ordinary special characters in the search input",
                          "Search MUST handle ordinary characters with a normal result set, never a 405/5xx."),
    "unpaginated-list": ("and count the returned items",
                         "List endpoints MUST paginate (bounded page size)."),
}



def qa_for(finding: dict) -> dict:
    """
    QA bug-report fields for a finding: {'how','expected','data_safety'} — or {}
    when the probe has no entry. data_safety follows the owner's non-negotiable #7.
    """
    if not isinstance(finding, dict):
        return {}
    probe = str(finding.get("probe", "")).lower()
    if probe not in _QA:
        return {}
    how, expected = _QA[probe]
    try:
        from execution.probes import MUTATING_PROBES   # single source of truth
    except Exception:
        MUTATING_PROBES = frozenset()
    safety = ("Reproduced with disposable records QAX created for this scan and cleaned up "
              "afterwards; no existing records were modified." if probe in MUTATING_PROBES else
              "Read-only reproduction — no records were created or modified.")
    return {"how": how, "expected": expected, "data_safety": safety}
