"""
core/console.py — UTF-8 stdout/stderr on every platform
=======================================================
Windows consoles default to cp1252, so printing "✅" raised UnicodeEncodeError: in the web
server that killed a scan's API-test step ("ExecutionService failed: 'charmap' codec can't
encode character '\\u2705'"), and the benchmarks crashed on their own result lines.
main.py already did this for the CLI; the web server, CI script and evals call this.
"""

from __future__ import annotations

import sys


def force_utf8_output() -> None:
    """Switch stdout and stderr to UTF-8 where the stream allows it. Never raises."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
