"""E15: a wheel install (`pip install .` → `qax`, `qax-web`) must carry every non-Python file the
shipped commands read — the web UI, config/*.md/*.json, the run templates, the demo/gate targets —
and must never carry per-user data (config/projects/*) or secrets."""
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

from core import paths

ROOT = Path(__file__).resolve().parent.parent

REQUIRED = [
    "web/static/app.html", "web/static/app.js", "web/static/app.css", "web/static/index.html",
    "qax_config/system_prompt.md", "qax_config/xray_schema.json",
    "qax_configs/smoke.json", "qax_configs/regression.json",
    "core/vendor/axe-core/axe.min.js",
    "evals/targets/vuln_jwt_app.py", "evals/truth/golden-jwt.json",
    "main.py",
]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_explicit_package_list_covers_every_package():
    """The explicit `packages` list must not silently drop a package added later."""
    listed = set(_pyproject()["tool"]["setuptools"]["packages"])
    tops = {p.split(".")[0] for p in listed} - {"qax_config", "qax_configs"}
    on_disk = set()
    for top in tops:
        for init in (ROOT / top).rglob("__init__.py"):
            rel = init.parent.relative_to(ROOT)
            if "__pycache__" not in rel.parts:
                on_disk.add(".".join(rel.parts))
    assert on_disk <= listed, f"packages missing from pyproject.toml: {sorted(on_disk - listed)}"


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory) -> set:
    out = tmp_path_factory.mktemp("wheel")
    r = subprocess.run([sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-q", "-w", str(out)],
                       capture_output=True, text=True, timeout=300)
    wheels = list(out.glob("qax-*.whl"))
    if r.returncode != 0 or not wheels:
        pytest.skip(f"could not build the wheel here (no setuptools/network?): {r.stderr[-300:]}")
    with zipfile.ZipFile(wheels[0]) as z:
        return set(z.namelist())


def test_wheel_ships_ui_config_and_targets(wheel_names):
    missing = [f for f in REQUIRED if f not in wheel_names]
    assert not missing, f"wheel is missing runtime files: {missing}"


def test_wheel_ships_no_user_data_or_secrets(wheel_names):
    bad = [n for n in wheel_names
           if "/projects/" in n or n.startswith(("config/", "configs/", "output/", "tests/"))
           or Path(n).name in {".env", "app_map.json", "selectors.json"}]
    assert not bad, f"wheel ships files it must not: {bad[:10]}"


def test_config_file_prefers_repo_then_installed(tmp_path, monkeypatch):
    assert paths.config_file("system_prompt.md") == ROOT / "config" / "system_prompt.md"
    installed = tmp_path / "site" / "qax_config"
    installed.mkdir(parents=True)
    (installed / "xray_schema.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path / "no-repo")
    monkeypatch.setattr(paths, "_installed_dir", lambda pkg: installed if pkg == "qax_config" else None)
    assert paths.config_file("xray_schema.json") == installed / "xray_schema.json"
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "xray_schema.json").write_text("{}", encoding="utf-8")   # the user's copy in the run folder wins
    assert paths.config_file("xray_schema.json") == tmp_path / "config" / "xray_schema.json"
    # neither has it → the repo path, so the caller's "not found" message names it
    assert paths.config_file("nope.md") == tmp_path / "no-repo" / "config" / "nope.md"


def test_configs_template_falls_back_to_shipped_copy(tmp_path, monkeypatch):
    shipped = tmp_path / "site" / "qax_configs"
    shipped.mkdir(parents=True)
    (shipped / "smoke.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path / "no-repo")
    monkeypatch.setattr(paths, "_installed_dir", lambda pkg: shipped if pkg == "qax_configs" else None)
    assert paths.configs_template("configs/smoke.json") == shipped / "smoke.json"
    assert paths.configs_template("mine.json") == Path("mine.json")      # not a template: untouched
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "smoke.json").write_text("{}", encoding="utf-8")
    assert paths.configs_template("configs/smoke.json") == Path("configs/smoke.json")  # the user's copy wins


# ── What QAX writes: never site-packages (E15 follow-up) ─────────────────────

def test_checkout_keeps_writing_where_it_always_did():
    assert paths.is_checkout()
    assert paths.data_root() == ROOT
    assert paths.output_dir() == ROOT / "output"
    assert paths.projects_dir() == ROOT / "config" / "projects"


def test_wheel_install_writes_under_the_run_folder(tmp_path, monkeypatch):
    site = tmp_path / "site-packages"
    run = tmp_path / "work"
    run.mkdir()
    monkeypatch.chdir(run)
    monkeypatch.setattr(paths, "REPO_ROOT", site)          # no config/system_prompt.md there → wheel layout
    assert not paths.is_checkout()
    assert paths.output_dir() == run / "output"
    assert paths.projects_dir() == run / "config" / "projects"


def test_editable_config_file_copies_the_shipped_prompt_once(tmp_path, monkeypatch):
    shipped = tmp_path / "site-packages" / "qax_config"
    shipped.mkdir(parents=True)
    (shipped / "system_prompt.md").write_text("shipped", encoding="utf-8")
    run = tmp_path / "work"
    run.mkdir()
    monkeypatch.chdir(run)
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path / "site-packages")
    monkeypatch.setattr(paths, "_installed_dir", lambda pkg: shipped if pkg == "qax_config" else None)
    target = paths.editable_config_file("system_prompt.md")
    assert target == run / "config" / "system_prompt.md" and target.read_text(encoding="utf-8") == "shipped"
    target.write_text("edited", encoding="utf-8")
    assert paths.editable_config_file("system_prompt.md").read_text(encoding="utf-8") == "edited"   # not re-copied
    assert paths.config_file("system_prompt.md") == target                           # and it is what is read
    assert (shipped / "system_prompt.md").read_text(encoding="utf-8") == "shipped"                   # package untouched


def test_no_module_builds_output_or_config_paths_from_its_install_location():
    """Writes must go through core.paths; `Path(__file__)…/"output"|"config"` lands in site-packages."""
    import re
    pat = re.compile(r"Path\(__file__\)[^\n]*/\s*[\"'](output|config)[\"']")
    hits = []
    for top in ("core", "services", "execution", "web", "cli"):
        for f in (ROOT / top).rglob("*.py"):
            if f.name != "paths.py" and pat.search(f.read_text(encoding="utf-8")):
                hits.append(str(f.relative_to(ROOT)))
    if pat.search((ROOT / "main.py").read_text(encoding="utf-8")):
        hits.append("main.py")
    assert not hits, f"use core.paths instead: {hits}"
