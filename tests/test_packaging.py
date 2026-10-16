"""S5: every third-party module the supported surface imports must be declared in a requirements file,
so a clean install can run it. (The Streamlit UI that this used to cover was removed on 2026-10-17;
`web/` and the scanner are the supported surface now.)"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _declared(path: str) -> set:
    names = set()
    for line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(re.split(r"[<>=!\[ ;]", line, 1)[0].lower().replace("_", "-"))
    return names


def _third_party_imports(py_file: Path) -> set:
    """Top-level import names in `py_file` that are not stdlib and not this repo's own packages."""
    import sys
    own = {p.name for p in ROOT.iterdir() if p.is_dir() or p.suffix == ".py"} | {"main"}
    out = set()
    for node in ast.walk(ast.parse(py_file.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        else:
            continue
        for n in names:
            top = n.split(".")[0]
            if top not in sys.stdlib_module_names and top not in own and top != "__future__":
                out.add(top)
    return out


def test_web_server_third_party_imports_are_declared():
    declared = _declared("requirements.txt")
    needed = _third_party_imports(ROOT / "web" / "server.py")
    assert "flask" in needed, "expected web/server.py to import flask"
    missing = sorted(n for n in needed if n.lower().replace("_", "-") not in declared)
    assert not missing, f"web/server.py imports not declared in requirements.txt: {missing}"


def test_scanner_requirements_cover_the_demo_and_scan_path():
    declared = _declared("requirements-scan.txt")
    for name in ("requests", "flask", "anthropic", "pydantic"):
        assert name in declared, f"{name} missing from requirements-scan.txt (demo/scan would not start)"
