"""Request/response DTOs for the student attempt flow (`app/routers/take.py`).

`correct_index` must never appear on any model in this module -- see
CLAUDE.md rule 4 and tests/unit/test_take_schemas.py, which walks
`model_fields` recursively to enforce it. Every response carries
`server_now` so the client can compute its clock offset against the server
instead of trusting its own clock for the countdown.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.question import Question
from app.models.session import SessionStatus


class TakeInfo(BaseModel):
    test_title: str
    duration_seconds: int
    question_count: int
    deadline: datetime | None
    session_status: SessionStatus
    student_name: str
    ends_at: datetime | None  # set once the session has been started
    server_now: datetime


class TakeQuestion(BaseModel):
    question_id: str
    order: int
    stem: str
    options: list[str]

    @classmethod
    def from_model(cls, question: Question) -> "TakeQuestion":
        return cls(
            question_id=question.question_id,
            order=question.order,
            stem=question.stem,
            options=question.options,
        )


class StartAttemptResponse(BaseModel):
    questions: list[TakeQuestion]
    ends_at: datetime
    server_now: datetime


class SubmitRequest(BaseModel):
    # extra="forbid" + a capped, strictly-typed dict is the "reject
    # unknown-shaped payloads hard" requirement: no surprise top-level
    # fields, no more than 100 answers, no out-of-range option index.
    model_config = ConfigDict(extra="forbid")

    answers: Annotated[dict[str, int], Field(max_length=100)]

    @field_validator("answers")
    @classmethod
    def _validate_answer_values(cls, answers: dict[str, int]) -> dict[str, int]:
        for value in answers.values():
            if not (0 <= value <= 3):
                raise ValueError("each answer value must be between 0 and 3")
        return answers


class SubmitResponse(BaseModel):
    """No score, no correct answers -- just an acknowledgement."""

    status: Literal["submitted"] = "submitted"
