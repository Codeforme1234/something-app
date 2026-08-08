"""Request/response DTOs for test authoring.

The size caps here (title/stem/option lengths, exactly 4 options, max 100
questions per test) are a security boundary — they bound DynamoDB item size
and request payload size — not just UX guardrails. Don't loosen them without
reconsidering MAX_BODY_BYTES in app/main.py too.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.core.rich_text import rich_text_to_plain, sanitize_rich_text
from app.models.question import Question
from app.models.test import Difficulty, Test, TestStatus


def _stripped_nonblank(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be blank")
    return stripped


def _visible_text(html: str) -> str:
    """The text a reader would see, for deciding whether an optional rich-text
    field is really empty -- an untouched Tiptap editor still posts "<p></p>"."""
    return rich_text_to_plain(html).strip()


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
    # The stem is rich text (a Tiptap-produced HTML fragment), not plain
    # text -- max_length here is a raw-size backstop against pathological
    # markup bloat; the real "1-1000 characters" rule is enforced against
    # the VISIBLE text by sanitize_rich_text, which also strips the HTML
    # down to the small allowed tag set before it's ever stored.
    stem: Annotated[str, Field(min_length=1, max_length=6000)]
    options: Annotated[list[str], Field(min_length=4, max_length=4)]
    correct_index: Annotated[int, Field(ge=0, le=3)]
    # Object-store key from POST /tests/{id}/question-images, echoed back here
    # by the editor. Validated against *this* test in test_service before it is
    # trusted -- a key is a path fragment we later render into a URL.
    image_key: Annotated[str, Field(max_length=200)] | None = None
    # Plain text for the <img alt> attribute. Deliberately NOT run through
    # sanitize_rich_text: that returns HTML and raises on blank, and React
    # escapes attribute values anyway.
    image_alt: Annotated[str, Field(max_length=300)] | None = None

    @field_validator("stem")
    @classmethod
    def _sanitize_stem(cls, v: str) -> str:
        return sanitize_rich_text(v, "stem", max_visible_chars=1000)

    @field_validator("image_alt")
    @classmethod
    def _strip_image_alt(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None

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


#: How many questions a prompt-only run produces when the teacher does not say.
#: Applied in exactly one place -- app.services.generation_job._resolve_count --
#: and never to a PDF run, which takes its count from the paper instead.
DEFAULT_QUESTION_COUNT = 10


class GenerateQuestionsRequest(BaseModel):
    topic: Annotated[str, Field(min_length=1, max_length=300)]
    # None means "the teacher did not say", which is NOT the same as 10: a PDF
    # run derives its count from the paper's own numbering and ignores this
    # field entirely, so a default here would silently cap a 75-question paper
    # at whatever number happened to be baked in. Only the prompt path falls
    # back to DEFAULT_QUESTION_COUNT.
    count: Annotated[int, Field(ge=1, le=20)] | None = None

    @property
    def effective_count(self) -> int:
        """How many questions to ask a *generator* for.

        The one place DEFAULT_QUESTION_COUNT is applied. Extraction never calls
        this -- a PDF's count is the paper's -- which is the whole reason `count`
        is nullable rather than defaulted on the field.
        """
        return self.count or DEFAULT_QUESTION_COUNT
    difficulty: Difficulty = Difficulty.medium
    # Free-text instructions from the teacher: style, scope, what to avoid. A
    # Tiptap fragment like a question stem, because a teacher writing several
    # rules wants a list. Sanitized on the way in and flattened to plain text
    # before it reaches a prompt (app.core.rich_text.rich_text_to_plain).
    guidelines: Annotated[str, Field(max_length=8000)] | None = None
    # Text read out of the uploaded source document. Extraction happens
    # server-side now (a PDF needs a parser, a photo needs vision) -- see
    # app/services/knowledge_base.py, which caps it at this same length.
    knowledge_base: Annotated[str, Field(max_length=20_000)] | None = None
    # Where that document is stored, so the finished test can link back to what
    # it was generated from. Validated against the caller's own namespace.
    knowledge_base_key: Annotated[str, Field(max_length=200)] | None = None

    @field_validator("topic")
    @classmethod
    def _strip_topic(cls, v: str) -> str:
        return _stripped_nonblank(v, "topic")

    @field_validator("guidelines")
    @classmethod
    def _sanitize_guidelines(cls, v: str | None) -> str | None:
        if v is None:
            return None
        # An empty editor still posts "<p></p>", so blank has to mean None here
        # rather than the ValueError sanitize_rich_text raises for a required
        # field like a question stem.
        if not _visible_text(v):
            return None
        return sanitize_rich_text(v, "guidelines", max_visible_chars=4000)

    @field_validator("knowledge_base", "knowledge_base_key")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
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
    # Only ever set alongside status=generation_failed. Carried on the summary
    # rather than the detail because the dashboard card is where a teacher sees
    # the failure -- they never open a test that has no questions.
    generation_error: str | None = None

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
            generation_error=test.generation_error,
        )


class QuestionOut(BaseModel):
    question_id: str
    order: int
    stem: str  # sanitized HTML fragment (see QuestionInput) -- render, don't escape
    # Plain strings rendered as React text children, never as HTML -- which is
    # why, unlike `stem`, they need no sanitizer. If an option is ever rendered
    # with dangerouslySetInnerHTML, that stops being true.
    options: list[str]
    correct_index: int
    # This model carries BOTH the key and the URL, unlike the student- and
    # review-facing ones. The editor has to send `image_key` back on the next
    # PUT /questions, because replace_questions re-mints every question_id --
    # without the round-trip every save would orphan every image.
    image_key: str | None = None
    image_url: str | None = None
    image_alt: str | None = None

    @classmethod
    def from_model(cls, question: Question, image_url: str | None = None) -> "QuestionOut":
        return cls(**question.model_dump(), image_url=image_url)


class TestDetail(TestSummary):
    questions: list[QuestionOut]

    @classmethod
    def from_models(
        cls,
        test: Test,
        questions: list[Question],
        image_url_for: Callable[[str | None], str | None] | None = None,
    ) -> "TestDetail":
        """`image_url_for` resolves a stored key to a servable URL. It is a
        callable rather than a dict keyed by question_id on purpose: those ids
        are re-minted on every save, so a stale mapping would silently drop
        URLs instead of failing loudly. Defaults to None so callers that don't
        care about images (and every existing test) stay unchanged.
        """
        resolve = image_url_for or (lambda _key: None)
        return cls(
            **TestSummary.from_model(test).model_dump(),
            questions=[QuestionOut.from_model(q, resolve(q.image_key)) for q in questions],
        )


class QuestionImageUploadResponse(BaseModel):
    """The teacher's editor holds onto image_key and sends it back on the next
    PUT /questions; image_url is what it renders immediately."""

    image_key: str
    image_url: str
