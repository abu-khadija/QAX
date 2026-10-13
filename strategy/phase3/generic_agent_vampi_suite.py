"""
test_vampi_api.py — a functional API test suite for VAmPI.

Written as a generalist coding agent would when asked only to "write a test suite
for this API": cover the endpoints' happy paths and basic validation, asserting
the behaviour the API appears to promise. No security intent, not told what (if
anything) is wrong with the app.

Run: pytest test_vampi_api.py -v   (VAmPI on http://127.0.0.1:5001)
"""

import uuid
import requests

BASE = "http://127.0.0.1:5001"


def _new_user():
    u = "user_" + uuid.uuid4().hex[:8]
    return {"username": u, "password": "Passw0rd!", "email": f"{u}@example.com"}


def _register(user):
    return requests.post(f"{BASE}/users/v1/register", json=user, timeout=10)


def _login(user):
    return requests.post(f"{BASE}/users/v1/login",
                         json={"username": user["username"], "password": user["password"]}, timeout=10)


def _token(user):
    _register(user)
    return _login(user).json().get("auth_token")


# ── root / discovery ─────────────────────────────────────────────────────────

def test_root_returns_api_info():
    r = requests.get(f"{BASE}/", timeout=10)
    assert r.status_code == 200
    assert "message" in r.json()


# ── registration ─────────────────────────────────────────────────────────────

def test_register_new_user_succeeds():
    r = _register(_new_user())
    assert r.status_code == 200
    assert r.json().get("status") == "success"


def test_register_duplicate_username_is_rejected():
    user = _new_user()
    _register(user)
    r = _register(user)
    assert r.status_code != 200 or r.json().get("status") != "success"


def test_register_requires_all_fields():
    r = requests.post(f"{BASE}/users/v1/register",
                      json={"username": "nobody"}, timeout=10)  # missing password/email
    assert r.status_code >= 400


# ── login ────────────────────────────────────────────────────────────────────

def test_login_returns_token():
    user = _new_user()
    _register(user)
    r = _login(user)
    assert r.status_code == 200
    assert r.json().get("auth_token")


def test_login_wrong_password_rejected():
    user = _new_user()
    _register(user)
    r = requests.post(f"{BASE}/users/v1/login",
                      json={"username": user["username"], "password": "WRONG"}, timeout=10)
    assert r.status_code == 401


# ── users ────────────────────────────────────────────────────────────────────

def test_list_users():
    r = requests.get(f"{BASE}/users/v1", timeout=10)
    assert r.status_code == 200
    assert "users" in r.json()


def test_get_user_by_username():
    user = _new_user()
    _register(user)
    r = requests.get(f"{BASE}/users/v1/{user['username']}", timeout=10)
    assert r.status_code == 200
    assert r.json().get("username") == user["username"]


def test_get_unknown_user_returns_404():
    r = requests.get(f"{BASE}/users/v1/no_such_user_{uuid.uuid4().hex[:6]}", timeout=10)
    assert r.status_code == 404


# ── me / auth-gated ──────────────────────────────────────────────────────────

def test_me_requires_token():
    r = requests.get(f"{BASE}/me", timeout=10)
    assert r.status_code == 401


def test_me_returns_own_profile():
    user = _new_user()
    token = _token(user)
    r = requests.get(f"{BASE}/me", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    assert r.status_code == 200
    assert user["username"] in r.text


# ── books ────────────────────────────────────────────────────────────────────

def test_create_and_list_books():
    user = _new_user()
    token = _token(user)
    title = "book_" + uuid.uuid4().hex[:6]
    r = requests.post(f"{BASE}/books/v1", json={"book_title": title, "secret": "s3cret"},
                      headers={"Authorization": f"Bearer {token}"}, timeout=10)
    assert r.status_code == 200
    r2 = requests.get(f"{BASE}/books/v1", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    assert r2.status_code == 200
    assert "Books" in r2.json()


def test_get_book_by_title_requires_token():
    r = requests.get(f"{BASE}/books/v1/whatever", timeout=10)
    assert r.status_code == 401


def test_create_book_requires_token():
    r = requests.post(f"{BASE}/books/v1", json={"book_title": "x", "secret": "y"}, timeout=10)
    assert r.status_code == 401
