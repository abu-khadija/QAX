"""
core/paths.py — QAX: where the shipped read-only data files live.

In a repo checkout (the supported layout, also Docker and `pip install -e .`) the files are
at `<repo>/config/` and `<repo>/configs/`. A wheel install (`pip install .`) has no repo root,
so pyproject.toml ships the same files as the data-only packages `qax_config` (config/*.md,
config/*.json — never config/projects/, which is per-user data) and `qax_configs` (the run
templates). These helpers return the repo copy when it exists and the installed copy otherwise.
What QAX writes goes under `data_root()`: the checkout, or (wheel install) the folder QAX runs
from — never site-packages.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def is_checkout() -> bool:
    """True when QAX runs from a repo checkout (also Docker and `pip install -e .`), False from a wheel."""
    return (REPO_ROOT / "config" / "system_prompt.md").exists()


def data_root() -> Path:
    """
    Where QAX keeps what it WRITES (output/, config/projects/, learned skills).

    A checkout keeps its own folder, as before. A wheel install must never write into
    site-packages, so it uses the folder QAX runs from — the same place the cwd-relative
    output/ writes (screenshots, reports, web runs) already go. Never raises.
    """
    if is_checkout():
        return REPO_ROOT
    try:
        return Path.cwd()
    except OSError:
        return Path.home()


def output_dir() -> Path:
    """`<data root>/output`."""
    return data_root() / "output"


def user_config_dir() -> Path:
    """`<data root>/config` — holds projects/<name>/ (app maps, selectors, context) and skills.json."""
    return data_root() / "config"


def projects_dir() -> Path:
    """`<data root>/config/projects`."""
    return user_config_dir() / "projects"


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

    Prefers `<repo>/config/<name>`; from a wheel install, a copy in `<cwd>/config/<name>`
    overrides the shipped one (that is where an edited system prompt is saved); then the
    installed `qax_config` package; when none has it, returns the repo path so callers
    report a clear "not found". Never raises.
    """
    repo = REPO_ROOT / "config" / name
    if repo.exists():
        return repo
    if not is_checkout():
        override = user_config_dir() / name
        if override.exists():
            return override
    installed = _installed_dir("qax_config")
    if installed is not None and (installed / name).exists():
        return installed / name
    return repo


def editable_config_file(name: str) -> Path:
    """
    Path to write an edited copy of a shipped config file (e.g. the optimizer's system prompt).

    A checkout edits `<repo>/config/<name>` in place, as before. A wheel install never edits
    site-packages: it copies the shipped file to `<cwd>/config/<name>` once (config_file then
    reads that copy) and returns it. Never raises — on a copy failure returns the target anyway.
    """
    if is_checkout():
        return REPO_ROOT / "config" / name
    target = user_config_dir() / name
    try:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            source = config_file(name)
            target.write_bytes(source.read_bytes() if source.exists() else b"")
    except OSError:
        pass
    return target


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
