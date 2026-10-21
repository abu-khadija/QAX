"""
evals/targets/ui_demo_app.py — a tiny web app with real pages, for the BROWSER engine
=====================================================================================
The positive control for functional QA in a real browser (Playwright): a login form,
a dashboard and a notes list. It has ONE planted functional bug, so a run proves both
directions — working flows must PASS and the bug must FAIL:

  PLANTED BUG: an empty note is accepted and listed (the page should say
  "Note cannot be empty" and add nothing).

It also reproduces three behaviours of real apps that once broke QAX (linkding):
  * /login sends a signed-in user to the dashboard (cases must not share a session);
  * /live keeps a request open forever (the page never becomes "network idle");
  * /delayed shows its confirmation a moment AFTER the click (async UI updates);
  * /busy (HTTP 429) and /busy-text (200 + "Too many attempts") — the app rate-limits;
  * /spa — a single-page app (hash routes, late rendering, per-render ids; PocketBase).

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
    if request.method == "GET" and _logged_in():
        return redirect("/dashboard")          # like real apps: no login form when signed in
    error = ""
    if request.method == "POST":
        if request.form.get("username") == "demo" and request.form.get("password") == "demo123":
            resp = make_response(redirect("/dashboard"))
            resp.set_cookie("sid", _SESSION, httponly=True, samesite="Lax")
            return resp
        if request.form.get("username") == "throttled":
            # like Healthchecks: 200 + a rate-limit message, then the user is still logged out
            error = '<p id="error">Too many attempts, please try later.</p>'
        else:
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


@app.get("/notes/manage")
def manage_notes():
    """Delete notes one by one (what a cleanup step uses)."""
    if not _logged_in():
        return redirect("/login")
    rows = "".join(f"""<li><form method="post" action="/notes/delete"><input type="hidden" name="note" value="{html.escape(n)}">
<button type="submit">Delete "{html.escape(n)}"</button></form></li>""" for n in _NOTES)
    return _page("Manage notes", f"<h1>Manage notes</h1><ul id='manage'>{rows}</ul>")


@app.post("/notes/delete")
def delete_note():
    """Remove the first note with this text."""
    if not _logged_in():
        return redirect("/login")
    note = request.form.get("note", "")
    if note in _NOTES:
        _NOTES.remove(note)
    return redirect("/notes/manage")


@app.get("/_notes")
def notes_json():
    """The notes as JSON (for tests that check what a run left behind)."""
    return {"notes": list(_NOTES)}


@app.get("/logout")
def logout():
    """Clear the session and return to the login page."""
    resp = make_response(redirect("/login"))
    resp.delete_cookie("sid")
    return resp


@app.get("/live")
def live():
    """A page that keeps a request open forever (like live reload / SSE)."""
    return _page("Live", """<h1 id="live-title">Live page</h1>
<input id="live-input" name="live" placeholder="Live field">
<script>fetch('/stream');</script>""")


@app.get("/stream")
def stream():
    """Never-ending response (the page never reaches network idle)."""
    import time

    def gen():
        while True:
            time.sleep(1)
            yield " "
    return app.response_class(gen(), mimetype="text/plain")


@app.get("/delayed")
def delayed():
    """The confirmation appears 800 ms (or ?ms=…, at most 4000) after the click — no page navigation."""
    try:
        ms = max(0, min(int(request.args.get("ms", "800")), 4000))
    except ValueError:
        ms = 800
    return _page("Delayed", f"""<button id="save-btn" onclick="setTimeout(()=>{{
  document.getElementById('status').innerText='Saved!'}}, {ms})">Save</button>
<p id="status"></p>""")


_POSTS: list = []          # every POST the app received outside /login and /notes (tests read it)


@app.get("/menu-page")
def menu_page():
    """A menu opened by a button, and a typeless button inside a form (it would SUBMIT it)."""
    return _page("Menu", """<button id="menu-btn" type="button" aria-haspopup="true"
  onclick="document.getElementById('m').hidden = !document.getElementById('m').hidden">Account</button>
<div id="m" hidden><a id="profile-link" href="/profile">Profile</a></div>
<form method="post" action="/add-user"><button aria-expanded="false">Details</button></form>""")


@app.get("/profile")
def profile():
    """A page reachable only from the menu."""
    return _page("Profile", "<h1>Profile</h1>")


@app.route("/add-user", methods=["GET", "POST"])
def add_user():
    """Password fields that are NOT a login form (Django admin's "add user")."""
    if request.method == "POST":
        _POSTS.append(dict(request.form))
        return _page("Added", "<p>User added</p>")
    return _page("Add user", """<h1>Add user</h1><form method="post" action="/add-user">
<input name="username" type="text"><input name="password1" type="password">
<input name="password2" type="password"><button type="submit">Save</button></form>""")


_LABELS: list = []


@app.route("/wizard", methods=["GET", "POST"])
def wizard():
    """Two steps; step 1 answers slowly (1.5 s, like a slow API) and both steps have a #name field.
    A tool that acts right after the click types into the page that is leaving, and the value is lost."""
    import time as _t
    if request.method == "POST":
        _t.sleep(1.5)
        return _page("Wizard step 2", """<h1>Step 2</h1><form method="post" action="/wizard/confirm">
<input id="name" name="name"><button id="confirm" type="submit">Confirm</button></form>""")
    return _page("Wizard step 1", """<h1>Step 1</h1><form method="post" action="/wizard">
<input id="name" name="draft" value="draft"><button id="next" type="submit">Next</button></form>""")


@app.get("/wizard-spa")
def wizard_spa():
    """The same two steps, but step 2 arrives by fetch() (1.5 s) and replaces the form in place — no
    navigation, as in Turbo/htmx/SPA apps. Only a wait for the click's own request gets this right."""
    return _page("Wizard (in place)", """<div id="box"><h1>Step 1</h1>
<input id="name" value="draft"><button id="next" type="button" onclick="fetch('/wizard-step2').then(r=>r.text()).then(h=>{document.getElementById('box').innerHTML=h})">Next</button></div>""")


@app.get("/wizard-step2")
def wizard_step2():
    """Step 2's markup, after a slow answer."""
    import time as _t
    _t.sleep(1.5)
    return """<h1>Step 2</h1><form method="post" action="/wizard/confirm"><input id="name" name="name">
<button id="confirm" type="submit">Confirm</button></form>"""


@app.post("/wizard/confirm")
def wizard_confirm():
    """Shows what step 2 received."""
    return _page("Done", f'<p id="done">Confirmed: {html.escape(request.form.get("name", ""))}</p>')


@app.route("/labels", methods=["GET", "POST"])
def labels():
    """Labels must have unique names (like most apps' tags, usernames, project keys): a duplicate
    is refused. A run whose test data is not unique collides here (functional mutants)."""
    msg = ""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            msg = '<p id="label-msg">Name is required</p>'
        elif name in _LABELS:
            msg = '<p id="label-msg">A label with this name already exists</p>'
        else:
            _LABELS.append(name)
            msg = f'<p id="label-msg">Label created: {html.escape(name)}</p>'
    return _page("Labels", f"""<h1>Labels</h1>{msg}
<form method="post" action="/labels"><input id="label-name" name="name"><button id="label-save" type="submit">Create</button></form>""")


@app.get("/_posts")
def posts():
    """Test helper: the POSTs /add-user received."""
    return {"posts": _POSTS}


@app.get("/obstacles")
def obstacles():
    """What stops a naive runner: a cookie banner, an overlay over the button, a button far
    below the fold, a link that opens a new tab, a JavaScript error and a failing API call."""
    return _page("Obstacles", """
<div id="cookie-banner" style="position:fixed;bottom:0;left:0;right:0;background:#eee;padding:8px;z-index:10">
  We use cookies. <button id="accept-all">Accept all</button> <button id="reject-all"
  onclick="document.getElementById('cookie-banner').remove()">Reject all</button></div>
<div id="overlay" style="position:fixed;inset:0;background:#0003;z-index:5"></div>
<script>
document.addEventListener('keydown', e => { if (e.key === 'Escape') { const o = document.getElementById('overlay'); if (o) o.remove(); } });
setTimeout(() => { throw new Error('demo uncaught error'); }, 10);
fetch('/boom');
</script>
<button id="covered-btn" onclick="document.getElementById('out').innerText='Covered clicked'">Covered</button>
<p id="out"></p>
<div style="height:3000px"></div>
<button id="far-btn" onclick="document.getElementById('out').innerText='Far clicked'">Far</button>
<a id="newtab-link" href="/newtab" target="_blank">Open help</a>""")


@app.get("/newtab")
def newtab():
    """The page a target=_blank link opens."""
    return _page("Help", "<h1 id='help-title'>Help centre</h1>")


@app.get("/frames")
def frames():
    """A payment-style form embedded in an iframe — the elements live in another document."""
    return _page("Checkout", """<h1>Checkout</h1>
<iframe id="pay-frame" title="Payment" src="/frame-inner" style="width:420px;height:160px"></iframe>""")


@app.get("/frame-inner")
def frame_inner():
    """The document inside the checkout iframe."""
    return _page("Payment", """<label for="card">Card number</label> <input id="card" name="card">
<button id="pay" onclick="document.getElementById('paid').textContent='Paid with '+document.getElementById('card').value">Pay now</button>
<p id="paid"></p>""")


@app.get("/a11y")
def a11y():
    """Accessibility problems a QA engineer would notice: an image with no alt text, a field
    with only a placeholder, an icon button with no name, grey-on-white text, a skipped heading."""
    return _page("Signup", """<h1>Signup</h1><h3>Details</h3>
<img src="/static-logo.png" width="10" height="10">
<input id="email-x" placeholder="Email">
<button id="icon-btn" onclick="this.dataset.n=1"></button>
<p style="color:#bbbbbb;background:#ffffff">Terms apply</p>""")


@app.get("/shadow")
def shadow():
    """A web component: its form lives in an open shadow root (Lit, Ionic, Salesforce …)."""
    return _page("Greeter", """<h1>Greeter</h1><qax-greeter></qax-greeter>
<script>
customElements.define('qax-greeter', class extends HTMLElement {
  connectedCallback() {
    const r = this.attachShadow({mode: 'open'});
    r.innerHTML = '<label for="who">Your name</label> <input id="who">'
      + ' <button id="greet">Greet</button> <p id="hello"></p>';
    r.getElementById('greet').onclick = () => { r.getElementById('hello').textContent = 'Hello, ' + r.getElementById('who').value; };
  }
});
</script>""")


@app.get("/team")
def team():
    """A table with Edit/Delete on every row and Angular-style numeric ids (row indexes)."""
    rows = "".join(f"<tr><td>{n}</td><td><input id='{i}' value='{n}'></td><td><button onclick=\"document.getElementById('out').textContent='Edit {n}'\">Edit</button>"
                   f" <button onclick=\"this.closest('tr').remove()\">Delete</button></td></tr>"
                   for i, n in enumerate(["Ada Lovelace", "Alan Turing", "Grace Hopper"]))
    return _page("Team", f"<h1>Team</h1><table id='team'>{rows}</table><p id='out'></p>")


@app.get("/shop")
def shop():
    """Product cards, each with the same '[ ADD TO BASKET ]' submit button, and a basket link
    whose text is only the item count."""
    cards = "".join(f"<div class='card'><img src='/x.png' alt='{n}'><div><span>{n}</span> <span>$1{i}.00</span></div>"
                    f"<form onsubmit=\"document.getElementById('out').textContent='Added {n}';return false\">"
                    f"<input type='submit' value='[ ADD TO BASKET ]'></form></div>"
                    for i, n in enumerate(["Bot Mug", "Bot Shirt", "Bot Cap"]))
    return _page("Shop", f"<a href='/basket-page'>0</a><div class='grid'>{cards}</div><p id='out'></p>")


@app.get("/away")
def away():
    """An open redirect (for origin-guard tests): sends the browser to ?to=."""
    return redirect(request.args.get("to", "/"))


@app.get("/link-away")
def link_away():
    """A link and an embedded form that belong to another site (?to=)."""
    to = html.escape(request.args.get("to", "/"))
    return _page("Elsewhere", f"<a id='out-link' href='{to}'>Leave</a>"
                              f"<iframe id='foreign' src='{to}form'></iframe>")


@app.get("/dialog")
def dialog():
    """A dialog whose submit repeats its opener's text ("Add item" ×2, like Beszel's Add System)."""
    return _page("Items", """<button type="button" onclick="document.getElementById('dlg').showModal()">Add item</button>
<dialog id="dlg"><form method="dialog"><h2>New item</h2><input id="item-name"><button type="submit">Add item</button></form></dialog>""")


@app.get("/boom")
def boom():
    """A server error the page calls in the background."""
    return {"error": "boom"}, 500


_SPA = """<!doctype html><html><head><meta charset="utf-8"><title>SPA</title></head><body>
<nav><a href="#/home">Home</a> <a href="#/settings">Settings</a> <a href="#/export-data">Export</a></nav>
<div id="view"></div>
<script>
function rnd(){ return Math.random().toString(36).slice(2, 10) + '1'; }   // per-render ids
function render(){
  const r = location.hash || '#/home', v = document.getElementById('view');
  v.innerHTML = '';
  setTimeout(() => {                                // rendered after an async "API call"
    if (r === '#/settings') {
      const id = 'name_' + rnd();
      v.innerHTML = '<h2>Rate limits</h2><label for="' + id + '">App name</label>'
        + '<input id="' + id + '" name="appname" value="' + (localStorage.appname || 'Demo') + '">'
        + '<button class="save" disabled><span>Save changes</span></button><p class="msg"></p>';
      const i = v.querySelector('input'), b = v.querySelector('button');
      i.oninput = () => { b.disabled = (i.value === (localStorage.appname || 'Demo')); };  // only a change enables Save
      b.onclick = () => { localStorage.appname = i.value; v.querySelector('.msg').innerText = 'Saved'; b.disabled = true; };
    } else { v.innerHTML = '<h2>Home</h2>'; }
  }, 400);
}
window.onhashchange = render; render();
</script></body></html>"""


@app.get("/spa")
def spa():
    """A single-page app: hash routes, a form rendered late, ids that change per render,
    a button whose text is in a child and that is disabled until a change (PocketBase)."""
    return _SPA


@app.get("/busy")
def busy():
    """A rate-limited answer (HTTP 429), like a gateway in front of a staging app."""
    return _page("Busy", "<h1>Too Many Requests</h1>"), 429


@app.get("/busy-text")
def busy_text():
    """Rate limiting reported in the page with a 200 (Healthchecks: "Too many attempts")."""
    return _page("Log in", '<p id="error">Too many attempts, please try later.</p>')


@app.post("/_reset")
def reset():
    """Test helper: clear the notes."""
    _NOTES.clear()
    return {"ok": True}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="QAX browser-engine demo app")
    ap.add_argument("--port", type=int, default=8911)
    a = ap.parse_args()
    app.run(host="127.0.0.1", port=a.port, debug=False, threaded=True)
