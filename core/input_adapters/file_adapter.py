"""
core/input_adapters/file_adapter.py — Single-File Input Adapter
================================================================
Reads one source file from the local filesystem.

This is the default adapter used when --input file (or --file) is passed.
It wraps the existing filesystem read so the pipeline can treat it the same
as any other input source.

Usage:
    adapter = FileInputAdapter()
    items   = adapter.fetch("src/Auth/LoginController.cs")
    # items[0]["content"] holds the file text
"""

import logging
import os
from pathlib import Path

from core.input_adapters.base import BaseInputAdapter

logger = logging.getLogger(__name__)


class FileInputAdapter(BaseInputAdapter):
    """
    Input adapter that reads a single source file from disk.

    Returns a one-element list on success, or [] if the file is not found
    or cannot be decoded.
    """

    name = "file"

    def validate(self) -> tuple[bool, str]:
        """
        No credentials required — always valid.

        Returns:
            tuple[bool, str]: (True, "") unconditionally.
        """
        return True, ""

    def fetch(self, source: str, **kwargs) -> list[dict]:
        """
        Read a single file and return its content as a normalised input dict.

        Args:
            source (str): Absolute or relative path to the source file.
            **kwargs: Unused for this adapter.

        Returns:
            list[dict]: Single-element list with the file content,
                        or [] if the file does not exist or cannot be read.
        """
        if not source:
            logger.warning("QAX FileInputAdapter: no source path provided")
            return []

        if not os.path.exists(source):
            logger.warning("QAX FileInputAdapter: file not found — %s", source)
            print(f"  [QAX] File not found: {source}")
            return []

        try:
            content = Path(source).read_text(encoding="utf-8", errors="ignore")
            logger.info("QAX FileInputAdapter: read %s (%d lines)", source, content.count("\n"))
            return [{
                "file_path":   source,
                "content":     content,
                "file_name":   os.path.basename(source),
                "source_type": self.name,
                "metadata": {
                    "size_lines": len(content.splitlines()),
                    "extension":  os.path.splitext(source)[1],
                },
            }]
        except Exception as exc:
            logger.error("QAX FileInputAdapter: read error — %s", exc)
            print(f"  [QAX] Could not read file: {exc}")
            return []
