"""
Golden regression target for QAX (LOCAL EVAL ONLY — never deploy).

A deliberately-vulnerable Flask API whose protected endpoints decode the JWT
WITHOUT verifying its signature, so a forged token is accepted everywhere. It is
the fixed scenario `evals/gate.py` scans on every change to prove QAX still:
  • proves the JWT signature flaw (jwt-flaws),
  • sweeps the forged token across the whole protected surface (forged-token-replay),
  • flags missing security headers,
and stays SILENT when the flaw is fixed (QAX_SECURE=1 — the no-false-positive control).

/tasks (POST create + GET list) plants the five SPEC-TYPED classes mined from the owner's KB
(boolean-false, single-element-array, date-order, long-input, duplicate-rows); SECURE
handles each correctly so the same probes must go silent (the no-false-positive control).

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


@app.get("/reset-link")
def reset_link():
    # VULNERABLE: builds the reset URL from the incoming Host header (host-header
    # injection → reset-link / cache poisoning). SECURE uses a fixed canonical host.
    host = "app.example.com" if SECURE else request.headers.get("Host", "app.example.com")
    return ('<html><body>Reset at https://' + host + '/reset?t=abc</body></html>', 200,
            {"Content-Type": "text/html"})


@app.post("/import")
def import_xml():
    # VULNERABLE: "parses" XML with external entities resolved — a file:// entity reads
    # a local file (XXE). Simulated: if the body references /etc/passwd via SYSTEM, inline
    # the passwd signature. SECURE mode does not resolve entities.
    raw = request.get_data(as_text=True) or ""
    if (not SECURE) and 'SYSTEM "file:///etc/passwd"' in raw:
        return ("<result>root:x:0:0:root:/root:/bin/bash</result>", 200,
                {"Content-Type": "application/xml"})
    return ("<result>ok</result>", 200, {"Content-Type": "application/xml"})


@app.post("/graphql")
def graphql():
    # VULNERABLE: introspection enabled — returns the schema. SECURE disables it.
    body = request.get_json(silent=True) or {}
    if "__schema" in str(body.get("query", "")):
        if SECURE:
            return jsonify({"errors": [{"message": "introspection is disabled"}]}), 200
        return jsonify({"data": {"__schema": {"queryType": {"name": "Query"}}}}), 200
    return jsonify({"data": {}}), 200


_UPLOADS: dict = {}


@app.post("/upload")
def upload():
    f = request.files.get("file")
    if f is None and request.files:
        f = next(iter(request.files.values()))
    if f is None:
        return jsonify({"error": "no file"}), 400
    name = f.filename or "file"
    # VULNERABLE: accepts any extension and later serves it by its guessed type.
    # SECURE: only image uploads are allowed.
    if SECURE and not name.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
        return jsonify({"error": "only image uploads allowed"}), 400
    _UPLOADS[name] = f.read()
    return jsonify({"url": "/uploads/" + name}), 201


@app.get("/uploads/<name>")
def serve_upload(name):
    import mimetypes
    data = _UPLOADS.get(name)
    if data is None:
        return jsonify({"error": "not found"}), 404
    # VULNERABLE: serves with the type guessed from the extension (so .html → text/html,
    # executable in-origin). SECURE: never serves user uploads as an executable type.
    ct = "application/octet-stream" if SECURE else (mimetypes.guess_type(name)[0] or "application/octet-stream")
    return (data, 200, {"Content-Type": ct})


@app.post("/orders")
def create_order():
    body = request.get_json(silent=True) or {}
    qty = body.get("quantity", 1)
    # VULNERABLE: no business validation — a negative quantity is accepted and stored.
    # SECURE: reject non-positive quantities.
    if SECURE and (not isinstance(qty, int) or qty < 1):
        return jsonify({"error": "quantity must be positive"}), 400
    return jsonify({"id": 1, "item": body.get("item", "x"), "quantity": qty}), 201


_TASKS: list = []


@app.post("/tasks")
def create_task():
    """
    A create endpoint carrying the five spec-typed defects (each fixed in SECURE mode):
      • boolean-false       `done:false` is treated as a missing required field (422);
      • single-element-array a 1-item `labels` is rejected (scalar-vs-array binding, 400);
      • date-order          `ends_at` before `starts_at` is accepted and stored;
      • long-input          a very long `title` crashes with an unhandled 500;
    SECURE: false/1-item accepted, reversed dates 422, long title a clean 422.
    """
    body = request.get_json(silent=True) or {}
    title = body.get("title")
    if not isinstance(title, str) or not title:
        return jsonify({"error": "title is required"}), 422
    if body.get("done") is False and not SECURE:
        return jsonify({"message": "The done field is required."}), 422          # implicit-required bool
    labels = body.get("labels", [])
    if isinstance(labels, list) and len(labels) == 1 and not SECURE:
        return jsonify({"message": "labels must be an array"}), 400              # scalar-vs-array binding
    s, e = body.get("starts_at"), body.get("ends_at")
    if SECURE and isinstance(s, str) and isinstance(e, str) and e < s:
        return jsonify({"error": "ends_at must not be before starts_at"}), 422
    if len(title) > 1000:
        if SECURE:
            return jsonify({"error": "title is too long"}), 422
        return jsonify({"error": "value too long for column"}), 500             # unhandled overflow
    rec = {"id": len(_TASKS) + 1, "title": title, "done": body.get("done", True),
           "labels": labels if isinstance(labels, list) else [], "starts_at": s, "ends_at": e}
    _TASKS.append(rec)
    return jsonify(rec), 201


@app.get("/tasks")
def list_tasks():
    """
    VULNERABLE: a join fan-out lists a task once PER LABEL, so a task with two labels
    appears twice (same id). SECURE lists each task once. Honours ?limit= so QAX's
    unpaginated-list probe stays quiet (this target is not about pagination).
    """
    rows: list = []
    for t in _TASKS:
        if SECURE or not t["labels"]:
            rows.append(t)
        else:
            rows.extend(dict(t, label=lab) for lab in t["labels"])
    try:
        rows = rows[:max(0, int(request.args["limit"]))]
    except (KeyError, ValueError):
        pass
    return jsonify(rows), 200


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
            "/reset-link": {"get": {"responses": {"200": {"description": "ok"}}}},
            "/import": {"post": {"responses": {"200": {"description": "ok"}}}},
            "/graphql": {"post": {"responses": {"200": {"description": "ok"}}}},
            "/upload": {"post": {"responses": {"201": {"description": "ok"}}}},
            "/orders": {"post": {"responses": {"201": {"description": "ok"}},
                                 "requestBody": {"content": {"application/json": {"schema": {"properties": {
                                     "item": {"type": "string"}, "quantity": {"type": "integer"}}}}}}}},
            "/tasks": {
                "get": {"parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                        "responses": {"200": {"description": "ok"}}},
                "post": {"responses": {"201": {"description": "ok"}},
                         "requestBody": {"content": {"application/json": {"schema": {
                             "type": "object", "required": ["title"],
                             "properties": {
                                 "title": {"type": "string"},
                                 "done": {"type": "boolean"},
                                 "labels": {"type": "array", "items": {"type": "string"}},
                                 "starts_at": {"type": "string", "format": "date-time"},
                                 "ends_at": {"type": "string", "format": "date-time"}}}}}}}},
            "/health": {"get": {"responses": {"200": {"description": "ok"}}}},
        },
    })


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8911")), debug=False)
