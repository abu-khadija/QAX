"""
test_text_encoding.py — every text file QAX reads or writes names its encoding
==============================================================================
Regression (Windows, 2026-10-23): open() / read_text() / write_text() without `encoding=`
use the OS locale — cp1252 on Windows — so a UTF-8 file with "✅" or Arabic text raised
UnicodeDecodeError, and a scan step that printed one died ("ExecutionService failed:
'charmap' codec can't encode character"). macOS and Linux default to UTF-8, which hid it.
This walks the source and fails on any text-mode call that leaves the encoding to the OS.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"output", "build", "node_modules", "vendor", "__pycache__", ".git"}


def _sources():
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT).parts
        if any(part in SKIP_DIRS or part.startswith(".venv") for part in rel):
            continue
        yield p


def _mode(call: ast.Call, pos: int) -> str | None:
    """The mode string of an open()-style call, if it is a literal."""
    if len(call.args) > pos and isinstance(call.args[pos], ast.Constant):
        return str(call.args[pos].value)
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return None


def text_calls_without_encoding(path: Path) -> list[int]:
    """Line numbers of text-mode open/fdopen/read_text/write_text calls with no encoding=."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or any(k.arg == "encoding" for k in node.keywords):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        if name in ("read_text", "write_text") and isinstance(f, ast.Attribute):
            bad.append(node.lineno)
        elif (name == "open" and isinstance(f, ast.Name)) or (name == "fdopen" and isinstance(f, ast.Attribute)):
            if "b" not in (_mode(node, 1) or "r"):
                bad.append(node.lineno)
    return bad


def test_every_text_read_and_write_names_its_encoding():
    offenders = [f"{p.relative_to(ROOT)}:{n}" for p in _sources() for n in text_calls_without_encoding(p)]
    assert not offenders, "text I/O without encoding= (breaks on Windows):\n" + "\n".join(offenders)


def test_the_guard_sees_a_bare_open_and_accepts_binary_and_explicit(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("open('a')\nopen('a', 'rb')\nopen('a', encoding='utf-8')\nfrom pathlib import Path\n"
                 "Path('a').read_text()\nPath('a').write_text('x', encoding='utf-8')\n", encoding="utf-8")
    assert text_calls_without_encoding(f) == [1, 5]
