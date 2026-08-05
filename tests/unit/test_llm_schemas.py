"""Unit tests for the LLM output validation layer. Same caps as
app.schemas.tests.QuestionInput (see tests/unit/test_test_schemas.py), plus
the set-level "no duplicate stems" and "exactly N questions" rules that only
make sense once several generated questions are considered together."""

import pytest
from pydantic import ValidationError

from app.llm.schemas import GeneratedMCQ, GeneratedMCQSet


def _options() -> list[str]:
    return ["Alpha", "Beta", "Gamma", "Delta"]


def _mcq(stem: str = "Q?", options: list[str] | None = None, correct_index: int = 0) -> GeneratedMCQ:
    return GeneratedMCQ(stem=stem, options=options or _options(), correct_index=correct_index)


def test_requires_exactly_four_options():
    with pytest.raises(ValidationError):
        GeneratedMCQ(stem="Q?", options=_options()[:3], correct_index=0)
    with pytest.raises(ValidationError):
        GeneratedMCQ(stem="Q?", options=[*_options(), "Extra"], correct_index=0)


def test_options_must_be_unique_within_a_question():
    with pytest.raises(ValidationError):
        GeneratedMCQ(stem="Q?", options=["A", "A", "B", "C"], correct_index=0)


def test_options_are_stripped_and_nonblank():
    with pytest.raises(ValidationError):
        GeneratedMCQ(stem="Q?", options=["A", "   ", "B", "C"], correct_index=0)


def test_option_max_length_enforced():
    with pytest.raises(ValidationError):
        GeneratedMCQ(stem="Q?", options=["x" * 301, "B", "C", "D"], correct_index=0)


def test_stem_must_be_nonblank_after_strip():
    with pytest.raises(ValidationError):
        GeneratedMCQ(stem="   ", options=_options(), correct_index=0)


def test_stem_is_stripped():
    mcq = GeneratedMCQ(stem="  Hello?  ", options=_options(), correct_index=0)
    assert mcq.stem == "Hello?"


def test_stem_max_length_enforced():
    with pytest.raises(ValidationError):
        GeneratedMCQ(stem="x" * 1001, options=_options(), correct_index=0)


@pytest.mark.parametrize("index", [-1, 4])
def test_correct_index_out_of_range_rejected(index):
    with pytest.raises(ValidationError):
        GeneratedMCQ(stem="Q?", options=_options(), correct_index=index)


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_correct_index_boundary_accepted(index):
    GeneratedMCQ(stem="Q?", options=_options(), correct_index=index)


def test_set_rejects_duplicate_stems():
    with pytest.raises(ValidationError):
        GeneratedMCQSet(questions=[_mcq(stem="Same?"), _mcq(stem="Same?")])


def test_set_accepts_unique_stems():
    GeneratedMCQSet(questions=[_mcq(stem="One?"), _mcq(stem="Two?")])


def test_duplicate_stem_check_compares_after_stripping():
    with pytest.raises(ValidationError):
        GeneratedMCQSet(questions=[_mcq(stem="Same?"), _mcq(stem="  Same?  ")])


def test_validate_count_accepts_exact_match():
    question_set = GeneratedMCQSet(questions=[_mcq(stem="One?"), _mcq(stem="Two?")])
    question_set.validate_count(2)


def test_validate_count_rejects_mismatch():
    question_set = GeneratedMCQSet(questions=[_mcq(stem="One?"), _mcq(stem="Two?")])
    with pytest.raises(ValueError, match="expected exactly 3"):
        question_set.validate_count(3)
