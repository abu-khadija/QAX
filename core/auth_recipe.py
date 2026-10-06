"""
core/auth_recipe.py — derive an app's auth flow from the AppModel
================================================================
The probes and coverage templates used to hard-code TaskFlow's auth shape
(/auth/register, /auth/login, an "email"+"password" body, an "access_token"
response field, a JWT subject that is the email). That only worked on TaskFlow.

This derives those from the whole-system AppModel instead — endpoint paths,
request/response field names, the identifier and secret fields — with sensible
fallbacks — so the exploit probes and the pagination template generalise to any
app. Pure logic, no dependencies, no model calls.
"""

from __future__ import annotations


def field_names(fields) -> list[str]:
    """Normalise a request/response field list to plain names."""
    out: list[str] = []
    for f in fields or []:
        if isinstance(f, str):
            out.append(f)
        elif isinstance(f, dict) and f.get("name"):
            out.append(str(f["name"]))
    return out


def _find(model, *keywords: str, method: str | None = None):
    """First endpoint whose path contains any keyword (optional method)."""
    for e in getattr(model, "endpoints", []):
        if not isinstance(e, dict):
            continue
        if method and str(e.get("method", "")).upper() != method:
            continue
        if any(k in str(e.get("path", "")).lower() for k in keywords):
            return e
    return None


def derive_auth_recipe(model) -> dict:
    """
    Infer the app's auth flow from the model.

    Returns a dict:
        register_path, login_path, identity_path,
        id_field, secret_field, token_field.
    Fields fall back to the common REST conventions when the model is silent.
    """
    _SECRET_KW = ("password", "passwd", "pwd", "secret", "pin", "pass")
    _ID_KW = ("email", "username", "login", "phone", "mobile", "msisdn", "user")

    def _has_secret_field(e) -> bool:
        return any(any(k in n.lower() for k in _SECRET_KW)
                   for n in field_names(e.get("request_fields")))

    login = None
    for e in getattr(model, "endpoints", []):
        if not isinstance(e, dict) or str(e.get("method", "")).upper() != "POST":
            continue
        p = str(e.get("path", "")).lower()
        if any(k in p for k in ("register", "signup", "sign-up")):
            continue
        if any(k in p for k in ("login", "signin", "sign-in", "token", "authenticate", "session")):
            login = e
            break

    register = _find(model, "register", "signup", "sign-up", "account", method="POST")
    if register is None or register is login:
        # Fallback: a POST endpoint (not the login) that takes a secret field —
        # that's the credential-creating endpoint whatever it's called.
        for e in getattr(model, "endpoints", []):
            if (isinstance(e, dict) and str(e.get("method", "")).upper() == "POST"
                    and e is not login and _has_secret_field(e)):
                register = e
                break

    identity = _find(model, "/me", "profile", "current", "whoami", method="GET")

    reg_fields = field_names(register.get("request_fields")) if register else []
    id_field = next((f for f in reg_fields if any(k in f.lower() for k in _ID_KW)), None)
    secret_field = next((f for f in reg_fields if any(k in f.lower() for k in _SECRET_KW)), None)
    login_resp = field_names(login.get("response_fields")) if login else []
    token_field = next((f for f in login_resp if any(
        k in f.lower() for k in ("token", "jwt", "access", "bearer", "auth"))), None)

    return {
        "register_path": (register or {}).get("path") or "/auth/register",
        "login_path": (login or {}).get("path") or "/auth/login",
        "identity_path": (identity or {}).get("path") or "/auth/me",
        "id_field": id_field or "email",
        "secret_field": secret_field or "password",
        "token_field": token_field or "access_token",
    }


def register_body(recipe: dict, model, identity_value: str,
                  password: str = "Secret123!") -> dict:
    """
    Build a registration body that fills ALL of the register endpoint's required
    fields, not just id + secret. Apps reject a partial body (e.g. VAmPI requires
    `email` too), so a probe/template that sends only username+password fails to
    even create its user. Values are type-sensible dummies.
    """
    reg_fields: list[str] = []
    for e in getattr(model, "endpoints", []):
        if (isinstance(e, dict) and str(e.get("path", "")) == recipe["register_path"]
                and str(e.get("method", "")).upper() == "POST"):
            reg_fields = field_names(e.get("request_fields"))
            break

    body = {recipe["id_field"]: identity_value, recipe["secret_field"]: password}
    for f in reg_fields:
        if f in body:
            continue
        lf = f.lower()
        if "email" in lf:
            body[f] = identity_value if "@" in str(identity_value) else f"{identity_value}@example.com"
        elif any(k in lf for k in ("phone", "mobile", "msisdn")):
            body[f] = "+10000000000"
        elif any(k in lf for k in ("name", "first", "last", "title")):
            body[f] = "Test"
        elif any(k in lf for k in ("age", "count", "number", "qty", "id")):
            body[f] = 1
        else:
            body[f] = "test"
    return body


def subject_candidates(victim_id_value: str, register_response: dict | None) -> list:
    """
    Candidate values for a forged JWT's subject claim, most-likely first.

    Different apps put the identity in `sub` differently (the email, the numeric
    id, a username). We try the login identifier, then anything id-like from the
    registration response.
    """
    out: list = [victim_id_value]
    if isinstance(register_response, dict):
        for key in ("id", "user_id", "uuid", "sub", "username"):
            val = register_response.get(key)
            if val is not None and val not in out:
                out.append(val)
    # local-part of an email is a common username fallback
    if isinstance(victim_id_value, str) and "@" in victim_id_value:
        local = victim_id_value.split("@", 1)[0]
        if local not in out:
            out.append(local)
    return out
