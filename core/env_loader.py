"""
core/env_loader.py — load .env without overriding what the operator set
========================================================================
`.env` fills in variables that are missing or EMPTY in the environment (the reason the
old code used load_dotenv(override=True): an empty `ANTHROPIC_API_KEY=` exported by a
shell must not hide the key in .env). It never replaces a non-empty value: before, a
`.env` with DRY_RUN=false silently overrode `DRY_RUN=true` set on the command line or in
CI — a safety switch the operator could not trust.
"""

from __future__ import annotations

import os


def load_env(path: str | None = None) -> dict:
    """
    Fill missing/empty environment variables from .env (never override a set value).

    Args:
        path (str | None): .env path; default: python-dotenv's search from the cwd.

    Returns:
        dict: The variables this call set (name → value). Never raises.
    """
    try:
        from dotenv import dotenv_values, find_dotenv
        values = dotenv_values(path or find_dotenv(usecwd=True) or None)
    except Exception:  # noqa: BLE001
        return {}
    applied = {}
    for key, value in (values or {}).items():
        if value is None:
            continue
        if not os.environ.get(key):
            os.environ[key] = value
            applied[key] = value
    return applied
