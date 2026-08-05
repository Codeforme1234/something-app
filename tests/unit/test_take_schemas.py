"""Unit tests for app/schemas/take.py -- the student-facing wire contract.

The critical invariant (CLAUDE.md rule 4 / app/models/question.py has
`correct_index`) is that none of these response models can leak it. Walk
`model_fields` recursively rather than spot-checking one field name, so a
future nested model can't reintroduce it unnoticed.
"""

from datetime import UTC, datetime, timedelta
from typing import get_args, get_origin

import pytest
from pydantic import BaseModel, ValidationError

from app.core.clock import now
from app.models.question import Question
from app.schemas.take import (
    StartAttemptResponse,
    SubmitRequest,
    SubmitResponse,
    TakeInfo,
    TakeQuestion,
)


def _model_classes_reachable_from(model_cls: type[BaseModel], seen: set[type] | None = None) -> set[type]:
    seen = seen if seen is not None else set()
    if model_cls in seen:
        return seen
    seen.add(model_cls)
    for field in model_cls.model_fields.values():
        for candidate in (field.annotation, *get_args(field.annotation)):
            candidate = get_origin(candidate) or candidate
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                _model_classes_reachable_from(candidate, seen)
    return seen


@pytest.mark.parametrize("root", [TakeInfo, TakeQuestion, StartAttemptResponse, SubmitRequest, SubmitResponse])
def test_no_model_reachable_from_take_schemas_has_correct_index(root):
    for model_cls in _model_classes_reachable_from(root):
        assert "correct_index" not in model_cls.model_fields, (
            f"{model_cls.__name__} must never expose correct_index to students"
        )


def test_take_question_from_model_does_not_carry_correct_index():
    question = Question(question_id="q1", order=1, stem="2+2?", options=["3", "4", "5", "6"], correct_index=1)

    take_question = TakeQuestion.from_model(question)

    assert "correct_index" not in take_question.model_dump()
    assert take_question.question_id == "q1"
    assert take_question.options == ["3", "4", "5", "6"]


def test_submit_response_is_only_a_status():
    resp = SubmitResponse()
    assert resp.model_dump() == {"status": "submitted"}


# --- SubmitRequest validation ------------------------------------------------


def test_submit_request_accepts_valid_answers():
    req = SubmitRequest(answers={"q1": 0, "q2": 3})
    assert req.answers == {"q1": 0, "q2": 3}


def test_submit_request_rejects_out_of_range_value():
    with pytest.raises(ValidationError):
        SubmitRequest(answers={"q1": 4})


def test_submit_request_rejects_negative_value():
    with pytest.raises(ValidationError):
        SubmitRequest(answers={"q1": -1})


def test_submit_request_rejects_more_than_100_entries():
    answers = {f"q{i}": 0 for i in range(101)}
    with pytest.raises(ValidationError):
        SubmitRequest(answers=answers)


def test_submit_request_accepts_exactly_100_entries():
    answers = {f"q{i}": 0 for i in range(100)}
    SubmitRequest(answers=answers)


def test_submit_request_rejects_unknown_top_level_field():
    with pytest.raises(ValidationError):
        SubmitRequest(answers={"q1": 0}, score=100)


def test_take_info_round_trips_fields():
    deadline = now() + timedelta(days=1)
    info = TakeInfo(
        test_title="Algebra",
        duration_seconds=600,
        question_count=2,
        deadline=deadline,
        session_status="invited",
        student_name="Ada",
        ends_at=None,
        server_now=datetime.now(UTC),
    )
    assert info.ends_at is None
    assert info.session_status == "invited"
