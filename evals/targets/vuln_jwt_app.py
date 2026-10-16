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

Repo sharing (`/user`, `/user/repos`, `/repos/{owner}/{repo}` + `/collaborators/{name}`) plants STALE
ACCESS AFTER REVOCATION: the vulnerable app keeps a "ever granted" cache, so a collaborator the owner
REMOVED can still read the repo; SECURE checks current membership only. It needs two supplied
identities (QAX's access-revocation probe), so the gate exercises it in a separate authenticated scan.

Endpoints: /me (control), /admin/users, /billing/invoices (protected, swept),
/health (public), /auth/register + /auth/login (register is required before login),
/openapi.json (so QAX grounds the exact surface regardless of source modelling).
"""
import base64
import hashlib
import hmac
import json
import os

from flask import Flask, jsonify, make_response, request

app = Flask(__name__)
SECURE = os.environ.get("QAX_SECURE") == "1"
# The signing secret is "hardcoded in the source" (the source lane of the gate tells QAX so). SECURE
# rotates it to a value that is not in the source, so a token forged with the leaked secret is rejected.
SECRET = os.urandom(32) if SECURE else b"super-secret-signing-key"
LEAKED_SECRET = "super-secret-signing-key"
_USERS: dict = {}
_FAILS: dict = {}


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
    pw = str(body.get("password", ""))
    if len(pw) > 72:
        # VULNERABLE: the hashing layer chokes on a >72-byte password and the error is unhandled (5xx).
        # SECURE: a clean validation error.
        if SECURE:
            return jsonify({"error": "password too long"}), 422
        return jsonify({"error": "hash failed: password cannot be longer than 72 bytes"}), 500
    _USERS[body.get("email", "")] = body.get("password", "")
    # VULNERABLE: binds and echoes every client-supplied field (admin=true, role=admin ...) — mass
    # assignment. SECURE: only the identity comes back.
    rec = {"email": body.get("email", "")} if SECURE else {k: v for k, v in body.items() if k != "password"}
    return jsonify(rec), 201


@app.post("/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    email = body.get("email", "")
    if email not in _USERS:                 # must register first
        return jsonify({"error": "no such user"}), 401
    if _USERS[email] != body.get("password", ""):
        # VULNERABLE: unlimited wrong-password attempts (no throttling / lockout). SECURE: 429 after 5.
        if SECURE:
            _FAILS[email] = _FAILS.get(email, 0) + 1
            if _FAILS[email] > 5:
                return jsonify({"error": "too many attempts"}), 429
        return jsonify({"error": "invalid credentials"}), 401
    tok = _make_token(email)
    resp = make_response(jsonify({"access_token": tok}), 200)
    # VULNERABLE: the session cookie is readable by JavaScript (no HttpOnly). SECURE sets it.
    resp.set_cookie("session", tok, httponly=SECURE)
    return resp


_COMMENTS: list = []
_NOTES: dict = {}       # title -> {"owner", "title", "body", "summary"}


@app.post("/comments")
def add_comment():
    """Stores a comment. The GET below renders it as HTML (stored XSS when unescaped)."""
    body = request.get_json(silent=True) or {}
    content = body.get("content")
    if not isinstance(content, str):
        return jsonify({"error": "content must be a string"}), 422
    _COMMENTS.append(content)
    return jsonify({"id": len(_COMMENTS)}), 201


@app.get("/comments")
def list_comments():
    """VULNERABLE: stored comments are written into the HTML page raw (stored XSS).
    SECURE: HTML-escaped."""
    import html as _html
    items = _COMMENTS if not SECURE else [_html.escape(c) for c in _COMMENTS]
    return ("<html><body><ul>" + "".join(f"<li>{c}</li>" for c in items) + "</ul></body></html>", 200,
            {"Content-Type": "text/html"})


@app.post("/lookup")
def lookup():
    """
    VULNERABLE (two classes on one lookup):
      • SQL injection — a quote in `username` breaks the query (error text returned, HTTP 500);
      • NoSQL operator injection — an operator object ({"$ne": x}) is evaluated as a filter and
        returns every account.
    SECURE: parameterised (a quote is just a character) and operator objects are rejected (400).
    """
    body = request.get_json(silent=True) or {}
    u = body.get("username")
    if isinstance(u, dict):
        if SECURE:
            return jsonify({"error": "username must be a string"}), 400
        everyone = [{"username": n} for n in list(_USERS) + ["admin", "root", "svc-backup"]]
        if "$ne" in u:
            return jsonify({"users": everyone}), 200          # filter bypassed: all accounts
        return jsonify({"users": []}), 200                    # $eq / anything else: no match
    if not isinstance(u, str):
        return jsonify({"error": "username is required"}), 422
    if not SECURE and ("'" in u or '"' in u):
        return ('sqlite3.OperationalError: near "\'": syntax error', 500, {"Content-Type": "text/plain"})
    return jsonify({"users": [{"username": n} for n in _USERS if n == u]}), 200


@app.post("/notes")
def create_note():
    """
    Private notes, owned by the creator. VULNERABLE: the `summary` field is accepted ("saved") but
    never stored (silent data loss on save). SECURE stores every field.
    """
    sub = _auth()
    if not sub:
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    title, text, summary = body.get("title"), body.get("body"), body.get("summary")
    if not isinstance(title, str) or not title:
        return jsonify({"error": "title is required"}), 422
    note = {"owner": sub, "title": title, "body": text if isinstance(text, str) else ""}
    if SECURE:
        note["summary"] = summary if isinstance(summary, str) else ""
    _NOTES[title] = note
    return jsonify({"success": True, "message": "saved"}), 201


@app.get("/notes/<note_id>")
def get_note(note_id):
    """VULNERABLE: any authenticated user can read any note (BOLA). SECURE: owner only (404 otherwise)."""
    sub = _auth()
    if not sub:
        return jsonify({"error": "unauthorized"}), 401
    note = _NOTES.get(note_id)
    if note is None or (SECURE and note["owner"] != sub):
        return jsonify({"error": "not found"}), 404
    return jsonify(note), 200


@app.post("/appointments")
def create_appointment():
    """VULNERABLE: `scheduled_at` is parsed with no error handling, so a malformed value is an
    unhandled 500. SECURE: a clean 422."""
    import datetime as _dt
    body = request.get_json(silent=True) or {}
    title, when = body.get("title"), body.get("scheduled_at")
    if not isinstance(title, str) or not isinstance(when, str):
        return jsonify({"error": "title and scheduled_at must be strings"}), 422
    try:
        at = _dt.datetime.fromisoformat(when)
    except ValueError:
        if SECURE:
            return jsonify({"error": "scheduled_at must be an ISO-8601 datetime"}), 422
        raise                                                    # VULNERABLE: unhandled -> HTTP 500
    return jsonify({"id": 1, "title": title, "scheduled_at": at.isoformat()}), 201


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


def _auth_unsafe():
    """VULNERABLE: decodes the token with no error handling, so a malformed token crashes the
    handler (an unhandled 5xx where a clean 401 belongs). SECURE: the guarded path."""
    tok = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not tok:
        return None
    if SECURE:
        return _subject(tok)
    _h, p, _s = tok.split(".")
    return json.loads(_b64u_dec(p)).get("sub")


@app.get("/account")
def account():
    """VULNERABLE: returns the stored credential material along with the profile
    (excessive data exposure) and crashes on a malformed token (auth-error). SECURE: neither."""
    sub = _auth_unsafe()
    if not sub:
        return jsonify({"error": "unauthorized"}), 401
    out = {"email": sub, "plan": "free"}
    if not SECURE:
        out["password_hash"] = "$2b$12$notarealhashnotarealhashnotarealhashnotarealhash"
    return jsonify(out), 200


@app.get("/items/<item_id>")
def item(item_id):
    """VULNERABLE: a non-numeric id raises and the raw traceback is returned to the client
    (stack-trace disclosure). SECURE: a clean 404."""
    try:
        n = int(item_id)
    except ValueError:
        if SECURE:
            return jsonify({"error": "not found"}), 404
        return ("Traceback (most recent call last):\n  File \"/srv/app/items.py\", line 12, in item\n"
                f"    n = int({item_id!r})\nValueError: invalid literal for int()", 500,
                {"Content-Type": "text/plain"})
    return jsonify({"id": n, "name": "widget"}), 200


@app.errorhandler(404)
def not_found(_e):
    """VULNERABLE: the global handler for unknown routes itself fails (5xx instead of 404)."""
    return (jsonify({"error": "not found"}), 404) if SECURE else (jsonify({"error": "internal"}), 500)


@app.get("/products/<pid>")
def product(pid):
    """VULNERABLE: the id is spliced into a raw SQL string — a quote breaks the query and the driver's
    error text comes back (SQL injection). SECURE: parameterised (a quote is just a character)."""
    if not SECURE and ("'" in pid or '"' in pid):
        return ('sqlite3.OperationalError: unrecognized token: "\'"', 500, {"Content-Type": "text/plain"})
    return (jsonify({"id": pid, "name": "widget"}), 200) if pid.isdigit() else (jsonify({"error": "not found"}), 404)


@app.get("/ping")
def ping():
    """
    VULNERABLE: `host` is interpolated into a shell command (simulated — nothing is executed): a
    separator followed by `echo X` runs and its output is returned. SECURE: strict hostname allow-list.
    """
    import re as _re
    host = request.args.get("host", "127.0.0.1")
    if SECURE and not _re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", host):
        return jsonify({"error": "invalid host"}), 400
    parts = _re.split(r";|&&|\||`|\$\(", host)
    out = f"PING {parts[0].strip()}: 64 bytes\n"
    for extra in parts[1:]:
        m = _re.match(r"\s*echo\s+([^\s)`]+)", extra)
        if m:
            out += m.group(1) + "\n"
    return out, 200, {"Content-Type": "text/plain"}


@app.get("/search")
def search():
    """VULNERABLE: raw search-engine operator syntax — an ordinary special character (email '@', a
    slash, quotes...) is rejected with 405. SECURE: the query is escaped and answered."""
    q = request.args.get("q", "")
    if not SECURE and any(ch in q for ch in '@"()/|~^-'):
        return jsonify({"error": "method not allowed"}), 405
    return jsonify({"results": [], "q": q[:50]}), 200


@app.get("/fetch")
def fetch():
    """VULNERABLE: fetches any http(s) URL it is given (SSRF). SECURE: refuses non-allow-listed hosts."""
    import urllib.request
    from urllib.parse import urlparse
    url = request.args.get("url", "")
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "url must be http(s)"}), 400
    if SECURE and urlparse(url).hostname not in ("api.example.com",):
        return jsonify({"error": "host not allowed"}), 400
    try:
        n = len(urllib.request.urlopen(url, timeout=3).read())
    except Exception:
        return jsonify({"error": "fetch failed"}), 502
    return jsonify({"fetched_bytes": n}), 200


@app.after_request
def _cors(resp):
    """VULNERABLE: reflects ANY Origin with credentials allowed (CORS misconfiguration). SECURE: no CORS."""
    origin = request.headers.get("Origin")
    if origin and not SECURE:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp


@app.get("/download")
def download():
    """VULNERABLE: the `file` query param is used as a filesystem path with no sanitising (traversal).
    Simulated: a path naming /etc/passwd returns its contents. SECURE: rejects any path-like value."""
    f = request.args.get("file", "readme.txt")
    if SECURE and (".." in f or f.startswith(("/", "\\")) or ":" in f or "%2f" in f.lower()):
        return jsonify({"error": "invalid file name"}), 400
    if "etc/passwd" in f.replace("%2f", "/").replace("%2F", "/"):
        return "root:x:0:0:root:/root:/bin/bash\n", 200, {"Content-Type": "text/plain"}
    return "Hello from " + f, 200, {"Content-Type": "text/plain"}


@app.get("/logs")
def logs():
    """VULNERABLE: returns every row and ignores ?limit= (unbounded, unpaginated list).
    SECURE: paginates (default 50) and honours limit."""
    rows = [{"id": i, "msg": f"event {i}"} for i in range(150)]
    if SECURE:
        try:
            n = int(request.args.get("limit", 50))
        except ValueError:
            n = 50
        rows = rows[:max(0, n)]
    return jsonify(rows), 200


@app.get("/redirect")
def redirect_to():
    """VULNERABLE: redirects to whatever `url` says (open redirect). SECURE: same-site paths only."""
    url = request.args.get("url", "/")
    if SECURE and not (url.startswith("/") and not url.startswith("//")):
        return jsonify({"error": "invalid redirect target"}), 400
    return "", 302, {"Location": url}


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
_REPOS: dict = {}       # (owner, name) -> {}
_COLLAB: dict = {}      # (owner, name) -> {current collaborators}
_EVER: dict = {}        # (owner, name) -> {everyone ever granted}  (the stale cache)


@app.get("/user")
def whoami():
    sub = _auth()
    return (jsonify({"login": sub}), 200) if sub else (jsonify({"error": "unauthorized"}), 401)


@app.get("/user/repos")
def my_repos():
    sub = _auth()
    if not sub:
        return jsonify({"error": "unauthorized"}), 401
    key = (sub, "proj")
    _REPOS.setdefault(key, {})                    # every user owns one repo (lazily created)
    return jsonify([{"owner": {"login": o}, "name": n} for (o, n) in _REPOS if o == sub]), 200


@app.get("/repos/<owner>/<repo>")
def get_repo(owner, repo):
    sub = _auth()
    if not sub:
        return jsonify({"error": "unauthorized"}), 401
    key = (owner, repo)
    if key not in _REPOS:
        return jsonify({"error": "not found"}), 404
    allowed = sub == owner or sub in _COLLAB.get(key, set())
    # VULNERABLE: a revoked collaborator is still honoured from a permission cache that is never
    # invalidated. SECURE: current membership only.
    if not SECURE and sub in _EVER.get(key, set()):
        allowed = True
    return (jsonify({"owner": owner, "name": repo}), 200) if allowed else (jsonify({"error": "not found"}), 404)


@app.put("/repos/<owner>/<repo>/collaborators/<name>")
def add_collaborator(owner, repo, name):
    sub = _auth()
    if not sub:
        return jsonify({"error": "unauthorized"}), 401
    key = (owner, repo)
    if sub != owner or key not in _REPOS:
        return jsonify({"error": "forbidden"}), 403
    _COLLAB.setdefault(key, set()).add(name)
    _EVER.setdefault(key, set()).add(name)
    return "", 204


@app.delete("/repos/<owner>/<repo>/collaborators/<name>")
def remove_collaborator(owner, repo, name):
    sub = _auth()
    if not sub:
        return jsonify({"error": "unauthorized"}), 401
    key = (owner, repo)
    if sub != owner or key not in _REPOS:
        return jsonify({"error": "forbidden"}), 403
    _COLLAB.get(key, set()).discard(name)         # the row is removed… (the _EVER cache is NOT, when vulnerable)
    if SECURE:
        _EVER.get(key, set()).discard(name)
    return "", 204


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
            "/comments": {
                "get": {"responses": {"200": {"description": "html"}}},
                "post": {"responses": {"201": {"description": "ok"}},
                         "requestBody": {"content": {"application/json": {"schema": {"properties": {
                             "content": {"type": "string"}}}}}}}},
            "/lookup": {"post": {"responses": {"200": {"description": "ok"}},
                                 "requestBody": {"content": {"application/json": {"schema": {"properties": {
                                     "username": {"type": "string"}}}}}}}},
            "/notes": {"post": {"security": secured, "responses": {"201": {"description": "ok"}},
                                "requestBody": {"content": {"application/json": {"schema": {"properties": {
                                    "title": {"type": "string"}, "body": {"type": "string"},
                                    "summary": {"type": "string"}}}}}}}},
            "/notes/{note_id}": {"get": {"security": secured,
                                         "parameters": [{"name": "note_id", "in": "path", "required": True,
                                                         "schema": {"type": "string"}}],
                                         "responses": {"200": {"description": "ok"}}}},
            "/appointments": {"post": {"responses": {"201": {"description": "ok"}},
                                       "requestBody": {"content": {"application/json": {"schema": {"properties": {
                                           "title": {"type": "string"},
                                           "scheduled_at": {"type": "string", "format": "date-time"}}}}}}}},
            "/me": {"get": {"security": secured, "responses": {"200": {"description": "ok"}}}},
            "/admin/users": {"get": {"security": secured, "responses": {"200": {"description": "ok"}}}},
            "/billing/invoices": {"get": {"security": secured, "responses": {"200": {"description": "ok"}}}},
            "/account": {"get": {"security": secured, "responses": {"200": {"description": "ok"}}}},
            "/items/{item_id}": {"get": {"parameters": [{"name": "item_id", "in": "path", "required": True,
                                                         "schema": {"type": "string"}}],
                                         "responses": {"200": {"description": "ok"}}}},
            "/download": {"get": {"parameters": [{"name": "file", "in": "query", "schema": {"type": "string"}}],
                                  "responses": {"200": {"description": "ok"}}}},
            "/logs": {"get": {"parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                              "responses": {"200": {"description": "ok"}}}},
            "/redirect": {"get": {"parameters": [{"name": "url", "in": "query", "schema": {"type": "string"}}],
                                  "responses": {"302": {"description": "redirect"}}}},
            "/products/{pid}": {"get": {"parameters": [{"name": "pid", "in": "path", "required": True,
                                                         "schema": {"type": "string"}}],
                                        "responses": {"200": {"description": "ok"}}}},
            "/ping": {"get": {"parameters": [{"name": "host", "in": "query", "schema": {"type": "string"}}],
                              "responses": {"200": {"description": "ok"}}}},
            "/search": {"get": {"parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}],
                                "responses": {"200": {"description": "ok"}}}},
            "/fetch": {"get": {"parameters": [{"name": "url", "in": "query", "schema": {"type": "string"}}],
                               "responses": {"200": {"description": "ok"}}}},
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
            "/user": {"get": {"security": secured, "responses": {"200": {"description": "ok"}}}},
            "/user/repos": {"get": {"security": secured, "responses": {"200": {"description": "ok"}}}},
            "/repos/{owner}/{repo}": {"get": {"security": secured, "responses": {"200": {"description": "ok"}}}},
            "/repos/{owner}/{repo}/collaborators/{collaborator}": {
                "put": {"security": secured, "responses": {"204": {"description": "ok"}}},
                "delete": {"security": secured, "responses": {"204": {"description": "ok"}}}},
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
