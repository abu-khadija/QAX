"""S8: source-file collection extracted to cli/files (pure, no sys.exit)."""
import pytest

from cli.files import collect_source_files, resolve_ext


def test_single_file_and_missing(tmp_path):
    f = tmp_path / "a.py"; f.write_text("x")
    assert collect_source_files(str(f), None) == [str(f)]
    with pytest.raises(FileNotFoundError):
        collect_source_files(str(tmp_path / "nope.py"), None)


def test_folder_collects_by_extension(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.py").write_text("x"); (tmp_path / "sub" / "b.py").write_text("x")
    (tmp_path / "c.txt").write_text("x")
    got = collect_source_files(None, str(tmp_path), ext="py")
    assert sorted(p.rsplit("/", 1)[-1] for p in got) == ["a.py", "b.py"]
    with pytest.raises(FileNotFoundError):
        collect_source_files(None, str(tmp_path / "missing"), ext=".py")


def test_resolve_ext_behavior_unchanged():
    assert resolve_ext(".ts") == ".ts" and resolve_ext("ts") == ".ts"
    assert resolve_ext(None, "example3") == ".ts"
    assert resolve_ext(None, "anything") == ".cs"      # legacy default, preserved


def test_main_wrapper_keeps_exit_codes(tmp_path):
    import main
    with pytest.raises(SystemExit) as e:
        main._collect_files_from_args(str(tmp_path / "nope.py"), None)
    assert e.value.code == 1                       # missing file → exit 1
    with pytest.raises(SystemExit) as e:
        main._collect_files_from_args(None, str(tmp_path), ext=".py")
    assert e.value.code == 0                       # folder with no matches → exit 0
