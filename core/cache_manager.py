"""
cache_manager.py — File-level Result Cache
===========================================
Caches Claude API results keyed by MD5 hash of the source file content
and modification time. On a cache hit the API call is skipped entirely,
saving time and cost on repeated analysis of unchanged files.

Cache files are stored as JSON in output/cache/.
The cache is keyed per-file; re-running analysis on an edited file
automatically invalidates the old cache entry.
"""

import hashlib
import json
import logging
import os
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Default cache directory ────────────────────────────────────────────────────
DEFAULT_CACHE_DIR = Path(__file__).parent.parent / "output" / "cache"


class CacheManager:
    """
    File-level result cache for Claude analysis outputs.

    Keys are MD5 hashes of (file content + mtime) — if the file changes,
    the key changes and the old cache entry is never returned.

    Usage:
        cache = CacheManager()
        key = cache.get_key("/path/to/Controller.cs")
        cached = cache.get(key)
        if cached is not None:
            return cached
        result = claude.analyze_code(...)
        cache.set(key, result)
    """

    def __init__(self, cache_dir: str | None = None):
        """
        Initialize the CacheManager and create the cache directory if needed.

        Args:
            cache_dir (str | None): Path to the cache directory.
                                    Defaults to output/cache/.
        """
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("CacheManager initialized at %s", self.cache_dir)

    def get_key(self, file_path: str) -> str | None:
        """
        Compute a cache key for a source file using MD5 of content + mtime.

        The key incorporates both the file content (detects any edits) and
        the modification timestamp (extra guard for same-content renames).
        Returns None if the file cannot be read, so callers can skip caching.

        Args:
            file_path (str): Absolute or relative path to the source file.

        Returns:
            str | None: 32-character hex MD5 string, or None on error.
        """
        try:
            path = Path(file_path)
            if not path.exists():
                return None
            content = path.read_bytes()
            mtime = str(path.stat().st_mtime).encode()
            digest = hashlib.md5(content + mtime).hexdigest()
            return digest
        except Exception as e:
            logger.debug("Could not compute cache key for %s: %s", file_path, e)
            return None

    def get(self, key: str) -> list | None:
        """
        Return cached test cases for the given key, or None on miss.

        Args:
            key (str): Cache key from get_key().

        Returns:
            list | None: Cached list of test case dicts, or None on cache miss.
        """
        if not key:
            return None
        cache_file = self.cache_dir / f"{key}.json"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)
            logger.info("Cache hit — skipping API call (key: %s...)", key[:8])
            return data
        except Exception as e:
            logger.debug("Cache read failed for key %s: %s", key[:8], e)
            return None

    def set(self, key: str, data: list) -> None:
        """
        Write test case results to cache under the given key.

        Args:
            key (str): Cache key from get_key().
            data (list): List of test case dicts to cache.
        """
        if not key or data is None:
            return
        cache_file = self.cache_dir / f"{key}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug("Cache written (key: %s..., %d cases)", key[:8], len(data))
        except Exception as e:
            logger.debug("Cache write failed for key %s: %s", key[:8], e)

    def clear(self) -> int:
        """
        Delete all cached JSON files from the cache directory.

        Returns:
            int: Number of files deleted.
        """
        deleted = 0
        for cache_file in self.cache_dir.glob("*.json"):
            try:
                cache_file.unlink()
                deleted += 1
            except Exception as e:
                logger.debug("Could not delete cache file %s: %s", cache_file, e)
        logger.info("Cache cleared — %d file(s) deleted", deleted)
        return deleted

    def stats(self) -> dict:
        """
        Return cache statistics: file count and total size in MB.

        Returns:
            dict: {"count": int, "size_mb": float}
        """
        files = list(self.cache_dir.glob("*.json"))
        total_bytes = sum(f.stat().st_size for f in files if f.exists())
        return {
            "count": len(files),
            "size_mb": round(total_bytes / 1_048_576, 3),
        }
