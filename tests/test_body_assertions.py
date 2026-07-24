"""
test_body_assertions.py — APIRunner response-body assertions (expect_json)
==========================================================================
Covers the path resolver and the assertion pass that catches computed/persistence
bugs the status code hides (e.g. total_pages off by one).

Run with:
    pytest tests/test_body_assertions.py -v
"""

from execution.api_runner import APIRunner, _MISSING


# ── _resolve_path ────────────────────────────────────────────────────────────

def test_resolve_simple_key():
    assert APIRunner._resolve_path({"total": 5}, "total") == 5


def test_resolve_nested_and_index():
    data = {"items": [{"title": "a"}, {"title": "b"}]}
    assert APIRunner._resolve_path(data, "items.1.title") == "b"


def test_resolve_length():
    assert APIRunner._resolve_path({"items": [1, 2, 3]}, "items.length") == 3
    assert APIRunner._resolve_path({"items": [1, 2, 3]}, "items.#") == 3


def test_resolve_missing_paths():
    assert APIRunner._resolve_path({"a": 1}, "b") is _MISSING
    assert APIRunner._resolve_path({"items": [1]}, "items.5") is _MISSING
    assert APIRunner._resolve_path({"a": 1}, "a.b") is _MISSING


def test_resolve_value_none_is_not_missing():
    # A real null value must be distinguishable from a missing path.
    assert APIRunner._resolve_path({"x": None}, "x") is None


# ── _assert_body ─────────────────────────────────────────────────────────────

def _fresh():
    return {"status": "PASS", "error": None}


def test_assert_body_pass():
    r = _fresh()
    APIRunner._assert_body(r, {"total": 5, "total_pages": 3, "items": [1, 2, 3, 4, 5]},
                           {"total": 5, "total_pages": 3, "items.length": 5})
    assert r["status"] == "PASS"


def test_assert_body_catches_pagination_off_by_one():
    # The real TaskFlow bug: total_pages = floor(5/2)=2 but the oracle is ceil=3.
    r = _fresh()
    APIRunner._assert_body(r, {"total": 5, "total_pages": 2}, {"total": 5, "total_pages": 3})
    assert r["status"] == "FAIL"
    assert "total_pages" in r["error"]
    assert "expected 3" in r["error"] and "got 2" in r["error"]


def test_assert_body_missing_path_fails():
    r = _fresh()
    APIRunner._assert_body(r, {"total": 5}, {"total_pages": 3})
    assert r["status"] == "FAIL"
    assert "not found" in r["error"]


def test_assert_body_no_json_fails():
    r = _fresh()
    APIRunner._assert_body(r, None, {"total": 5})
    assert r["status"] == "FAIL"
    assert "JSON" in r["error"]


# ── rendered expect_json (control experiments) ───────────────────────────────

def test_render_sole_placeholder_keeps_type():
    # "{{my_id}}" alone must render to the raw int, not the string "190".
    assert APIRunner._render("{{my_id}}", {"my_id": 190}) == 190
    # embedded placeholder still renders as a string path.
    assert APIRunner._render("/todos/{{rid}}", {"rid": 7}) == "/todos/7"


def test_mass_assignment_control_passes_when_safe():
    # Safe app: the created record is owned by the attacker (my_id), not 999999.
    expect = APIRunner._render({"user_id": "{{my_id}}"}, {"my_id": 190})
    r = _fresh()
    APIRunner._assert_body(r, {"user_id": 190, "title": "x"}, expect)
    assert r["status"] == "PASS"            # no false alarm


def test_mass_assignment_control_fails_when_vulnerable():
    # Vulnerable app: the injected user_id actually took effect.
    expect = APIRunner._render({"user_id": "{{my_id}}"}, {"my_id": 190})
    r = _fresh()
    APIRunner._assert_body(r, {"user_id": 999999, "title": "x"}, expect)
    assert r["status"] == "FAIL"
    assert "user_id" in r["error"]
