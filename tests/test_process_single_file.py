"""Characterization tests for main._process_single_file (S8 prerequisite).

They pin the legacy generation path's CURRENT behavior so it can be routed through
GenerationService without silently losing anything: non-testable skip, chunking of
large files + cross-chunk de-duplication, and the SmartChecker cache-hit short-cut.
The Claude client is faked — no network, no credits.
"""
import main


class _FakeClaude:
    def __init__(self):
        self.calls = 0

    def analyze_code(self, **kw):
        self.calls += 1
        return [{"title": f"case {self.calls}"}, {"title": "shared case"}]


def _precheck(monkeypatch, **over):
    import core.smart_checker as sc
    base = {"pre_check_summary": "ok", "test_type": "api", "risk": {},
            "recommended_model": None, "cache_hit": False, "cached_result": []}
    base.update(over)
    monkeypatch.setattr(sc.SmartChecker, "run_pre_checks", lambda self, **k: dict(base))


def _big_py(tmp_path, funcs=120):
    body = "".join(f"def f{i}(x):\n    if x > {i}:\n        return x\n    return {i}\n\n"
                   for i in range(funcs))
    p = tmp_path / "service.py"; p.write_text(body)
    return str(p)


def test_skips_non_testable_file(tmp_path, monkeypatch):
    _precheck(monkeypatch)
    p = tmp_path / "consts.py"; p.write_text("A = 1\nB = 2\n")
    claude = _FakeClaude()
    assert main._process_single_file(str(p), claude, None, chunk_size=100, use_cache=False) == []
    assert claude.calls == 0                                 # never calls the model


def test_large_file_is_chunked_and_deduplicated(tmp_path, monkeypatch):
    _precheck(monkeypatch)
    claude = _FakeClaude()
    out = main._process_single_file(_big_py(tmp_path), claude, None, chunk_size=100, use_cache=False)
    assert claude.calls > 1                                  # one call per chunk
    titles = [c["title"] for c in out]
    assert titles.count("shared case") == 1                  # cross-chunk duplicates removed
    assert len(out) == claude.calls + 1


def test_cache_hit_returns_cached_without_calling_model(tmp_path, monkeypatch):
    _precheck(monkeypatch, cache_hit=True, cached_result=[{"title": "from cache"}])
    claude = _FakeClaude()
    out = main._process_single_file(_big_py(tmp_path, 10), claude, None, chunk_size=300, use_cache=True)
    assert out == [{"title": "from cache"}] and claude.calls == 0


def test_chunked_file_ignores_stale_whole_file_cache_and_scopes_chunks(tmp_path, monkeypatch):
    # A whole-file cache entry for a file that gets chunked can only be one chunk's
    # result (pre-fix), so it must not short-circuit; each chunk call gets a scope.
    _precheck(monkeypatch, cache_hit=True, cached_result=[{"title": "stale chunk-1 only"}])
    scopes = []

    class _Rec(_FakeClaude):
        def analyze_code(self, **kw):
            scopes.append(kw.get("cache_scope"))
            return super().analyze_code(**kw)
    claude = _Rec()
    out = main._process_single_file(_big_py(tmp_path), claude, None, chunk_size=100, use_cache=True)
    assert claude.calls > 1 and {"title": "stale chunk-1 only"} not in out
    assert len(set(scopes)) == claude.calls and None not in scopes      # one distinct scope per chunk


def test_real_line_count_reaches_model_selection(tmp_path, monkeypatch):
    # read_file() reports "size_lines"; the legacy path used to read a missing
    # "line_count" key and always passed 0, so large files never got the heavy model.
    _precheck(monkeypatch)
    seen = []

    class _Rec(_FakeClaude):
        def analyze_code(self, **kw):
            seen.append(kw.get("line_count"))
            return super().analyze_code(**kw)
    path = _big_py(tmp_path)
    main._process_single_file(path, _Rec(), None, chunk_size=10_000, use_cache=False)
    expected = len(open(path).read().splitlines())
    assert seen == [expected] and expected > 150
