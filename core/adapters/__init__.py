"""
core/adapters/__init__.py — Output Adapter Layer
=================================================
Exports all adapter classes and a factory helper.

Available adapters:
  xray    → Push to Jira/Xray via existing XrayClient
  zephyr  → Push to Zephyr Scale REST API v2
  excel   → Save to .xlsx file via openpyxl
  none    → Hold in memory only (skip export)

Usage:
    from core.adapters import get_adapter
    adapter = get_adapter("excel")
    result  = adapter.export(test_cases, project="myapp", dry_run=True)
"""

from core.adapters.base import BaseExportAdapter
from core.adapters.xray_adapter import XrayAdapter
from core.adapters.zephyr_adapter import ZephyrAdapter
from core.adapters.excel_adapter import ExcelAdapter
from core.adapters.csv_adapter import CSVAdapter
from core.adapters.memory_adapter import MemoryAdapter

# ── Registry: name → class ────────────────────────────────────────────────────
_REGISTRY: dict[str, type[BaseExportAdapter]] = {
    "xray":   XrayAdapter,
    "zephyr": ZephyrAdapter,
    "excel":  ExcelAdapter,
    "csv":    CSVAdapter,
    "none":   MemoryAdapter,
}


def get_adapter(name: str) -> BaseExportAdapter:
    """
    Instantiate and return an export adapter by name.

    Args:
        name (str): Adapter key — "xray", "zephyr", "excel", or "none".
                    Unknown names fall back to MemoryAdapter.

    Returns:
        BaseExportAdapter: Ready-to-use adapter instance.
    """
    cls = _REGISTRY.get((name or "none").lower(), MemoryAdapter)
    return cls()


__all__ = [
    "BaseExportAdapter",
    "XrayAdapter",
    "ZephyrAdapter",
    "ExcelAdapter",
    "CSVAdapter",
    "MemoryAdapter",
    "get_adapter",
]
