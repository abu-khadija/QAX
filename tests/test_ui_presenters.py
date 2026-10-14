"""S8: pure UI formatting helpers extracted from ui/app.py (no Streamlit needed)."""
from ui.presenters import (badge, build_markdown_export, confidence_bar, count_todos,
                           mask_secret, risk_badge_class, score_badge_class)


def test_badge_and_bar():
    assert badge("Hi", "green") == '<span class="badge badge-green">Hi</span>'
    assert 'width:90%' in confidence_bar(9) and '#22c55e' in confidence_bar(9)
    assert 'width:0%' in confidence_bar(-3)       # clamped, no crash


def test_badge_classes_by_band():
    assert [risk_badge_class(r) for r in ("critical", "high", "medium", "low", "?")] == \
        ["red", "amber", "teal", "gray", "gray"]
    assert [score_badge_class(s) for s in (9, 7.5, 6, 2)] == ["green", "teal", "amber", "red"]


def test_mask_secret():
    assert mask_secret("") == "(not set)"
    m = mask_secret("sk-abcdef1234")
    assert m.startswith("sk-a") and "•" in m and "abcdef" not in m


def test_count_todos(tmp_path):
    f = tmp_path / "f.py"; f.write_text("# TODO a\nx=1\n# TODO b\n")
    assert count_todos(str(f)) == 2
    assert count_todos(str(tmp_path / "missing.py")) == 0


def test_build_markdown_export_has_titles(tmp_path):
    md = build_markdown_export([{"test_case_id": "TC-1", "title": "Login works",
                                 "module": "auth", "test_type": "api", "confidence_score": 8,
                                 "manual_steps": [{"step_number": 1, "action": "open"}]}])
    assert "Login works" in md and "auth" in md   # reporter lists by title + module
