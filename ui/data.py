"""ui/data.py — data access for the UI, with no Streamlit import (S8, testable).

Pure functions that take `root` (the QAX project root). ui/app.py keeps the thin,
@st.cache_data-decorated wrappers that call these with QAX_ROOT.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def list_projects(root: str | Path) -> list[str]:
    """Project folder names under config/projects/, else the DEFAULT_PROJECT env, else []."""
    projects_dir = Path(root) / "config" / "projects"
    if projects_dir.exists():
        found = sorted(p.name for p in projects_dir.iterdir() if p.is_dir())
        if found:
            return found
    default = os.getenv("DEFAULT_PROJECT", "")
    return [default] if default else []


def load_all_test_cases(root: str | Path) -> list[dict]:
    """Every test case across output/test_cases_*.json (newest file first)."""
    all_cases: list[dict] = []
    for f in sorted((Path(root) / "output").glob("test_cases_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list):
                all_cases.extend(data)
        except Exception:
            continue
    return all_cases


def load_latest_test_cases(root: str | Path) -> list[dict]:
    """Test cases from the most recent output JSON, or [] when there are none."""
    files = sorted((Path(root) / "output").glob("test_cases_*.json"), reverse=True)
    if not files:
        return []
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def connection_status() -> dict[str, bool | str]:
    """Which services are configured via env (no API calls). dry_run defaults true."""
    return {
        "claude":  bool(os.getenv("ANTHROPIC_API_KEY")),
        "jira":    bool(os.getenv("JIRA_BASE_URL") and os.getenv("JIRA_EMAIL") and os.getenv("JIRA_API_TOKEN")),
        "xray":    bool(os.getenv("XRAY_CLIENT_ID") and os.getenv("XRAY_CLIENT_SECRET")),
        "dry_run": os.getenv("DRY_RUN", "true").lower() == "true",
    }
