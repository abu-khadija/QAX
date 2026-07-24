"""
test_istqb_knowledge.py — the ISTQB knowledge base
==================================================
The knowledge is fixed doctrine, so these assert its shape and that the prompt
block carries the actionable techniques (especially BVA for pagination).

Run with:
    pytest tests/test_istqb_knowledge.py -v
"""

from core import istqb_knowledge as k


def test_seven_principles():
    assert len(k.PRINCIPLES) == 7
    assert all("name" in p and "apply" in p for p in k.PRINCIPLES)


def test_five_core_techniques_present():
    names = " ".join(t["name"] for t in k.TECHNIQUES)
    for technique in ["Equivalence Partitioning", "Boundary Value Analysis",
                      "Decision Table", "State Transition", "Use Case"]:
        assert technique in names
    assert all("what" in t and "apply" in t for t in k.TECHNIQUES)


def test_prompt_block_is_actionable():
    block = k.prompt_block()
    assert "Boundary Value Analysis" in block
    # BVA must be concrete about pagination / computed counts — the gap it closes.
    assert "page_size" in block or "page size" in block
    assert "ceil(" in block
    assert "expect_json" in block


def test_prompt_block_names_all_techniques():
    block = k.prompt_block()
    for t in k.TECHNIQUES:
        assert t["name"] in block
