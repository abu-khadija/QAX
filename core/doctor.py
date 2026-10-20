"""
core/doctor.py — is this machine ready to run QAX?
==================================================
Checks what a new user trips over, in the order they meet it: the Python version,
the packages, Playwright and its browser, git, a writable output folder, and which
integrations are configured (AI provider, Jira, Xray) — names only, never values.
Every check returns ok / warn / fail with the exact fix. Never raises.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

_PLACEHOLDER = ("your-", "sk-ant-your", "YOUR_")


def _set(name: str) -> bool:
    """True when an environment variable holds a real (non-placeholder) value."""
    v = os.getenv(name, "").strip()
    return bool(v) and not any(v.startswith(p) for p in _PLACEHOLDER)


def run_checks(root: str | None = None) -> list:
    """
    Run every readiness check.

    Args:
        root (str | None): The QAX folder (defaults to this checkout).

    Returns:
        list[dict]: [{"name", "status": "ok"|"warn"|"fail", "detail", "fix"}].
    """
    from core.paths import data_root
    base = Path(root or data_root())
    out = []

    def add(name, status, detail, fix=""):
        out.append({"name": name, "status": status, "detail": detail, "fix": fix})

    v = sys.version_info
    add("Python", "ok" if (3, 11) <= v[:2] <= (3, 12) else ("warn" if v[:2] > (3, 12) else "fail"),
        f"{v.major}.{v.minor}.{v.micro}",
        "" if (3, 11) <= v[:2] <= (3, 12) else ("Use Python 3.11 or 3.12 (QAX needs 3.11+)." if v[:2] < (3, 11)
                                                else "Use Python 3.11 or 3.12 (Playwright 1.44 does not support newer)."))

    missing = [m for m in ("anthropic", "flask", "requests", "jsonschema", "dotenv", "openpyxl")
               if importlib.util.find_spec(m) is None]
    add("Packages", "fail" if missing else "ok", ", ".join(missing) + " missing" if missing else "all present",
        "make install   (or: pip install -r requirements.txt)" if missing else "")

    if importlib.util.find_spec("playwright") is None:
        add("Browser engine", "warn", "Playwright not installed — functional QA runs over HTTP only",
            "pip install playwright==1.44.0 && python -m playwright install chromium")
    else:
        cache = Path(os.getenv("PLAYWRIGHT_BROWSERS_PATH", "") or
                     (Path.home() / ("Library/Caches/ms-playwright" if sys.platform == "darwin"
                                     else ".cache/ms-playwright")))
        names = [p.name for p in cache.iterdir()] if cache.is_dir() else []
        has = any(n.startswith("chromium") for n in names)
        others = [b for b in ("firefox", "webkit") if any(n.startswith(b) for n in names)]
        add("Browser engine", "ok" if has else "warn",
            ("Playwright + Chromium" + (" + " + " + ".join(others) if others else "")) if has
            else "Playwright installed, Chromium not found",
            "" if has else "python -m playwright install chromium")
        if has and len(others) < 2:
            add("Other browsers", "warn", "Firefox / WebKit not installed — runs use Chromium",
                "python -m playwright install firefox webkit   (optional, ~200 MB)")

    add("git", "ok" if shutil.which("git") else "warn",
        "found" if shutil.which("git") else "not found — repository URLs cannot be cloned",
        "" if shutil.which("git") else "Install git, or give a local folder instead of a URL.")

    try:
        (base / "output").mkdir(exist_ok=True)
        probe = base / "output" / ".qax-write-check"
        probe.write_text("ok")
        probe.unlink()
        add("Output folder", "ok", str(base / "output"))
    except Exception as exc:  # noqa: BLE001
        add("Output folder", "fail", f"not writable: {exc}", "Run QAX from a folder you can write to.")

    from core.ai_provider import describe, provider
    if provider() == "openai":
        ok_ai = bool(os.getenv("QAX_AI_MODEL", "").strip())
        add("AI provider", "ok" if ok_ai else "warn", describe() + (" (reachability not checked)" if ok_ai else ""),
            "" if ok_ai else "Set QAX_AI_MODEL (and QAX_AI_BASE_URL / QAX_AI_API_KEY) in .env")
    else:
        add("AI provider", "ok" if _set("ANTHROPIC_API_KEY") else "warn",
            "Anthropic — ANTHROPIC_API_KEY set (credit not checked)" if _set("ANTHROPIC_API_KEY") else
            "not set — scans, map, runs and reports work; writing test cases needs an AI provider",
            "" if _set("ANTHROPIC_API_KEY") else
            "Add ANTHROPIC_API_KEY to .env — or QAX_AI_PROVIDER=openai with an OpenAI-compatible API or a local model")
    jira = all(_set(k) for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))
    add("Jira", "ok" if jira else "warn", "configured" if jira else "not configured — bugs are previewed (dry run)",
        "" if jira else "Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY in .env")
    xray = _set("XRAY_CLIENT_ID") and _set("XRAY_CLIENT_SECRET")
    add("Xray", "ok" if xray else "warn", "configured (never exercised on a real instance)" if xray
        else "not configured — test cases export to CSV/JSON/Excel", "" if xray else "Set XRAY_CLIENT_ID / XRAY_CLIENT_SECRET")
    live = os.getenv("QAX_ALLOW_LIVE_EXPORT", "false").lower() == "true"
    add("Live writes", "ok", "ENABLED on this server (Jira/Xray writes possible after confirmation)" if live
        else "off (dry run) — the safe default")
    return out


def print_report(checks: list) -> int:
    """Print the checks; return the exit code (1 when any check failed)."""
    icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}
    print("\n  QAX doctor\n")
    for c in checks:
        print(f"  {icon[c['status']]} {c['name']:<15} {c['detail']}")
        if c["fix"]:
            print(f"      → {c['fix']}")
    failed = [c for c in checks if c["status"] == "fail"]
    print("\n  " + ("Not ready: fix the ❌ lines above." if failed else "Ready. Start with:  python main.py demo   or   make web") + "\n")
    return 1 if failed else 0
