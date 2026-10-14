"""Regression: with caching on (the CLI default), every chunk of a large file must be
analyzed. The cache key used to be file-level only, so chunks 2..N were served
chunk 1's cached result and never reached the model."""
import types

import core.cache_manager as cm
from core.claude_client import ClaudeClient


class _MemCache:
    store: dict = {}

    def __init__(self, *a, **k): pass
    def get_key(self, file_path): return "FILEKEY"          # file-level key, same for all chunks
    def get(self, key): return self.store.get(key)
    def set(self, key, data): self.store[key] = data


def _client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cm, "CacheManager", _MemCache)
    _MemCache.store = {}
    c = ClaudeClient(project="")
    calls = []

    def create(**kw):
        user = kw["messages"][0]["content"]
        text = user if isinstance(user, str) else " ".join(
            p.get("text", "") for p in user if isinstance(p, dict))
        tag = "A" if "CHUNK_A" in text else "B"
        calls.append(tag)
        return types.SimpleNamespace(stop_reason="end_turn",
                                     content=[types.SimpleNamespace(text='{"title": "case %s"}]' % tag)])
    c.client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
    return c, calls


def test_each_chunk_gets_its_own_cache_entry(monkeypatch, tmp_path):
    c, calls = _client(monkeypatch)
    f = str(tmp_path / "big.py")
    a = c.analyze_code(code="def a(): return 1  # CHUNK_A", file_path=f, cache_scope="chunk-1", use_cache=True)
    b = c.analyze_code(code="def b(): return 2  # CHUNK_B", file_path=f, cache_scope="chunk-2", use_cache=True)
    assert a[0]["title"] == "case A" and b[0]["title"] == "case B"   # chunk 2 really analyzed
    assert calls == ["A", "B"]
    again = c.analyze_code(code="def b(): return 2  # CHUNK_B", file_path=f, cache_scope="chunk-2", use_cache=True)
    assert again[0]["title"] == "case B" and calls == ["A", "B"]      # per-chunk cache hit, no call


def test_unscoped_whole_file_key_is_unchanged():
    assert ClaudeClient._scoped_cache_key("K", None) == "K"           # whole-file calls keep their key
    assert ClaudeClient._scoped_cache_key("K", "chunk-1") != "K"
    assert ClaudeClient._scoped_cache_key("K", "chunk-1") != ClaudeClient._scoped_cache_key("K", "chunk-2")
