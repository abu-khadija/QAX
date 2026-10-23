"""test_doctor_browser.py — doctor finds Playwright's browsers on every OS and proves Chromium starts."""

from __future__ import annotations

import sys
from pathlib import Path

import core.doctor as doctor


def test_windows_browsers_live_under_localappdata(monkeypatch):
    # Regression (Windows, 2026-10-23): doctor looked in ~/.cache/ms-playwright and said
    # "Chromium not found" right after `playwright install chromium` succeeded.
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/u/AppData/Local")
    assert doctor._browsers_dir() == Path("C:/Users/u/AppData/Local") / "ms-playwright"


def test_the_override_and_the_other_systems(monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert doctor._browsers_dir() == tmp_path
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    monkeypatch.setattr(sys, "platform", "linux")
    assert doctor._browsers_dir() == Path.home() / ".cache/ms-playwright"


def test_an_installed_chromium_that_does_not_start_is_a_failure(monkeypatch, tmp_path):
    # Regression (Windows, 2026-10-23): the folder existed, so doctor said ready, but every
    # browser run died with "BrowserType.launch: spawn UNKNOWN".
    (tmp_path / "chromium-1117").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(doctor, "_chromium_launch_error", lambda timeout=45: "BrowserType.launch: spawn UNKNOWN")
    check = {c["name"]: c for c in doctor.run_checks()}["Browser engine"]
    assert check["status"] == "fail" and "spawn UNKNOWN" in check["detail"]


def test_a_chromium_that_starts_is_ready(monkeypatch, tmp_path):
    (tmp_path / "chromium-1117").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(doctor, "_chromium_launch_error", lambda timeout=45: "")
    assert {c["name"]: c for c in doctor.run_checks()}["Browser engine"]["status"] == "ok"
