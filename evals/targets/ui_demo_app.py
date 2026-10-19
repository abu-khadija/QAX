"""
evals/targets/ui_demo_app.py — a tiny web app with real pages, for the BROWSER engine
=====================================================================================
The positive control for functional QA in a real browser (Playwright): a login form,
a dashboard and a notes list. It has ONE planted functional bug, so a run proves both
directions — working flows must PASS and the bug must FAIL:

  PLANTED BUG: an empty note is accepted and listed (the page should say
  "Note cannot be empty" and add nothing).

Credentials are fixed demo values (user `demo`, password `demo123`) — this app holds no
real data and listens on 127.0.0.1 only. Selectors: config/projects/uidemo/selectors.json.

Run:  python evals/targets/ui_demo_app.py --port 8911
"""

from __future__ import annotations

import argparse
import html

from flask import Flask, make_response, redirect, request

app = Flask(__name__)
_NOTES: list[str] = []
_SESSION = "uidemo-session"

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head>
<body><nav><a id="logout-link" href="/logout">Log out</a></nav>{body}</body></html>"""


def _page(title: str, body: str):
    """Render a minimal HTML page."""
    return _PAGE.format(title=html.escape(title), body=body)


def _logged_in() -> bool:
    """True when the demo session cookie is present."""
    return request.cookies.get("sid") == _SESSION


@app.get("/")
def index():
    """Send visitors to the dashboard or the login page."""
    return redirect("/dashboard" if _logged_in() else "/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login form; demo/demo123 logs in, anything else shows an error."""
    error = ""
    if request.method == "POST":
        if request.form.get("username") == "demo" and request.form.get("password") == "demo123":
            resp = make_response(redirect("/dashboard"))
            resp.set_cookie("sid", _SESSION, httponly=True, samesite="Lax")
            return resp
        error = '<p id="error">Invalid username or password</p>'
    return _page("Log in", f"""<h1>Log in</h1>{error}
<form method="post" action="/login">
  <input id="username" name="username" placeholder="Username">
  <input id="password" name="password" type="password" placeholder="Password">
  <button id="login-btn" type="submit">Log in</button>
</form>""")


@app.get("/dashboard")
def dashboard():
    """The signed-in home page with the notes list and the add-note form."""
    if not _logged_in():
        return redirect("/login")
    items = "".join(f"<li>{html.escape(n) or '&nbsp;'}</li>" for n in _NOTES)
    return _page("Dashboard", f"""<h1 id="welcome">Welcome, demo</h1>
<form method="post" action="/notes">
  <input id="note-input" name="note" placeholder="New note">
  <button id="add-note-btn" type="submit">Add note</button>
</form>
<p id="note-count">{len(_NOTES)} note(s)</p>
<ul id="notes">{items}</ul>""")


@app.post("/notes")
def add_note():
    """Add a note. PLANTED BUG: an empty note is not rejected."""
    if not _logged_in():
        return redirect("/login")
    _NOTES.append(request.form.get("note", "").strip())   # BUG: no empty check
    return redirect("/dashboard")


@app.get("/logout")
def logout():
    """Clear the session and return to the login page."""
    resp = make_response(redirect("/login"))
    resp.delete_cookie("sid")
    return resp


@app.post("/_reset")
def reset():
    """Test helper: clear the notes."""
    _NOTES.clear()
    return {"ok": True}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="QAX browser-engine demo app")
    ap.add_argument("--port", type=int, default=8911)
    a = ap.parse_args()
    app.run(host="127.0.0.1", port=a.port, debug=False)
