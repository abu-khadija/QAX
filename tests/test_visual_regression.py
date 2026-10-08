"""
test_visual_regression.py — screenshot diff + baseline logic (uses Pillow)
"""

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from services.visual_regression import compare_images, visual_findings  # noqa: E402


def _img(path, size=(100, 100), color=(255, 255, 255), box=None, boxcolor=(0, 0, 0)):
    im = Image.new("RGB", size, color)
    if box:
        for x in range(box[0], box[2]):
            for y in range(box[1], box[3]):
                im.putpixel((x, y), boxcolor)
    im.save(path)
    return str(path)


def test_identical_images(tmp_path):
    a = _img(tmp_path / "a.png"); b = _img(tmp_path / "b.png")
    assert compare_images(a, b)["status"] == "identical"


def test_changed_images_write_diff(tmp_path):
    a = _img(tmp_path / "a.png")
    b = _img(tmp_path / "b.png", box=(0, 0, 60, 60))  # ~36% of pixels black
    res = compare_images(a, b, diff_path=str(tmp_path / "diff.png"))
    assert res["status"] == "changed" and res["pct"] > 0.5
    assert (tmp_path / "diff.png").exists()


def test_dimension_mismatch(tmp_path):
    a = _img(tmp_path / "a.png", size=(100, 100))
    b = _img(tmp_path / "b.png", size=(120, 100))
    assert compare_images(a, b)["status"] == "dimension_mismatch"


def test_visual_findings_seeds_baseline_then_detects(tmp_path):
    base = tmp_path / "baseline"
    cur1 = tmp_path / "run1"; cur1.mkdir()
    _img(cur1 / "screen_00.png")
    # first run: no baseline -> seed it, no findings
    assert visual_findings(str(base), str(cur1)) == []
    assert (base / "screen_00.png").exists()
    # second run: a changed screen -> one P3 visual finding
    cur2 = tmp_path / "run2"; cur2.mkdir()
    _img(cur2 / "screen_00.png", box=(0, 0, 70, 70))
    out = visual_findings(str(base), str(cur2), diff_dir=str(tmp_path / "diff"))
    assert len(out) == 1 and out[0]["source"] == "visual-regression" and out[0]["severity"] == "P3"
    assert "visual change" in out[0]["title"].lower()
