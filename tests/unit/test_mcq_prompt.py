"""Unit tests for the MCQ prompt templates -- prompt text lives only in
app/llm/prompts/mcq_generation.py (CLAUDE.md rule), so this is where its
content is worth pinning down with tests."""

from app.llm.prompts.mcq_generation import render_mcq_prompt
from app.models.test import Difficulty


def test_prompt_without_knowledge_base_has_no_source_material_section():
    _, user_prompt = render_mcq_prompt("Photosynthesis", 5, Difficulty.medium)
    assert "Source material" not in user_prompt
    assert "Photosynthesis" in user_prompt


def test_prompt_with_knowledge_base_includes_it_verbatim():
    kb = "Chlorophyll absorbs red and blue light but reflects green light."
    _, user_prompt = render_mcq_prompt("Photosynthesis", 3, Difficulty.easy, kb)
    assert "Source material" in user_prompt
    assert kb in user_prompt
    # The instruction to prefer the material over general knowledge must
    # actually be present, not just the raw text dumped in.
    assert "general knowledge" in user_prompt


def test_prompt_with_blank_knowledge_base_is_treated_as_absent():
    _, user_prompt = render_mcq_prompt("Topic", 5, Difficulty.medium, "")
    assert "Source material" not in user_prompt
