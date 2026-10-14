"""cli/files.py — resolve the source files a CLI run should analyze (S8).

Pure (no sys.exit): raises FileNotFoundError for a missing file/folder and returns
[] when nothing matches; main.py maps those to its original exit codes.

Folder mode with no --ext auto-detects the folder's dominant source language
(core.code_reader.detect_dominant_extension) — QAX is generic, so there is no
per-project or per-language hard-coded default. Vendored/build dirs are skipped.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from core.code_reader import SKIP_DIRS, detect_dominant_extension, is_generated_file

logger = logging.getLogger(__name__)


def resolve_ext(ext: str | None, folder: str | None = None) -> str | None:
    """Explicit --ext (normalised), else the folder's detected language, else None."""
    if ext:
        return ext if ext.startswith(".") else f".{ext}"
    return detect_dominant_extension(folder) if folder else None


def collect_source_files(file: str | None, folder: str | None, ext: str | None = None) -> list[str]:
    """Paths to analyze from --file or --folder. Raises FileNotFoundError if missing."""
    if file:
        path = Path(file)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file}")
        return [str(path)]
    if folder:
        if not Path(folder).is_dir():
            raise FileNotFoundError(f"Folder not found: {folder}")
        resolved = resolve_ext(ext, folder)
        if not resolved:
            logger.warning("No analyzable source files detected in %s", folder)
            return []
        if not ext:
            logger.info("Auto-detected source language in %s: %s (override with --ext)", folder, resolved)
        out: list[str] = []
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            out.extend(os.path.join(root, f) for f in sorted(files)
                       if f.endswith(resolved) and not is_generated_file(f))
        logger.info("Found %d %s file(s) in %s", len(out), resolved, folder)
        return sorted(out)
    return []
