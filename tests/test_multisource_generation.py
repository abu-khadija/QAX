"""
test_multisource_generation.py — GenerationService.generate_from_source
========================================================================
Tests the multi-source orchestration layer: input adapter -> items -> loop ->
generate -> aggregated BatchGenerationResult. Fully offline — the input adapter
and per-item generation are both stubbed.

Run with:
    pytest tests/test_multisource_generation.py -v
"""

from unittest.mock import MagicMock, patch

import pytest

from services.context import QAXContext
from services.exceptions import QAXConfigError
from services.generation_service import GenerationOptions, GenerationService
from services.results import BatchGenerationResult, GenerationResult


def _ctx() -> QAXContext:
    return QAXContext(anthropic_key="k", project="")


def _item(source_type="folder", file_path="a.py", content="def f(): pass", **meta) -> dict:
    return {
        "file_path": file_path,
        "content": content,
        "file_name": file_path,
        "source_type": source_type,
        "metadata": meta,
    }


class _FakeAdapter:
    """Input adapter stub that returns a fixed item list."""

    def __init__(self, items):
        self._items = items

    def fetch(self, source, **kwargs):
        return self._items


def _tc(i=1) -> dict:
    return {"test_case_id": f"TC-{i}", "title": "t", "confidence_score": 8}


# ─────────────────────────────────────────────────────────────────────────────
# Config + empty-source guards
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_key_raises():
    with pytest.raises(QAXConfigError):
        GenerationService(QAXContext(anthropic_key="")).generate_from_source(
            "x", input_type="folder"
        )


def test_empty_source_returns_error():
    with patch("core.input_adapters.get_input_adapter", return_value=_FakeAdapter([])):
        result = GenerationService(_ctx()).generate_from_source("x", input_type="folder")
    assert isinstance(result, BatchGenerationResult)
    assert result.success is False
    assert result.error
    assert result.count == 0


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation across items
# ─────────────────────────────────────────────────────────────────────────────

def test_folder_aggregates_two_items():
    items = [_item(file_path="a.py"), _item(file_path="b.py")]
    svc = GenerationService(_ctx())
    svc.generate = MagicMock(side_effect=[
        GenerationResult(test_cases=[_tc(1)]),
        GenerationResult(test_cases=[_tc(2)]),
    ])
    with patch("core.input_adapters.get_input_adapter", return_value=_FakeAdapter(items)):
        result = svc.generate_from_source("folder/", input_type="folder", ext=".py")

    assert result.item_count == 2
    assert result.count == 2
    assert svc.generate.call_count == 2


def test_batch_to_dict_shape():
    svc = GenerationService(_ctx())
    svc.generate = MagicMock(
        return_value=GenerationResult(test_cases=[_tc(1)], summary={"avg_confidence": 8})
    )
    with patch("core.input_adapters.get_input_adapter", return_value=_FakeAdapter([_item()])):
        result = svc.generate_from_source("x", input_type="folder")

    data = result.to_dict()
    assert data["count"] == 1
    assert data["success"] is True
    assert isinstance(data["test_cases"], list)


# ─────────────────────────────────────────────────────────────────────────────
# Non-disk sources (story/jira) pass content through, not a disk path
# ─────────────────────────────────────────────────────────────────────────────

def test_story_item_passes_content_through():
    item = _item(source_type="story", file_path="story://x",
                 content="As a user I want to log in")
    svc = GenerationService(_ctx())
    captured = {}

    def _fake_generate(source, *, options=None, progress=None):
        captured["source"] = source
        captured["options"] = options
        return GenerationResult(test_cases=[_tc(1)])

    svc.generate = _fake_generate
    with patch("core.input_adapters.get_input_adapter", return_value=_FakeAdapter([item])):
        result = svc.generate_from_source("As a user...", input_type="story")

    assert result.count == 1
    # content is passed directly (no disk read), and reused as the user story
    assert captured["options"].content == "As a user I want to log in"
    assert captured["options"].user_story == "As a user I want to log in"
    assert captured["options"].include_git is False
