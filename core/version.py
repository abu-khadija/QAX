"""
core/version.py — QAX's version, and the run-record fields every scan output carries
====================================================================================
PLAN.md §6 (per-run record): every evidence run records the QAX version and commit, and the mode it ran
in, so a result can always be traced back to exactly what produced it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

__version__ = "0.1.0"

_ROOT = Path(__file__).resolve().parent.parent


def commit() -> str | None:
    """The short git commit of this checkout (+ "-dirty" with uncommitted changes); None outside git."""
    try:
        sha = subprocess.run(["git", "-C", str(_ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        if sha.returncode != 0:
            return None
        dirty = subprocess.run(["git", "-C", str(_ROOT), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=5)
        return sha.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")
    except Exception:
        return None


def run_record(**options) -> dict:
    """{"qax_version", "qax_commit", "options"} — options must never contain secrets (pass flags/booleans)."""
    return {"qax_version": __version__, "qax_commit": commit(), "options": dict(options)}
