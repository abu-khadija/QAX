"""
test_typed_probes.py — probes that need the spec's field types (2026-10-15)
=====================================================================================
boolean-false · single-element-array · date-order · long-input · duplicate-rows

Each probe is control-gated, so every test here pins BOTH sides: it fires on the
buggy app, and stays silent on the correct one (and when its positive control fails).
A fake in-process app stands in for the server — no network.
"""

import json
from unittest.mock import MagicMock

from execution.typed_probes import (_date_pairs, _tokens, _typed_body, boolean_false_probe,
                                    date_order_probe, duplicate_rows_probe, long_input_probe,
                                    single_element_array_probe)
from services.results import AppModel

_TYPES = {
    "title": {"type": "string"},
    "active": {"type": "boolean"},
    "labels": {"type": "array", "items_type": "string"},
    "starts_at": {"type": "string", "format": "date-time"},
    "ends_at": {"type": "string", "format": "date-time"},
    "id": {"type": "integer", "readOnly": True},
}


def _model(auth_required=False, **extra):
    ep = {"method": "POST", "path": "/things", "auth_required": auth_required,
          "request_fields": list(_TYPES), "required_fields": ["title"], "request_field_types": _TYPES}
    ep.update(extra)
    return AppModel(root="/x", endpoints=[ep])


class _App:
    """A tiny create API whose bugs are switchable."""

    def __init__(self, bool_false="ok", array1="ok", dates="reject", long="ok", reject_all=False):
        self.bool_false, self.array1, self.dates, self.long, self.reject_all = \
            bool_false, array1, dates, long, reject_all
        self.calls, self._qax_skips = [], []

    @staticmethod
    def _resp(code, body):
        return MagicMock(status_code=code, text=json.dumps(body), json=lambda: body)

    def post(self, url, json=None, headers=None, timeout=None):
        body = dict(json or {})
        self.calls.append((url, body))
        if self.reject_all:
            return self._resp(422, {"message": "nope"})
        if body.get("active") is False:
            if self.bool_false == "required":
                return self._resp(422, {"message": "The active field is required."})
            if self.bool_false == "crash":
                return self._resp(500, {"message": "boom"})
        if isinstance(body.get("labels"), list) and len(body["labels"]) == 1 and self.array1 == "reject":
            return self._resp(400, {"message": "labels must be an array"})
        s, e = body.get("starts_at"), body.get("ends_at")
        if s and e and e < s and self.dates == "reject":
            return self._resp(422, {"message": "ends_at must be after starts_at"})
        title = body.get("title", "")
        if len(title) > 1000:
            if self.long == "crash":
                return self._resp(500, {"message": "value too long for column"})
            if self.long == "truncate":
                body["title"] = title[:200]
        return self._resp(201, {"id": len(self.calls), **body})


def _kw(app, **auth):
    return dict(session=app, auth=auth or None)


# ── helpers ──────────────────────────────────────────────────────────────────

def test_tokens_split_snake_and_camel():
    assert _tokens("start_date") == ["start", "date"]
    assert _tokens("endsAt") == ["ends", "at"]
    assert _tokens("a.b.starts_at") == ["starts", "at"]


def test_date_pairs_match_mirrored_names_only():
    t = {"start_date": {"type": "string", "format": "date"}, "end_date": {"type": "string", "format": "date"},
         "starts_at": {"type": "string", "format": "date-time"}, "closed_at": {"type": "string", "format": "date-time"},
         "from": {"type": "string", "format": "date"}, "to": {"type": "string", "format": "date"}}
    pairs = _date_pairs(t)
    assert ("start_date", "end_date") in pairs and ("from", "to") in pairs
    assert ("starts_at", "closed_at") not in pairs            # "closed_at" is not the mirror of "starts_at"
    assert ("start_date", "to") not in pairs                  # different shapes never pair


def test_typed_body_sends_required_only_skips_readonly_and_applies_overrides():
    b = _typed_body(_model().endpoints[0], {"active": False})
    assert set(b) == {"title", "active"} and b["active"] is False and b["title"].startswith("qax")
    # No required info recorded → every WRITABLE field, never the read-only one.
    ep = dict(_model().endpoints[0]); ep.pop("required_fields")
    assert "id" not in _typed_body(ep) and {"title", "active", "labels"} <= set(_typed_body(ep))


# ── boolean-false ────────────────────────────────────────────────────────────

def test_boolean_false_fires_when_false_is_rejected_as_required():
    out = boolean_false_probe(_model(), "http://x", **_kw(_App(bool_false="required")))
    assert len(out) == 1 and out[0].probe == "boolean-false" and out[0].severity == "P2"
    assert "cannot be set to false" in out[0].title and "required" in out[0].evidence


def test_boolean_false_fires_on_crash():
    out = boolean_false_probe(_model(), "http://x", **_kw(_App(bool_false="crash")))
    assert len(out) == 1 and "crashed with HTTP 500" in out[0].evidence


def test_boolean_false_silent_on_a_correct_app():
    assert boolean_false_probe(_model(), "http://x", **_kw(_App())) == []


def test_boolean_false_silent_when_the_true_control_fails():
    # The app rejects everything (e.g. a missing required field we can't fill) → can't judge.
    assert boolean_false_probe(_model(), "http://x", **_kw(_App(reject_all=True))) == []


def test_boolean_false_ignores_an_unrelated_4xx():
    class _Other(_App):
        def post(self, url, json=None, headers=None, timeout=None):
            if (json or {}).get("active") is False:
                return self._resp(403, {"message": "forbidden"})      # not a "required" message
            return super().post(url, json=json, headers=headers, timeout=timeout)
    assert boolean_false_probe(_model(), "http://x", **_kw(_Other())) == []


# ── single-element-array ─────────────────────────────────────────────────────

def test_single_element_array_fires():
    out = single_element_array_probe(_model(), "http://x", **_kw(_App(array1="reject")))
    assert len(out) == 1 and out[0].probe == "single-element-array" and out[0].severity == "P2"
    assert "exactly 1 item" in out[0].evidence


def test_single_element_array_silent_on_a_correct_app():
    assert single_element_array_probe(_model(), "http://x", **_kw(_App())) == []


def test_single_element_array_skips_when_spec_requires_two_items():
    types = {**_TYPES, "labels": {"type": "array", "items_type": "string", "min_items": 2}}
    m = _model(request_field_types=types)
    assert single_element_array_probe(m, "http://x", **_kw(_App(array1="reject"))) == []


# ── date-order ───────────────────────────────────────────────────────────────

def test_date_order_fires_when_end_before_start_is_stored():
    out = date_order_probe(_model(), "http://x", **_kw(_App(dates="accept")))
    assert len(out) == 1 and out[0].probe == "date-order" and out[0].severity == "P3"
    assert "BEFORE start" in out[0].evidence


def test_date_order_silent_when_reversed_dates_are_rejected():
    assert date_order_probe(_model(), "http://x", **_kw(_App(dates="reject"))) == []


def test_date_order_silent_when_the_ordered_control_is_rejected():
    assert date_order_probe(_model(), "http://x", **_kw(_App(reject_all=True))) == []


def test_date_order_silent_when_the_server_normalises_instead_of_storing():
    class _Swap(_App):
        def post(self, url, json=None, headers=None, timeout=None):
            b = dict(json or {})
            if b.get("starts_at") and b.get("ends_at") and b["ends_at"] < b["starts_at"]:
                b["starts_at"], b["ends_at"] = b["ends_at"], b["starts_at"]   # silently reorders
                return self._resp(201, b)
            return super().post(url, json=json, headers=headers, timeout=timeout)
    # The reversed pair comes back CROSSED (start=day 10, end=day 20): the server handled it.
    assert date_order_probe(_model(), "http://x", **_kw(_Swap())) == []


def test_date_order_silent_when_the_response_does_not_echo_the_dates():
    class _NoEcho(_App):
        def post(self, url, json=None, headers=None, timeout=None):
            r = super().post(url, json=json, headers=headers, timeout=timeout)
            return self._resp(r.status_code, {"id": 1}) if r.status_code < 300 else r
    assert date_order_probe(_model(), "http://x", **_kw(_NoEcho(dates="accept"))) == []   # inconclusive


# ── long-input ───────────────────────────────────────────────────────────────

def test_long_input_fires_on_a_5xx():
    out = long_input_probe(_model(), "http://x", **_kw(_App(long="crash")))
    assert len(out) == 1 and out[0].severity == "P2" and "Unhandled error" in out[0].title


def test_long_input_fires_on_silent_truncation():
    out = long_input_probe(_model(), "http://x", **_kw(_App(long="truncate")))
    assert len(out) == 1 and out[0].severity == "P3" and "silently truncated" in out[0].title


def test_long_input_silent_when_stored_in_full():
    assert long_input_probe(_model(), "http://x", **_kw(_App(long="ok"))) == []


def test_long_input_silent_when_the_short_control_fails():
    assert long_input_probe(_model(), "http://x", **_kw(_App(reject_all=True))) == []


def test_long_input_silent_when_the_response_does_not_echo_the_field():
    class _NoEcho(_App):
        def post(self, url, json=None, headers=None, timeout=None):
            r = super().post(url, json=json, headers=headers, timeout=timeout)
            return self._resp(r.status_code, {"id": 1}) if r.status_code < 300 else r
    assert long_input_probe(_model(), "http://x", **_kw(_NoEcho(long="truncate"))) == []   # inconclusive


# ── duplicate-rows ───────────────────────────────────────────────────────────

class _ListApp:
    def __init__(self, payload, code=200):
        self.payload, self.code, self._qax_skips = payload, code, []

    def get(self, url, headers=None, timeout=None):
        return MagicMock(status_code=self.code, text=json.dumps(self.payload),
                         json=lambda: self.payload)


def _list_model():
    return AppModel(root="/x", endpoints=[{"method": "GET", "path": "/items", "auth_required": False}])


def test_duplicate_rows_fires_on_a_repeated_id():
    out = duplicate_rows_probe(_list_model(), "http://x",
                               session=_ListApp([{"id": 1}, {"id": 2}, {"id": 1}]))
    assert len(out) == 1 and out[0].probe == "duplicate-rows" and "id=1" in out[0].title


def test_duplicate_rows_reads_a_wrapped_collection():
    out = duplicate_rows_probe(_list_model(), "http://x",
                               session=_ListApp({"items": [{"uuid": "a"}, {"uuid": "a"}]}))
    assert len(out) == 1 and "uuid=a" in out[0].title


def test_duplicate_rows_silent_on_unique_ids_and_non_lists():
    assert duplicate_rows_probe(_list_model(), "http://x", session=_ListApp([{"id": 1}, {"id": 2}])) == []
    assert duplicate_rows_probe(_list_model(), "http://x", session=_ListApp({"count": 3})) == []
    assert duplicate_rows_probe(_list_model(), "http://x", session=_ListApp([{"id": 1}, {"id": 1}], code=401)) == []


# ── the not-tested ledger: skips are reported, n/a is not a gap ───────────────

def test_probes_report_not_applicable_when_the_app_has_nothing_to_test():
    app = _App()
    m = AppModel(root="/x", endpoints=[{"method": "GET", "path": "/health"}])
    for probe in (boolean_false_probe, single_element_array_probe, date_order_probe, long_input_probe):
        assert probe(m, "http://x", session=app) == []
    kinds = {(s["probe"], s["kind"]) for s in app._qax_skips}
    assert {("boolean-false", "not-applicable"), ("single-element-array", "not-applicable"),
            ("date-order", "not-applicable"), ("long-input", "not-applicable")} <= kinds


def test_probes_report_a_gap_when_auth_is_needed_but_unavailable():
    class _NoLogin(_App):
        def get(self, *a, **k):
            return MagicMock(status_code=404, text="", json=lambda: {})
    app = _NoLogin()
    assert boolean_false_probe(_model(auth_required=True), "http://x", session=app) == []
    gaps = [s for s in app._qax_skips if s["kind"] == "missing-prerequisite"]
    assert gaps and gaps[0]["probe"] == "boolean-false" and "no token" in gaps[0]["reason"]
    # QAX tried to self-register (its own /auth call), but never POSTed to the auth'd endpoint.
    assert not [c for c in app.calls if c[0].endswith("/things")]


def test_typed_probes_register_their_records_for_cleanup_and_are_mutating():
    from execution.probes import MUTATING_PROBES
    assert {"boolean-false", "single-element-array", "date-order", "long-input"} <= MUTATING_PROBES
    assert "duplicate-rows" not in MUTATING_PROBES            # read-only
    app = _App(); app._qax_created = []
    boolean_false_probe(_model(), "http://x", session=app)
    assert app._qax_created and app._qax_created[0]["collection"] == "/things"


# ── learn-from-rejection control + honest "could not exercise" reporting ─────────

class _PickyApp(_App):
    """Like real servers: the spec says only `title` is required, but a create also needs `owner`."""
    def post(self, url, json=None, headers=None, timeout=None):
        if not (json or {}).get("owner"):
            self.calls.append((url, dict(json or {})))
            return self._resp(422, {"message": "OwnerIsRequired"})
        return super().post(url, json=json, headers=headers, timeout=timeout)


_PICKY_TYPES = {**_TYPES, "owner": {"type": "string"}}


def test_control_learns_a_field_the_server_says_it_needs():
    app = _PickyApp(bool_false="required")
    m = _model(request_field_types=_PICKY_TYPES)
    out = boolean_false_probe(m, "http://x", **_kw(app))
    assert len(out) == 1 and out[0].probe == "boolean-false"        # control repaired → attack reached
    # The learned field rides along on the attack request too (same body shape as the control).
    assert all(c[1].get("owner") for c in app.calls[-2:])


def test_learning_adds_only_fields_the_spec_lists():
    app = _App()
    class _Weird(_App):
        def post(self, url, json=None, headers=None, timeout=None):
            return self._resp(422, {"message": "frobnicator is required"})   # not in the spec
    assert boolean_false_probe(_model(), "http://x", **_kw(_Weird())) == []
    w = _Weird()
    boolean_false_probe(_model(), "http://x", session=w)
    assert not any("frobnicator" in c[1] for c in w.calls)


def test_unexercised_probe_says_so_instead_of_going_quiet():
    app = _App(reject_all=True)                  # every control is rejected, no hint to learn from
    for probe, name in ((boolean_false_probe, "boolean-false"), (single_element_array_probe, "single-element-array"),
                        (date_order_probe, "date-order"), (long_input_probe, "long-input")):
        assert probe(_model(), "http://x", **_kw(app)) == []
        gaps = [s for s in app._qax_skips if s["probe"] == name and s["kind"] == "missing-prerequisite"]
        assert gaps and "positive control was rejected" in gaps[0]["reason"], name


def test_exercised_probe_does_not_report_a_gap():
    app = _App()                                  # a correct app: control passes, attack is handled
    boolean_false_probe(_model(), "http://x", **_kw(app))
    assert not [s for s in app._qax_skips if s["probe"] == "boolean-false"]


def test_only_real_creates_are_registered_for_cleanup():
    from execution.typed_probes import _Ctx
    mk = lambda code, body: MagicMock(status_code=code, json=lambda: body)
    assert _Ctx._created_something(mk(201, {"x": 1})) is True
    assert _Ctx._created_something(mk(200, {"id": 7})) is True
    assert _Ctx._created_something(mk(200, "<p>rendered markdown</p>")) is False   # a preview wrote nothing
    assert _Ctx._created_something(mk(200, {"html": "<p>x</p>"})) is False
