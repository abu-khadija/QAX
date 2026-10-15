"""
execution/typed_probes.py — probes that need the spec's FIELD TYPES
====================================================================
Mined from the owner's QA knowledge base (files 05 + 11, 2026-10-15). Each of these
bug classes was a real, recurring defect in the owner's job — and each is only
provable without guessing once the live OpenAPI spec says what a field IS
(boolean / array / date / bounded string), which QAX's spec grounding now records
as `request_field_types`:

  boolean-false         unticking a checkbox is rejected "field is required" / crashes
  single-element-array  a multi-select works with 2 values but fails with exactly 1
  date-order            END before START is accepted and stored
  long-input            a 5,000-char value crashes the server (5xx) or is silently cut
  duplicate-rows        one record appears twice in a collection response

Every probe is zero-LLM and control-gated: it reports ONLY when its positive control
(the same request with the benign variant) succeeded, so a failure to even create the
record is never misread as a bug. Probes that write add themselves to the
MUTATING_PROBES set and register their disposable records for cleanup. A probe that
cannot run says why via _skip() — never a silent [].
"""

from __future__ import annotations

import re
import uuid

import requests

from execution.probes import _auth_headers, _record_created, _resolve_token, _skip
from services.results import ProbeFinding

# Text-ish fields where a very long value is plausible (and not a secret/key).
_TEXT_FIELD_KW = ("title", "name", "description", "text", "body", "note", "comment",
                  "summary", "content", "message", "bio", "details")
_NEVER_STUFF = ("password", "passwd", "secret", "token", "key", "hash", "email", "url", "uri",
                "login", "username", "slug", "path")
_LONG = 5000
# A rejection message that is naming a missing/invalid field.
_REQ_HINT = re.compile(r"required|missing|empty|blank|must|cannot be|can't be|invalid", re.I)

# start/end vocabulary for the date-order probe (whole-word tokens of a field name).
_START_TOKENS = ("start", "starts", "begin", "begins", "from", "opens", "open")
_END_TOKENS = ("end", "ends", "finish", "finishes", "until", "to", "close", "closes", "due", "expires")


# ── shared helpers ───────────────────────────────────────────────────────────

def _is2xx(r) -> bool:
    return r is not None and 200 <= getattr(r, "status_code", 0) < 300


def _tokens(name: str) -> list[str]:
    """Split a field name into lowercase word tokens (snake_case and camelCase)."""
    leaf = str(name).rsplit(".", 1)[-1]
    leaf = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", leaf)
    return [t for t in re.split(r"[^A-Za-z0-9]+", leaf.lower()) if t]


def _creates(model):
    """POST endpoints with no path parameter and spec-typed request fields (collection creates)."""
    for e in getattr(model, "endpoints", []) or []:
        if not (isinstance(e, dict) and str(e.get("method", "")).upper() == "POST"):
            continue
        if "{" in str(e.get("path", "")):
            continue
        if not e.get("request_field_types"):
            continue
        yield e


def _typed_value(name: str, info: dict, uniq: str):
    """A benign, type-correct value for one field (unique where a collision could matter)."""
    t, fmt = info.get("type"), info.get("format")
    leaf = str(name).lower().rsplit(".", 1)[-1]
    if info.get("enum"):
        return info["enum"][0]
    if t == "boolean":
        return True
    if t in ("integer", "number"):
        return 1
    if t == "array":
        return []
    if t == "object":
        return {}
    if "email" in leaf or fmt == "email":
        return f"qax{uniq}@example.com"
    if any(k in leaf for k in ("password", "passwd", "secret", "pwd")):
        return "Secret123!"
    if fmt == "date-time":
        return "2020-01-01T00:00:00Z"
    if fmt == "date":
        return "2020-01-01"
    if fmt in ("uri", "url") or any(k in leaf for k in ("url", "website", "link")):
        return "https://example.com/qax"
    return f"qax{uniq}"


def _typed_body(endpoint: dict, overrides: dict | None = None) -> dict:
    """
    A valid-looking create body from the spec: the REQUIRED fields (or every writable
    field when the model recorded no required info) filled with type-correct values,
    plus `overrides`. Read-only fields are never sent.
    """
    types = endpoint.get("request_field_types") or {}
    overrides = overrides or {}
    uniq = uuid.uuid4().hex[:8]
    if "required_fields" in endpoint:
        wanted = [f for f in (endpoint.get("required_fields") or []) if f in types]
    else:
        wanted = list(types)
    body: dict = {}
    for f in wanted:
        if types[f].get("readOnly"):
            continue
        body[f] = _typed_value(f, types[f], uniq)
    for f, v in overrides.items():
        body[f] = v
    return body


def _json_values(resp, field: str) -> list:
    """Every value stored under `field` (case-insensitive key) anywhere in a JSON response."""
    try:
        data = resp.json()
    except Exception:
        return []
    found: list = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if str(k).lower() == field.lower():
                    found.append(v)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return found


class _Ctx:
    """Per-probe plumbing: session, base URL, token resolution and a safe POST."""

    def __init__(self, model, base_url, session, auth, tag: str):
        self.sess = session or requests.Session()
        self.base = base_url.rstrip("/")
        self.model, self.auth, self.tag = model, auth, tag
        self._token = None
        self._resolved = False
        self.extras = {}
        self.exercised = 0       # candidates whose positive control succeeded

    def token(self):
        if not self._resolved:
            self._resolved = True
            from core.auth_recipe import derive_auth_recipe
            self._token = _resolve_token(self.sess, self.base, derive_auth_recipe(self.model),
                                         self.model, f"{self.tag}_{uuid.uuid4().hex[:8]}@example.com",
                                         auth=self.auth)
        return self._token

    def headers(self, endpoint: dict):
        """Auth headers for an endpoint, or None when it needs auth we couldn't get."""
        if not endpoint.get("auth_required"):
            return {}
        tok = self.token()
        if not tok:
            _skip(self.sess, self.tag, "the endpoints need auth, but no token was supplied and "
                                       "self-registration/login failed", needs="auth-token")
            return None
        return _auth_headers(tok, self.auth)

    def post(self, endpoint: dict, body: dict, headers: dict):
        """POST a body; on a 2xx register it for cleanup. Returns the response or None."""
        path = str(endpoint.get("path", ""))
        try:
            r = self.sess.post(f"{self.base}{path}", json=body, headers=headers, timeout=15)
        except Exception:
            return None
        if _is2xx(r) and self._created_something(r):
            _record_created(self.sess, path, body, headers)
        return r

    @staticmethod
    def _created_something(resp) -> bool:
        """
        True when a 2xx actually CREATED a record — HTTP 201/202, or a body carrying an id.
        A 200 from a rendering/echo endpoint (e.g. a markdown preview) wrote nothing, and
        counting it would inflate the "records created / cleaned" footprint QAX reports.
        """
        if getattr(resp, "status_code", 0) in (201, 202):
            return True
        try:
            data = resp.json()
        except Exception:
            return False
        return isinstance(data, dict) and any(k in data for k in ("id", "uuid", "pk", "_id", "ID"))

    def _learn(self, endpoint: dict, body: dict, resp) -> dict:
        """
        Fields the SERVER says it needs. Specs routinely under-declare `required` (a real
        server answered "PasswordIsRequired" for a field its own spec marks optional), so a
        control that fails on "<known field> … required/missing/empty" is retried once with
        that field filled. Only fields the spec lists are ever added — nothing is guessed.
        """
        text = (getattr(resp, "text", "") or "")[:600]
        if not _REQ_HINT.search(text):
            return {}
        flat = re.sub(r"[_\-\s]", "", text.lower())
        uniq = uuid.uuid4().hex[:8]
        types = endpoint.get("request_field_types") or {}
        learned = {}
        for f, info in types.items():
            if f in body or info.get("readOnly"):
                continue
            if re.sub(r"[_\-\s]", "", f.lower()) in flat:
                learned[f] = _typed_value(f, info, uniq)
        return learned

    def body(self, endpoint: dict, overrides: dict | None = None) -> dict:
        """A typed body = required fields + whatever this endpoint taught us + overrides."""
        learned = self.extras.get(str(endpoint.get("path", "")), {})
        return _typed_body(endpoint, {**learned, **(overrides or {})})

    def control(self, endpoint: dict, overrides: dict, headers: dict, max_repairs: int = 2):
        """
        The positive control: POST the benign variant; if the server rejects it naming a
        missing field, add that field and retry (≤ max_repairs). Returns the response.
        """
        path = str(endpoint.get("path", ""))
        r = None
        for _ in range(max_repairs + 1):
            body = self.body(endpoint, overrides)
            r = self.post(endpoint, body, headers)
            if r is None or _is2xx(r):
                return r
            learned = self._learn(endpoint, body, r)
            if not learned:
                return r
            self.extras.setdefault(path, {}).update(learned)
        return r

    def attack(self, endpoint: dict, overrides: dict, headers: dict):
        """POST the variant under test (same learned fields as the control)."""
        return self.post(endpoint, self.body(endpoint, overrides), headers)

    def note_unexercised(self, probe: str, tried: int) -> None:
        """No candidate's positive control succeeded → say so, never a silent clean result."""
        _skip(self.sess, probe, f"could not build a request the app accepts — the positive control was "
                                f"rejected on all {tried} candidate endpoint(s), so the probe could not "
                                f"test the attack variant", needs="valid-body")


# ── 1. boolean `false` rejected / crashes ────────────────────────────────────

def boolean_false_probe(model, base_url: str, *, session: requests.Session | None = None,
                        auth: dict | None = None, limit: int = 4) -> list[ProbeFinding]:
    """
    Unticking a checkbox must be savable. The owner's worked example: a
    non-nullable boolean made the framework add an implicit "required" rule, so
    `false` was rejected with "The … field is required." — the flag became
    permanent and every co-submitted edit was lost. Also its crash twin: `false` → 5xx.

    Control: the SAME create with the field = true must be accepted (2xx), proving the
    endpoint works. Report only when `false` is then (a) rejected with a 4xx whose body
    says the field is REQUIRED, or (b) answered with a 5xx. We do not claim a silent
    flip (false stored as true): a server may legitimately own such a field. P2.
    """
    ctx = _Ctx(model, base_url, session, auth, "boolean-false")
    cands = []
    for e in _creates(model):
        types = e["request_field_types"]
        bools = [f for f, i in types.items() if i.get("type") == "boolean" and not i.get("readOnly")]
        if bools:
            cands.append((e, bools))
    if not cands:
        _skip(ctx.sess, "boolean-false", "no create endpoint with a boolean field", kind="not-applicable")
        return []
    out: list[ProbeFinding] = []
    for e, bools in cands[:limit]:
        hdr = ctx.headers(e)
        if hdr is None:
            continue
        for fld in bools[:2]:
            if not _is2xx(ctx.control(e, {fld: True}, hdr)):
                continue                                   # control failed → can't judge
            ctx.exercised += 1
            rf = ctx.attack(e, {fld: False}, hdr)
            if rf is None or _is2xx(rf):
                continue
            txt = (rf.text or "")[:300]
            code = rf.status_code
            required_msg = 400 <= code < 500 and re.search(r"required", txt, re.I) \
                and fld.lower() in txt.lower()
            if required_msg or code >= 500:
                path = str(e.get("path"))
                why = (f"rejected it with HTTP {code} — \"{txt[:120].strip()}\"" if required_msg
                       else f"crashed with HTTP {code}")
                out.append(ProbeFinding(
                    probe="boolean-false",
                    title=f"Boolean '{fld}' cannot be set to false on POST {path}",
                    exploited=True, severity="P2",
                    evidence=(f"POST {path} with {fld}=true was accepted (control), but the identical "
                              f"request with {fld}=false {why}. A checkbox that can be ticked but not "
                              f"unticked makes the setting permanent (implicit-required boolean)."),
                    target=path))
                return out        # one proof per scan; this is a systemic binding bug
    if not ctx.exercised:
        ctx.note_unexercised("boolean-false", len(cands[:limit]))
    return out


# ── 2. single-element array ──────────────────────────────────────────────────

def single_element_array_probe(model, base_url: str, *, session: requests.Session | None = None,
                               auth: dict | None = None, limit: int = 4) -> list[ProbeFinding]:
    """
    Scalar-vs-array binding (a common model-binding regression):
    a multi-select works with TWO values but fails with exactly ONE, because the single
    value arrives as a scalar. Control: an array of 2 valid items is accepted (2xx);
    report only when the identical request with a 1-item array is then rejected (4xx) or
    crashes (5xx). Skipped where the spec itself demands ≥2 items. P2.
    """
    ctx = _Ctx(model, base_url, session, auth, "single-element-array")
    cands = []
    for e in _creates(model):
        for f, i in e["request_field_types"].items():
            if i.get("type") == "array" and not i.get("readOnly") and (i.get("min_items") or 0) <= 1:
                items = i.get("items_enum") or None
                kind = i.get("items_type")
                if kind in ("string", "integer", "number") and (items is None or len(items) >= 2):
                    cands.append((e, f, i))
    if not cands:
        _skip(ctx.sess, "single-element-array", "no create endpoint with an array field", kind="not-applicable")
        return []

    def make(info, n):
        if info.get("items_enum"):
            return list(info["items_enum"][:n])
        if info.get("items_type") in ("integer", "number"):
            return list(range(1, n + 1))
        tag = uuid.uuid4().hex[:6]
        return [f"qax{tag}{k}" for k in range(n)]

    out: list[ProbeFinding] = []
    for e, fld, info in cands[:limit]:
        hdr = ctx.headers(e)
        if hdr is None:
            continue
        if not _is2xx(ctx.control(e, {fld: make(info, 2)}, hdr)):
            continue                                       # control: 2 items must work
        ctx.exercised += 1
        r1 = ctx.attack(e, {fld: make(info, 1)}, hdr)
        if r1 is None or _is2xx(r1):
            continue
        path = str(e.get("path"))
        out.append(ProbeFinding(
            probe="single-element-array",
            title=f"Single-value '{fld}' fails on POST {path} (works with two)",
            exploited=True, severity="P2",
            evidence=(f"POST {path} accepted '{fld}' with 2 items (control) but rejected the identical "
                      f"request with exactly 1 item (HTTP {r1.status_code} — "
                      f"\"{(r1.text or '')[:100].strip()}\"). A multi-select that fails with one value is "
                      f"a scalar-vs-array binding bug."),
            target=path))
        return out
    if not ctx.exercised:
        ctx.note_unexercised("single-element-array", len(cands[:limit]))
    return out


# ── 3. date order ────────────────────────────────────────────────────────────

def _date_pairs(types: dict) -> list[tuple[str, str]]:
    """(start_field, end_field) pairs among date/date-time fields, by mirrored names."""
    dates = [f for f, i in types.items()
             if i.get("type") == "string" and i.get("format") in ("date", "date-time")
             and not i.get("readOnly")]

    def shape(toks):
        # replace the start/end word with a placeholder so "start_date" == "end_date"
        return ["_X_" if (t in _START_TOKENS or t in _END_TOKENS) else t for t in toks]

    pairs = []
    for s in dates:
        st = _tokens(s)
        if not any(t in _START_TOKENS for t in st):
            continue
        for e in dates:
            et = _tokens(e)
            if e == s or not any(t in _END_TOKENS for t in et):
                continue
            if shape(st) == shape(et):
                pairs.append((s, e))
    return pairs


def date_order_probe(model, base_url: str, *, session: requests.Session | None = None,
                     auth: dict | None = None, limit: int = 4) -> list[ProbeFinding]:
    """
    END before START (the owner's "date-order negative" in the audit,
    booking and scheduler suites). Control: an ordered pair (start < end) is accepted
    (2xx). Report only when the REVERSED pair (end < start) is also accepted AND both
    reversed values are echoed back unchanged — i.e. stored, not normalised. P3.
    """
    ctx = _Ctx(model, base_url, session, auth, "date-order")
    cands = []
    for e in _creates(model):
        for s, en in _date_pairs(e["request_field_types"]):
            cands.append((e, s, en))
    if not cands:
        _skip(ctx.sess, "date-order", "no create endpoint with a start/end date pair", kind="not-applicable")
        return []

    def val(info, day):
        return f"2020-01-{day:02d}T00:00:00Z" if info.get("format") == "date-time" else f"2020-01-{day:02d}"

    out: list[ProbeFinding] = []
    for e, s, en in cands[:limit]:
        hdr = ctx.headers(e)
        if hdr is None:
            continue
        types = e["request_field_types"]
        ordered = {s: val(types[s], 10), en: val(types[en], 20)}
        reversed_ = {s: val(types[s], 20), en: val(types[en], 10)}
        if not _is2xx(ctx.control(e, ordered, hdr)):
            continue                                       # control failed
        ctx.exercised += 1
        rr = ctx.attack(e, reversed_, hdr)
        if not _is2xx(rr):
            continue                                       # correctly rejected
        d_start, d_end = reversed_[s][:10], reversed_[en][:10]
        # Stored AS SENT: each date must come back in ITS OWN field. A server that silently
        # swaps the two (normalises) returns them crossed — handled, not a bug — so it fails here.
        got_s = [str(v)[:10] for v in _json_values(rr, s)]
        got_e = [str(v)[:10] for v in _json_values(rr, en)]
        if d_start in got_s and d_end in got_e:
            path = str(e.get("path"))
            out.append(ProbeFinding(
                probe="date-order",
                title=f"'{en}' before '{s}' accepted on POST {path}",
                exploited=True, severity="P3",
                evidence=(f"POST {path} accepted {s}={reversed_[s]} with {en}={reversed_[en]} (end "
                          f"BEFORE start) and echoed both back, after accepting an ordered pair as the "
                          f"control. An interval that ends before it starts is nonsense data."),
                target=path))
            return out
    if not ctx.exercised:
        ctx.note_unexercised("date-order", len(cands[:limit]))
    return out


# ── 4. very long input ───────────────────────────────────────────────────────

def long_input_probe(model, base_url: str, *, session: requests.Session | None = None,
                     auth: dict | None = None, limit: int = 4) -> list[ProbeFinding]:
    """
    A 5,000-character value in a free-text field ("long text (5,000+
    chars)"; "persists in full or truncates gracefully, not a raw error
    page"). Control: a short value is accepted. Report (a) a 5xx on the long value (an
    unhandled overflow), or (b) a 2xx whose response echoes the HEAD marker but not the
    TAIL marker (silently truncated — the user is told "saved" while data is cut).
    No echo of either marker is inconclusive → silent. 5xx = P2, silent truncation = P3.
    """
    ctx = _Ctx(model, base_url, session, auth, "long-input")
    cands = []
    for e in _creates(model):
        for f, i in e["request_field_types"].items():
            leaf = f.lower()
            if (i.get("type") == "string" and not i.get("format") and not i.get("enum")
                    and not i.get("readOnly") and any(k in leaf for k in _TEXT_FIELD_KW)
                    and not any(k in leaf for k in _NEVER_STUFF)):
                cands.append((e, f))
                break
    if not cands:
        _skip(ctx.sess, "long-input", "no create endpoint with a free-text field", kind="not-applicable")
        return []
    out: list[ProbeFinding] = []
    for e, fld in cands[:limit]:
        hdr = ctx.headers(e)
        if hdr is None:
            continue
        if not _is2xx(ctx.control(e, {fld: f"qaxshort{uuid.uuid4().hex[:6]}"}, hdr)):
            continue                                       # control: a short value works
        ctx.exercised += 1
        tag = uuid.uuid4().hex[:8]
        head, tail = f"QAXHEAD{tag}", f"QAXTAIL{tag}"
        long_val = head + ("A" * (_LONG - len(head) - len(tail))) + tail
        rl = ctx.attack(e, {fld: long_val}, hdr)
        if rl is None:
            continue
        path = str(e.get("path"))
        if rl.status_code >= 500:
            out.append(ProbeFinding(
                probe="long-input",
                title=f"Unhandled error on a {_LONG}-character '{fld}' on POST {path}",
                exploited=True, severity="P2",
                evidence=(f"POST {path} accepted a short '{fld}' (control) but answered HTTP "
                          f"{rl.status_code} to a {_LONG}-character value. Oversized input should be "
                          f"rejected with a clean 4xx (or stored), never an unhandled server error."),
                target=path))
            return out
        if _is2xx(rl):
            txt = rl.text or ""
            if head in txt and tail not in txt:
                out.append(ProbeFinding(
                    probe="long-input",
                    title=f"'{fld}' silently truncated on POST {path}",
                    exploited=True, severity="P3",
                    evidence=(f"POST {path} returned HTTP {rl.status_code} for a {_LONG}-character '{fld}' "
                              f"and echoed the start of the value but not its end — the text was cut "
                              f"without any error, so the user is told it was saved intact."),
                    target=path))
                return out
    if not ctx.exercised:
        ctx.note_unexercised("long-input", len(cands[:limit]))
    return out


# ── 5. duplicate rows in a collection ────────────────────────────────────────

_ID_KEYS = ("id", "uuid", "pk", "_id")


def duplicate_rows_probe(model, base_url: str, *, session: requests.Session | None = None,
                         auth: dict | None = None, limit: int = 30) -> list[ProbeFinding]:
    """
    One record listed twice ("lists show a record once, even with many
    pending items on it" — a join fan-out). READ-ONLY: GET each collection endpoint (no
    path params), and report when two items of the SAME response carry the same
    id/uuid. An identifier repeated within one collection is never legitimate. P3.
    """
    ctx = _Ctx(model, base_url, session, auth, "duplicate-rows")
    gets = [e for e in getattr(model, "endpoints", []) or []
            if isinstance(e, dict) and str(e.get("method", "")).upper() == "GET"
            and "{" not in str(e.get("path", ""))]
    if not gets:
        _skip(ctx.sess, "duplicate-rows", "no parameter-free collection GET", kind="not-applicable")
        return []
    out: list[ProbeFinding] = []
    for e in gets[:limit]:
        hdr = ctx.headers(e)
        if hdr is None:
            continue
        path = str(e.get("path"))
        try:
            r = ctx.sess.get(f"{ctx.base}{path}", headers=hdr, timeout=15)
            data = r.json() if _is2xx(r) else None
        except Exception:
            continue
        items = data
        if isinstance(data, dict):
            items = next((data[k] for k in ("items", "data", "results", "records", "rows")
                          if isinstance(data.get(k), list)), None)
        if not (isinstance(items, list) and len(items) >= 2):
            continue
        for key in _ID_KEYS:
            ids = [it.get(key) for it in items if isinstance(it, dict)
                   and isinstance(it.get(key), (str, int)) and it.get(key) != ""]
            if len(ids) < 2:
                continue
            dup = next((i for i in ids if ids.count(i) > 1), None)
            if dup is not None:
                out.append(ProbeFinding(
                    probe="duplicate-rows",
                    title=f"Duplicate rows in GET {path} ({key}={dup} listed more than once)",
                    exploited=True, severity="P3",
                    evidence=(f"GET {path} returned {len(items)} item(s) in which {key}={dup} appears "
                              f"{ids.count(dup)} times. A record must appear once in a collection — "
                              f"a duplicated id usually means a join fan-out."),
                    target=path))
                break
    return out
