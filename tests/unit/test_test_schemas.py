"""Unit tests for the strict validation caps on test/question DTOs. These
caps are a security boundary (bounding DynamoDB item size and payload size),
not just UX guardrails — see app/schemas/tests.py."""

import pytest
from pydantic import ValidationError

from app.schemas.tests import CreateTestRequest, PutQuestionsRequest, QuestionInput, UpdateTestRequest


def _options() -> list[str]:
    return ["Alpha", "Beta", "Gamma", "Delta"]


def test_title_must_be_nonblank_after_strip():
    with pytest.raises(ValidationError):
        CreateTestRequest(title="   ", difficulty="easy", duration_seconds=600)


def test_title_is_stripped():
    req = CreateTestRequest(title="  Hello  ", difficulty="easy", duration_seconds=600)
    assert req.title == "Hello"


def test_title_max_length_enforced():
    with pytest.raises(ValidationError):
        CreateTestRequest(title="x" * 201, difficulty="easy", duration_seconds=600)


@pytest.mark.parametrize("seconds", [59, 14401])
def test_duration_seconds_out_of_range_rejected(seconds):
    with pytest.raises(ValidationError):
        CreateTestRequest(title="T", difficulty="easy", duration_seconds=seconds)


@pytest.mark.parametrize("seconds", [60, 14400])
def test_duration_seconds_boundary_accepted(seconds):
    CreateTestRequest(title="T", difficulty="easy", duration_seconds=seconds)


def test_update_request_allows_all_fields_omitted():
    req = UpdateTestRequest()
    assert req.model_dump(exclude_unset=True) == {}


def test_update_request_rejects_blank_title():
    with pytest.raises(ValidationError):
        UpdateTestRequest(title="   ")


def test_question_requires_exactly_four_options():
    with pytest.raises(ValidationError):
        QuestionInput(stem="Q?", options=_options()[:3], correct_index=0)
    with pytest.raises(ValidationError):
        QuestionInput(stem="Q?", options=[*_options(), "Extra"], correct_index=0)


def test_question_options_must_be_unique():
    with pytest.raises(ValidationError):
        QuestionInput(stem="Q?", options=["A", "A", "B", "C"], correct_index=0)


def test_question_options_stripped_and_nonblank():
    with pytest.raises(ValidationError):
        QuestionInput(stem="Q?", options=["A", "   ", "B", "C"], correct_index=0)


def test_question_option_max_length_enforced():
    with pytest.raises(ValidationError):
        QuestionInput(stem="Q?", options=["x" * 301, "B", "C", "D"], correct_index=0)


def test_question_stem_must_be_nonblank_after_strip():
    with pytest.raises(ValidationError):
        QuestionInput(stem="   ", options=_options(), correct_index=0)


@pytest.mark.parametrize("index", [-1, 4])
def test_question_correct_index_out_of_range_rejected(index):
    with pytest.raises(ValidationError):
        QuestionInput(stem="Q?", options=_options(), correct_index=index)


def test_put_questions_rejects_more_than_100():
    questions = [
        QuestionInput(stem=f"Q{i}", options=_options(), correct_index=0) for i in range(101)
    ]
    with pytest.raises(ValidationError):
        PutQuestionsRequest(questions=questions)


def test_put_questions_accepts_exactly_100():
    questions = [
        QuestionInput(stem=f"Q{i}", options=_options(), correct_index=0) for i in range(100)
    ]
    PutQuestionsRequest(questions=questions)
