"""Unit tests for the strict validation caps on test/question DTOs. These
caps are a security boundary (bounding DynamoDB item size and payload size),
not just UX guardrails — see app/schemas/tests.py."""

import pytest
from pydantic import ValidationError

from app.schemas.tests import (
    CreateTestRequest,
    GenerateQuestionsRequest,
    PutQuestionsRequest,
    QuestionInput,
    UpdateTestRequest,
)


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


def test_question_stem_keeps_allowed_rich_text_formatting():
    req = QuestionInput(
        stem="<p>Which is <strong>correct</strong>?</p>", options=_options(), correct_index=0
    )
    assert req.stem == "<p>Which is <strong>correct</strong>?</p>"


def test_question_stem_strips_disallowed_tags_and_attributes():
    req = QuestionInput(
        stem='<p onclick="evil()">Q<script>alert(1)</script>?</p>', options=_options(), correct_index=0
    )
    assert "<script>" not in req.stem
    assert "onclick" not in req.stem
    assert "Q" in req.stem


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


def test_generate_questions_topic_must_be_nonblank_after_strip():
    with pytest.raises(ValidationError):
        GenerateQuestionsRequest(topic="   ", count=5, difficulty="medium")


def test_generate_questions_topic_is_stripped():
    req = GenerateQuestionsRequest(topic="  Photosynthesis  ", count=5, difficulty="medium")
    assert req.topic == "Photosynthesis"


def test_generate_questions_topic_max_length_enforced():
    with pytest.raises(ValidationError):
        GenerateQuestionsRequest(topic="x" * 301, count=5, difficulty="medium")


@pytest.mark.parametrize("count", [0, 21])
def test_generate_questions_count_out_of_range_rejected(count):
    with pytest.raises(ValidationError):
        GenerateQuestionsRequest(topic="Topic", count=count, difficulty="medium")


@pytest.mark.parametrize("count", [1, 20])
def test_generate_questions_count_boundary_accepted(count):
    GenerateQuestionsRequest(topic="Topic", count=count, difficulty="medium")


def test_create_test_request_defaults_when_body_is_empty():
    """The dashboard's "New test" button posts an empty body -- no settings
    step at all -- so every field must have a usable default."""
    req = CreateTestRequest()
    assert req.title == "New test"
    assert req.difficulty == "medium"
    assert req.duration_seconds == 900


def test_generate_questions_knowledge_base_defaults_to_none():
    req = GenerateQuestionsRequest(topic="Topic", count=5, difficulty="medium")
    assert req.knowledge_base is None


def test_generate_questions_knowledge_base_is_stripped_and_blank_becomes_none():
    req = GenerateQuestionsRequest(topic="Topic", count=5, difficulty="medium", knowledge_base="   ")
    assert req.knowledge_base is None

    req = GenerateQuestionsRequest(
        topic="Topic", count=5, difficulty="medium", knowledge_base="  some notes  "
    )
    assert req.knowledge_base == "some notes"


def test_generate_questions_knowledge_base_max_length_enforced():
    with pytest.raises(ValidationError):
        GenerateQuestionsRequest(
            topic="Topic", count=5, difficulty="medium", knowledge_base="x" * 20_001
        )
