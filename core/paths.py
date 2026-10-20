"""
core/paths.py — QAX: where the shipped read-only data files live.

In a repo checkout (the supported layout, also Docker and `pip install -e .`) the files are
at `<repo>/config/` and `<repo>/configs/`. A wheel install (`pip install .`) has no repo root,
so pyproject.toml ships the same files as the data-only packages `qax_config` (config/*.md,
config/*.json — never config/projects/, which is per-user data) and `qax_configs` (the run
templates). These helpers return the repo copy when it exists and the installed copy otherwise.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _installed_dir(package: str) -> Path | None:
    """Directory of an installed data-only package (`qax_config`/`qax_configs`), or None. Never raises."""
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return None
    if spec is None:
        return None
    for loc in list(spec.submodule_search_locations or []):
        if Path(loc).is_dir():
            return Path(loc)
    return None


def config_file(name: str) -> Path:
    """
    Path to a shipped file in config/ (e.g. "system_prompt.md", "xray_schema.json").

    Prefers `<repo>/config/<name>`; falls back to the installed `qax_config` package; when
    neither has it, returns the repo path so callers report a clear "not found". Never raises.
    """
    repo = REPO_ROOT / "config" / name
    if repo.exists():
        return repo
    installed = _installed_dir("qax_config")
    if installed is not None and (installed / name).exists():
        return installed / name
    return repo


def configs_template(rel: str) -> Path:
    """
    Resolve a run-config path such as "configs/smoke.json" for reading.

    The path as given (relative to the working directory) wins; otherwise the repo copy;
    otherwise the template shipped in `qax_configs`. Returns the path as given when none exist.
    Never raises.
    """
    given = Path(rel)
    if given.exists() or given.is_absolute():
        return given
    parts = given.parts
    if not parts or parts[0] != "configs":
        return given
    repo = REPO_ROOT / given
    if repo.exists():
        return repo
    installed = _installed_dir("qax_configs")
    if installed is not None and (installed.joinpath(*parts[1:])).exists():
        return installed.joinpath(*parts[1:])
    return given
