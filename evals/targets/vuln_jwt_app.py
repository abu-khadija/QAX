"""
Golden regression target for QAX (LOCAL EVAL ONLY — never deploy).

A deliberately-vulnerable Flask API whose protected endpoints decode the JWT
WITHOUT verifying its signature, so a forged token is accepted everywhere. It is
the fixed scenario `evals/gate.py` scans on every change to prove QAX still:
  • proves the JWT signature flaw (jwt-flaws),
  • sweeps the forged token across the whole protected surface (forged-token-replay),
  • flags missing security headers,
and stays SILENT when the flaw is fixed (QAX_SECURE=1 — the no-false-positive control).

Endpoints: /me (control), /admin/users, /billing/invoices (protected, swept),
/health (public), /auth/register + /auth/login (register is required before login),
/openapi.json (so QAX grounds the exact surface regardless of source modelling).
"""
import base64
import hashlib
import hmac
import json
import os

from flask import Flask, jsonify, request

app = Flask(__name__)
SECRET = b"super-secret-signing-key"
SECURE = os.environ.get("QAX_SECURE") == "1"
_USERS: dict = {}


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_dec(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def _sign(header_b64: str, payload_b64: str) -> str:
    sig = hmac.new(SECRET, f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
    return _b64u(sig)


def _make_token(sub: str) -> str:
    h = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    p = _b64u(json.dumps({"sub": sub}).encode())
    return f"{h}.{p}.{_sign(h, p)}"


def _subject(token: str):
    """Return the token's subject. SECURE verifies the signature; vulnerable does NOT."""
    try:
        h, p, s = token.split(".")
    except ValueError:
        return None
    if SECURE and not hmac.compare_digest(s, _sign(h, p)):
        return None                      # correct behaviour: reject tampered tokens
    try:
        return json.loads(_b64u_dec(p)).get("sub")   # vulnerable: trusts the payload, any sig
    except Exception:
        return None


def _auth():
    tok = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    return _subject(tok) if tok else None


@app.post("/auth/register")
def register():
    body = request.get_json(silent=True) or {}
    _USERS[body.get("email", "")] = body.get("password", "")
    return jsonify({}), 201


@app.post("/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    email = body.get("email", "")
    if email not in _USERS:                 # must register first
        return jsonify({"error": "no such user"}), 401
    return jsonify({"access_token": _make_token(email)}), 200


@app.get("/me")
def me():
    sub = _auth()
    return (jsonify({"email": sub}), 200) if sub else (jsonify({"error": "unauthorized"}), 401)


@app.get("/admin/users")
def admin_users():
    sub = _auth()
    if not sub:
        return jsonify({"error": "unauthorized"}), 401
    # VULNERABLE: no function-level role check — any authenticated user reaches this
    # admin function (BFLA). SECURE mode enforces the admin role.
    if SECURE and "admin" not in str(sub).lower():
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"users": list(_USERS)}), 200


@app.get("/billing/invoices")
def invoices():
    sub = _auth()
    return (jsonify({"invoices": [{"id": 1, "amount": 4200}]}), 200) if sub else (jsonify({"error": "unauthorized"}), 401)


@app.get("/greet")
def greet():
    # VULNERABLE: the `name` query param is rendered through a template engine, so a
    # template expression is EVALUATED (simulated: {{a*b}} / ${a*b} / #{a*b} → product).
    # SECURE mode renders the name literally (no evaluation).
    import html as _html
    import re as _re
    name = request.args.get("name", "guest")
    if SECURE:
        name = _html.escape(name)            # encoded output: no SSTI eval, no reflected XSS
    else:
        def _ev(m):
            return str(int(m.group(1)) * int(m.group(2)))
        name = _re.sub(r"[{$#]\{(\d+)\*(\d+)\}\}?", _ev, name)   # evaluates the template (SSTI)
    return ("<html><body>Hello " + name + "</body></html>", 200,
            {"Content-Type": "text/html"})


@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.get("/openapi.json")
def spec():
    secured = [{"bearerAuth": []}]
    return jsonify({
        "openapi": "3.0.0",
        "info": {"title": "QAX golden vuln target", "version": "1.0.0"},
        "paths": {
            "/auth/register": {"post": {"responses": {"201": {"description": "ok"}}}},
            "/auth/login": {"post": {"responses": {"200": {"description": "ok"}}}},
            "/me": {"get": {"security": secured, "responses": {"200": {"description": "ok"}}}},
            "/admin/users": {"get": {"security": secured, "responses": {"200": {"description": "ok"}}}},
            "/billing/invoices": {"get": {"security": secured, "responses": {"200": {"description": "ok"}}}},
            "/greet": {"get": {
                "parameters": [{"name": "name", "in": "query",
                                "schema": {"type": "string"}}],
                "responses": {"200": {"description": "ok"}}}},
            "/health": {"get": {"responses": {"200": {"description": "ok"}}}},
        },
    })


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8911")), debug=False)
