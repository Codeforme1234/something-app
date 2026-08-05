"""Request/response DTOs for test authoring.

The size caps here (title/stem/option lengths, exactly 4 options, max 100
questions per test) are a security boundary — they bound DynamoDB item size
and request payload size — not just UX guardrails. Don't loosen them without
reconsidering MAX_BODY_BYTES in app/main.py too.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.models.question import Question
from app.models.test import Difficulty, Test, TestStatus


def _stripped_nonblank(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be blank")
    return stripped


class CreateTestRequest(BaseModel):
    """All fields default so `POST /tests` with an empty body `{}` works --
    the dashboard's "New test" button creates a draft with no settings step
    at all. The teacher renames/adjusts it afterwards via PATCH from the
    editor, which uses UpdateTestRequest below."""

    title: Annotated[str, Field(min_length=1, max_length=200)] = "New test"
    difficulty: Difficulty = Difficulty.medium
    duration_seconds: Annotated[int, Field(ge=60, le=14400)] = 900

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        return _stripped_nonblank(v, "title")


class UpdateTestRequest(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    difficulty: Difficulty | None = None
    duration_seconds: Annotated[int, Field(ge=60, le=14400)] | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str | None) -> str | None:
        return _stripped_nonblank(v, "title") if v is not None else None


class QuestionInput(BaseModel):
    stem: Annotated[str, Field(min_length=1, max_length=1000)]
    options: Annotated[list[str], Field(min_length=4, max_length=4)]
    correct_index: Annotated[int, Field(ge=0, le=3)]

    @field_validator("stem")
    @classmethod
    def _strip_stem(cls, v: str) -> str:
        return _stripped_nonblank(v, "stem")

    @field_validator("options")
    @classmethod
    def _validate_options(cls, options: list[str]) -> list[str]:
        stripped: list[str] = []
        for opt in options:
            s = opt.strip()
            if not (1 <= len(s) <= 300):
                raise ValueError("each option must be 1-300 characters after stripping")
            stripped.append(s)
        if len(set(stripped)) != len(stripped):
            raise ValueError("options must not contain duplicate values")
        return stripped


class PutQuestionsRequest(BaseModel):
    questions: Annotated[list[QuestionInput], Field(max_length=100)]


class GenerateQuestionsRequest(BaseModel):
    topic: Annotated[str, Field(min_length=1, max_length=300)]
    count: Annotated[int, Field(ge=1, le=20)]
    difficulty: Difficulty
    # Optional source text (e.g. from an uploaded .txt/.md file) the model
    # should base questions on instead of general knowledge about the topic.
    # Capped well under MAX_BODY_BYTES; extraction/truncation happens
    # client-side, this is just a hard backstop.
    knowledge_base: Annotated[str, Field(max_length=20_000)] | None = None

    @field_validator("topic")
    @classmethod
    def _strip_topic(cls, v: str) -> str:
        return _stripped_nonblank(v, "topic")

    @field_validator("knowledge_base")
    @classmethod
    def _strip_knowledge_base(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None


class GeneratedQuestion(BaseModel):
    """Same shape as QuestionInput, renamed because nothing is persisted by
    generation -- there is no question_id yet, only a draft for the teacher
    to review in the question editor before saving."""

    stem: str
    options: list[str]
    correct_index: int


class GenerateQuestionsResponse(BaseModel):
    questions: list[GeneratedQuestion]


class TestSummary(BaseModel):
    test_id: str
    title: str
    difficulty: Difficulty
    duration_seconds: int
    status: TestStatus
    deadline: datetime | None
    question_count: int
    student_count: int
    created_at: datetime

    @classmethod
    def from_model(cls, test: Test) -> "TestSummary":
        return cls(
            test_id=test.test_id,
            title=test.title,
            difficulty=test.difficulty,
            duration_seconds=test.duration_seconds,
            status=test.status,
            deadline=test.deadline,
            question_count=test.question_count,
            student_count=test.student_count,
            created_at=test.created_at,
        )


class QuestionOut(BaseModel):
    question_id: str
    order: int
    stem: str
    options: list[str]
    correct_index: int

    @classmethod
    def from_model(cls, question: Question) -> "QuestionOut":
        return cls(**question.model_dump())


class TestDetail(TestSummary):
    questions: list[QuestionOut]

    @classmethod
    def from_models(cls, test: Test, questions: list[Question]) -> "TestDetail":
        return cls(
            **TestSummary.from_model(test).model_dump(),
            questions=[QuestionOut.from_model(q) for q in questions],
        )
