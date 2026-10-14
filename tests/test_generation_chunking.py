"""GenerationService now chunks large real files, skips untestable ones, and really
resolves include_deps — the legacy CLI path's features, moved into the service the
scan, pipeline and multi-source paths all use."""
import core.claude_client as cc
import core.smart_checker as sc
from services.context import QAXContext
from services.generation_service import GenerationOptions, GenerationService


def _valid_case(title):
    return {"test_case_id": "TC-GEN-0001", "title": title, "description": "a valid generated test",
            "module": "m", "test_type": "api", "priority": "high",
            "preconditions": ["A clean database"], "confidence_score": 8, "needs_review": False,
            "generated_from_file": "f.py", "generated_at": "2026-10-14",
            "api_steps": [{"step_number": 1, "method": "GET", "path": "/x"}]}


def _setup(monkeypatch):
    calls = []

    class _FakeClient:
        def __init__(self, *a, **k): pass

        def analyze_code(self, **kw):
            calls.append(kw)
            return [_valid_case(f"Generated case number {len(calls)}"), _valid_case("Shared case seen in every chunk")]
        deduplicate_by_title = staticmethod(cc.ClaudeClient.deduplicate_by_title)

    monkeypatch.setattr(cc, "ClaudeClient", _FakeClient)
    monkeypatch.setattr(sc.SmartChecker, "run_pre_checks", lambda self, **k: {
        "pre_check_summary": "ok", "test_type": "api", "risk": {}, "recommended_model": None,
        "cache_hit": False, "cached_result": [], "skill_context": None})
    return calls


def _svc():
    return GenerationService(QAXContext(anthropic_key="k", project=""))


def _big(tmp_path, n=120):
    p = tmp_path / "svc.py"
    p.write_text("".join(f"def f{i}(x):\n    if x > {i}:\n        return x\n    return {i}\n\n" for i in range(n)))
    return str(p)


def test_large_real_file_is_chunked_scoped_and_deduped(tmp_path, monkeypatch):
    calls = _setup(monkeypatch)
    r = _svc().generate(_big(tmp_path), options=GenerationOptions(test_type="api", use_cache=False, chunk_size=100))
    assert len(calls) > 1                                           # one call per chunk
    assert len({c["cache_scope"] for c in calls}) == len(calls)     # distinct cache scope each
    titles = [t["title"] for t in r.test_cases]
    assert titles.count("Shared case seen in every chunk") == 1   # cross-chunk de-dup
    assert len(titles) == len(calls) + 1


def test_small_file_single_call_unscoped(tmp_path, monkeypatch):
    calls = _setup(monkeypatch)
    _svc().generate(_big(tmp_path, 5), options=GenerationOptions(test_type="api", use_cache=False))
    assert len(calls) == 1 and calls[0]["cache_scope"] is None


def test_untestable_real_file_skipped_without_model_call(tmp_path, monkeypatch):
    calls = _setup(monkeypatch)
    p = tmp_path / "c.py"; p.write_text("A = 1\nB = 2\n")
    r = _svc().generate(str(p), options=GenerationOptions(use_cache=False))
    assert calls == [] and r.test_cases == [] and r.summary.get("skipped")


def test_virtual_source_never_chunked_or_skipped(monkeypatch):
    calls = _setup(monkeypatch)
    spec = "\n".join(f"Endpoint: GET /items/{i}" for i in range(500))   # long, but virtual
    _svc().generate("endpoints://items", options=GenerationOptions(content=spec, test_type="api",
                                                                   use_cache=False, chunk_size=100))
    assert len(calls) == 1 and calls[0]["cache_scope"] is None


def test_include_deps_is_actually_resolved(tmp_path, monkeypatch):
    calls = _setup(monkeypatch)
    import core.code_reader as cr
    monkeypatch.setattr(cr, "extract_dependencies",
                        lambda **k: [{"file_name": "helper.py", "content": "def h(): pass"}])
    _svc().generate(_big(tmp_path, 5), options=GenerationOptions(use_cache=False, include_deps=True))
    assert calls[0]["dependencies"] and calls[0]["dependencies"][0]["file_name"] == "helper.py"


def test_screenshot_passed_when_enabled(tmp_path, monkeypatch):
    calls = _setup(monkeypatch)
    monkeypatch.setenv("ENABLE_SCREENSHOT", "true")
    import core.screenshot_capture as scap

    class _Cap:
        async def capture_module(self, mod): return "BASE64IMG"
    monkeypatch.setattr(scap, "ScreenshotCapture", _Cap)
    _svc().generate(_big(tmp_path, 5),
                    options=GenerationOptions(use_cache=False, screenshot=True))
    assert calls[0]["screenshot_base64"] == "BASE64IMG"


def test_screenshot_skipped_when_env_off(tmp_path, monkeypatch):
    calls = _setup(monkeypatch)
    monkeypatch.setenv("ENABLE_SCREENSHOT", "false")
    _svc().generate(_big(tmp_path, 5),
                    options=GenerationOptions(use_cache=False, screenshot=True))
    assert calls[0]["screenshot_base64"] is None


def test_screenshot_not_attempted_for_virtual_source(tmp_path, monkeypatch):
    calls = _setup(monkeypatch)
    monkeypatch.setenv("ENABLE_SCREENSHOT", "true")
    import core.screenshot_capture as scap

    def _boom(*a, **k): raise AssertionError("must not capture for a virtual source")
    monkeypatch.setattr(scap, "ScreenshotCapture", _boom)
    _svc().generate("endpoints://x", options=GenerationOptions(
        content="Endpoint: GET /x", test_type="api", use_cache=False, screenshot=True))
    assert calls[0]["screenshot_base64"] is None
