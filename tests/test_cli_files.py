"""Source-file collection: language auto-detection (no hard-coded .cs default),
vendored-dir skipping, and the CLI wrapper's exit codes."""
import pytest

from cli.files import collect_source_files, resolve_ext
from core.code_reader import detect_dominant_extension
from core.input_adapters.folder_adapter import FolderInputAdapter


def _tree(tmp_path, files):
    for rel in files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n")
    return tmp_path


# ── detection ─────────────────────────────────────────────────────────────────

def test_detects_dominant_language(tmp_path):
    _tree(tmp_path, ["a.py", "b.py", "pkg/c.py", "web/x.ts"])
    assert detect_dominant_extension(str(tmp_path)) == ".py"


def test_detection_ignores_vendored_dirs(tmp_path):
    # node_modules would otherwise make a Python repo look like JavaScript
    _tree(tmp_path, ["app.py", "core.py"] + [f"node_modules/lib/m{i}.js" for i in range(20)]
          + [".venv/lib/site.py"])
    assert detect_dominant_extension(str(tmp_path)) == ".py"


def test_detection_none_when_no_source(tmp_path):
    _tree(tmp_path, ["README.md", "data.json"])
    assert detect_dominant_extension(str(tmp_path)) is None


def test_no_hardcoded_language_default():
    # The old fallback returned ".cs" for any project; now no folder → no guess.
    assert resolve_ext(None) is None
    assert resolve_ext("ts") == ".ts" and resolve_ext(".go") == ".go"


# ── CLI collection ────────────────────────────────────────────────────────────

def test_single_file_and_missing(tmp_path):
    f = tmp_path / "a.py"; f.write_text("x")
    assert collect_source_files(str(f), None) == [str(f)]
    with pytest.raises(FileNotFoundError):
        collect_source_files(str(tmp_path / "nope.py"), None)


def test_folder_auto_detects_and_skips_vendor(tmp_path):
    _tree(tmp_path, ["src/a.ts", "src/b.ts", "src/c.py", "node_modules/z/d.ts", "dist/e.ts"])
    got = collect_source_files(None, str(tmp_path))          # no --ext
    names = sorted(p.rsplit("/", 1)[-1] for p in got)
    assert names == ["a.ts", "b.ts"]                          # .ts detected; vendor/build skipped


def test_folder_explicit_ext_and_missing(tmp_path):
    _tree(tmp_path, ["a.py", "sub/b.py", "c.txt"])
    got = collect_source_files(None, str(tmp_path), ext="py")
    assert sorted(p.rsplit("/", 1)[-1] for p in got) == ["a.py", "b.py"]
    with pytest.raises(FileNotFoundError):
        collect_source_files(None, str(tmp_path / "missing"), ext=".py")


def test_main_wrapper_keeps_exit_codes(tmp_path):
    import main
    with pytest.raises(SystemExit) as e:
        main._collect_files_from_args(str(tmp_path / "nope.py"), None)
    assert e.value.code == 1                       # missing file → exit 1
    with pytest.raises(SystemExit) as e:
        main._collect_files_from_args(None, str(tmp_path), ext=".py")
    assert e.value.code == 0                       # folder with no matches → exit 0


# ── pipeline folder adapter ───────────────────────────────────────────────────

def test_folder_adapter_auto_detects_and_skips_vendor(tmp_path):
    _tree(tmp_path, ["app/a.go", "app/b.go", "README.md", "vendor/x/c.go"])
    items = FolderInputAdapter().fetch(str(tmp_path))          # ext omitted → auto
    assert sorted(i["file_name"] for i in items) == ["a.go", "b.go"]


def test_folder_adapter_star_collects_all_non_vendor(tmp_path):
    _tree(tmp_path, ["a.py", "README.md", "node_modules/z.js"])
    items = FolderInputAdapter().fetch(str(tmp_path), ext="*")
    assert sorted(i["file_name"] for i in items) == ["README.md", "a.py"]


def test_detection_ignores_collected_static_assets(tmp_path):
    # Django `collectstatic` output (admin/swagger JS) must not flip a Python app to .js
    _tree(tmp_path, ["accounts/views.py", "articles/models.py", "manage.py"]
          + [f"staticfiles/admin/js/f{i}.js" for i in range(30)] + ["web/app.min.js", "web/b.min.js"])
    assert detect_dominant_extension(str(tmp_path)) == ".py"
