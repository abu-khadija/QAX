"""
test_tour.py — TourService + TourResult (the live tour)
=======================================================
Offline tests. The real browser tour (execution.live_tour.tour_app) is mocked, so
these run without Playwright — including on Python 3.14 where Playwright is absent.

Run with:
    pytest tests/test_tour.py -v
"""

from unittest.mock import patch

from services.context import QAXContext
from services.results import TourResult
from services.tour_service import TourService


def _ctx() -> QAXContext:
    return QAXContext(anthropic_key="k", project="")


def _sample_raw() -> dict:
    return {
        "base_url": "http://localhost:5173",
        "login_ok": True,
        "error": None,
        "screens": [
            {"url": "http://localhost:5173/dashboard", "title": "TaskFlow",
             "elements": [
                 {"tag": "input", "type": "search", "testid": "search",
                  "selector": '[data-testid="search"]', "label": "Search todos"},
                 {"tag": "button", "type": "button", "testid": "todo-form-submit",
                  "selector": '[data-testid="todo-form-submit"]', "label": "Add todo"},
             ],
             "errors": ["console.error: boom"]},
            {"url": "http://localhost:5173/categories", "title": "TaskFlow",
             "elements": [], "errors": []},
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────

def test_empty_url_is_error():
    result = TourService(_ctx()).tour("")
    assert not result.success
    assert result.screens == []


def test_playwright_missing_is_captured_not_raised():
    with patch("execution.live_tour.PLAYWRIGHT_AVAILABLE", False):
        result = TourService(_ctx()).tour("http://localhost:5173")
    assert not result.success
    assert "Playwright" in (result.error or "")


def test_tour_maps_raw_into_result():
    with patch("execution.live_tour.PLAYWRIGHT_AVAILABLE", True), \
         patch("execution.live_tour.tour_app", return_value=_sample_raw()):
        result = TourService(_ctx()).tour(
            "http://localhost:5173", login={"email": "a@b.com", "password": "x"})
    assert result.success
    assert result.screen_count == 2
    assert result.element_count == 2
    assert result.login_ok is True
    assert len(result.console_errors) == 1
    assert result.console_errors[0]["url"].endswith("/dashboard")


def test_as_context_surfaces_real_selectors():
    result = TourResult(**{k: v for k, v in _sample_raw().items()
                           if k in ("base_url", "screens", "login_ok", "error")})
    ctx = result.as_context()
    assert 'data-testid="todo-form-submit"' in ctx
    assert "/dashboard" in ctx
    assert "OBSERVED UI SURFACE" in ctx


def test_browser_failure_is_captured():
    with patch("execution.live_tour.PLAYWRIGHT_AVAILABLE", True), \
         patch("execution.live_tour.tour_app", side_effect=RuntimeError("browser died")):
        result = TourService(_ctx()).tour("http://localhost:5173")
    assert not result.success
    assert "browser died" in (result.error or "")


def test_as_context_empty_when_no_screens():
    assert TourResult(base_url="x").as_context() == ""
