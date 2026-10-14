"""S5: every third-party module the primary UI imports must be declared in
requirements.txt, so a clean `pip install -r requirements.txt` can launch it."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _declared_names() -> set:
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(re.split(r"[<>=!\[ ]", line, 1)[0].lower())
    return names


def test_ui_third_party_imports_are_declared():
    declared = _declared_names()
    # import-name → distribution name (only where they differ)
    dist = {"plotly": "plotly", "pandas": "pandas", "streamlit": "streamlit"}
    src = (ROOT / "ui" / "app.py").read_text(encoding="utf-8")
    needed = set()
    for m in re.finditer(r"^\s*(?:import|from)\s+(streamlit|plotly|pandas)\b", src, re.M):
        needed.add(dist[m.group(1)])
    assert needed, "expected the UI to import streamlit/plotly/pandas"
    missing = sorted(n for n in needed if n not in declared)
    assert not missing, f"UI imports not declared in requirements.txt: {missing}"
