"""cli/files.py — resolve the source files a CLI run should analyze (S8).

Pure (no sys.exit): raises FileNotFoundError for a missing file/folder and returns
[] when nothing matches; main.py maps those to its original exit codes.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Folder-mode extension fallback when --ext is not given. NOTE: the ".cs" default is a
# leftover from the original C#-only project and is not generic — kept unchanged here
# so this refactor doesn't alter behavior; see the roadmap for the follow-up.
_PROJECT_EXT = {"example3": ".ts"}
_DEFAULT_EXT = ".cs"


def resolve_ext(ext: str | None, project: str = "") -> str:
    """The extension to collect: explicit --ext, else the project map, else the default."""
    if ext is None:
        ext = _PROJECT_EXT.get((project or "").lower(), _DEFAULT_EXT)
    return ext if ext.startswith(".") else f".{ext}"


def collect_source_files(file: str | None, folder: str | None, ext: str | None = None,
                         project: str = "") -> list[str]:
    """Paths to analyze from --file or --folder. Raises FileNotFoundError if missing."""
    if file:
        path = Path(file)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file}")
        return [str(path)]
    if folder:
        dir_path = Path(folder)
        if not dir_path.is_dir():
            raise FileNotFoundError(f"Folder not found: {folder}")
        ext = resolve_ext(ext, project)
        files = sorted(dir_path.rglob(f"*{ext}"))
        logger.info("Found %d %s file(s) in %s", len(files), ext, folder)
        return [str(f) for f in files]
    return []
