"""FakeMCQGenerator must always produce output that passes the same
validation layer real generated output goes through, for every count and
difficulty a teacher can request."""

import pytest

from app.llm.fake import FakeMCQGenerator
from app.llm.schemas import GeneratedMCQSet
from app.models.test import Difficulty


@pytest.mark.parametrize("count", [1, 5, 20])
@pytest.mark.parametrize("difficulty", list(Difficulty))
def test_fake_generator_passes_full_validation(count, difficulty):
    questions = FakeMCQGenerator().generate("photosynthesis", count, difficulty)

    # Already-constructed GeneratedMCQ objects passed their own validators;
    # re-assembling the set exercises the set-level rules (duplicate stems,
    # exact count) too.
    question_set = GeneratedMCQSet(questions=questions)
    question_set.validate_count(count)


def test_fake_generator_returns_requested_count():
    questions = FakeMCQGenerator().generate("history", 7, Difficulty.medium)
    assert len(questions) == 7


def test_fake_generator_varies_correct_index():
    questions = FakeMCQGenerator().generate("history", 5, Difficulty.medium)
    assert len({q.correct_index for q in questions}) > 1


def test_fake_generator_incorporates_topic():
    questions = FakeMCQGenerator().generate("photosynthesis", 1, Difficulty.easy)
    q = questions[0]
    assert "photosynthesis" in q.stem
    assert any("photosynthesis" in opt for opt in q.options)


def test_fake_generator_works_with_no_network_or_key(monkeypatch):
    # No API key set anywhere -- fake mode must not need one.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    questions = FakeMCQGenerator().generate("anything", 3, Difficulty.hard)
    assert len(questions) == 3
