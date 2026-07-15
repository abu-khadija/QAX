"""
core/input_adapters/folder_adapter.py — Folder Input Adapter
=============================================================
Walks a directory and returns all source files matching a given extension.

Used when --input folder is passed, or when the pipeline is given a folder
path instead of a single file. Supports recursive and non-recursive modes.

Usage:
    adapter = FolderInputAdapter()
    items   = adapter.fetch("src/Auth/Controllers", ext=".cs")
    # items is a list of dicts, one per .cs file found
"""

import logging
import os
from pathlib import Path

from core.input_adapters.base import BaseInputAdapter

logger = logging.getLogger(__name__)


class FolderInputAdapter(BaseInputAdapter):
    """
    Input adapter that walks a directory and returns all matching source files.

    Supports filtering by file extension and toggling recursive traversal.
    Files that cannot be decoded are silently skipped.
    """

    name = "folder"

    def validate(self) -> tuple[bool, str]:
        """
        No credentials required — always valid.

        Returns:
            tuple[bool, str]: (True, "") unconditionally.
        """
        return True, ""

    def fetch(
        self,
        source: str,
        ext: str = ".cs",
        recursive: bool = True,
        **kwargs,
    ) -> list[dict]:
        """
        Walk a folder and collect all files matching the given extension.

        Args:
            source (str): Absolute or relative path to the directory.
            ext (str): File extension to filter by (default: ".cs").
                       Pass None to collect all files.
            recursive (bool): If True (default), walk all subdirectories.
                              If False, look only in the top-level folder.
            **kwargs: Unused additional options.

        Returns:
            list[dict]: One dict per matching file, or [] if folder not found
                        or no files match.
        """
        if not source:
            logger.warning("QAX FolderInputAdapter: no source path provided")
            return []

        if not os.path.isdir(source):
            logger.warning("QAX FolderInputAdapter: folder not found — %s", source)
            print(f"  [QAX] Folder not found: {source}")
            return []

        # Normalise extension
        if ext and not ext.startswith("."):
            ext = f".{ext}"

        results: list[dict] = []

        if recursive:
            walker = os.walk(source)
        else:
            # Non-recursive: yield only the top-level dir
            entries = os.listdir(source)
            walker = [(source, [], entries)]

        for root, _, files in walker:
            for fname in sorted(files):
                if ext and not fname.endswith(ext):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    content = Path(fpath).read_text(encoding="utf-8", errors="ignore")
                    results.append({
                        "file_path":   fpath,
                        "content":     content,
                        "file_name":   fname,
                        "source_type": self.name,
                        "metadata": {
                            "size_lines": len(content.splitlines()),
                            "extension":  os.path.splitext(fname)[1],
                            "folder":     source,
                        },
                    })
                except Exception as exc:
                    logger.debug(
                        "QAX FolderInputAdapter: skipping %s — %s", fpath, exc
                    )

        ext_label = ext or "*"
        print(f"  Found {len(results)} {ext_label} file(s) in {source}")
        logger.info(
            "QAX FolderInputAdapter: found %d %s files in %s",
            len(results), ext_label, source,
        )
        return results
