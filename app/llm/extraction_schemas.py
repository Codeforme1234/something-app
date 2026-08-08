"""Two-tier schema for PDF question extraction.

Same deliberate split as app/llm/schemas.py, for the same reason: the class
handed to OpenAI's structured-output mode must be UNCONSTRAINED, because
constraint keywords (minLength / maxItems / minimum) are rejected by strict
schema mode on some models -- and that rejection is deterministic, so a
Field-capped wire model is a 100% outage rather than an intermittent flake.

All the real rules live on the strict tier, which is validated locally and can
therefore fail in a way we can repair with a follow-up call.

Two differences from GeneratedMCQSet, both deliberate:

  - No hard count check. The product rule is "store all the questions
    irrespective", so a short set is repaired via a targeted re-run, never
    rejected.
  - No duplicate-stem rule. Real papers legitimately repeat near-identical stems
    across sections; rejecting them would drop rows. Duplicate *number* is the
    meaningful conflict, and it keeps the first occurrence rather than raising.
"""

import html
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.core.rich_text import sanitize_rich_text

#: Every QuizDeck question is a four-option MCQ.
OPTION_COUNT = 4


def _decode_entities(value: str) -> str:
    """Turn HTML entities the model emitted back into characters.

    The prompt asks for plain text, but a model transcribing "a < b" will still
    sometimes write `a &lt; b`. Left alone that gets escaped again on the way in
    and reaches the page as the literal text `a&amp;lt;b`.

    Decoding here is safe precisely because it happens BEFORE
    sanitize_rich_text: a decoded `<script>` becomes a real tag, which bleach
    then strips. Decoding after sanitizing would be a hole.
    """
    return html.unescape(value)


class ExtractedQuestionWire(BaseModel):
    """Sent to OpenAI as the response schema. No Field constraints -- see the
    module docstring."""

    number: int
    stem: str
    options: list[str]
    correct_index: int
    source_page: int
    has_figure: bool


class ExtractedQuestionSetWire(BaseModel):
    questions: list[ExtractedQuestionWire]


class ExtractedQuestion(BaseModel):
    """The strict tier. Mirrors app/llm/schemas.py::GeneratedMCQ's rules exactly,
    so extracted questions are held to the same standard as generated ones and
    end up satisfying app/schemas/tests.py::QuestionInput."""

    number: Annotated[int, Field(ge=1, le=999)]
    stem: Annotated[str, Field(min_length=1, max_length=6000)]
    options: Annotated[list[str], Field(min_length=OPTION_COUNT, max_length=OPTION_COUNT)]
    correct_index: Annotated[int, Field(ge=0, le=OPTION_COUNT - 1)]
    source_page: Annotated[int, Field(ge=1)]
    has_figure: bool = False

    @field_validator("stem")
    @classmethod
    def _sanitize_stem(cls, v: str) -> str:
        # The same trust boundary generated questions pass through: the stored
        # value is later rendered with dangerouslySetInnerHTML on the student
        # take page and the teacher's review page.
        return sanitize_rich_text(_decode_entities(v), "stem", max_visible_chars=1000)

    @field_validator("options")
    @classmethod
    def _validate_options(cls, options: list[str]) -> list[str]:
        stripped: list[str] = []
        for option in options:
            # Options are rendered as React text children, never as HTML, so an
            # entity left in here would display literally as "&lt;".
            s = _decode_entities(option).strip()
            if not (1 <= len(s) <= 300):
                raise ValueError("each option must be 1-300 characters after stripping")
            stripped.append(s)
        if len(set(stripped)) != len(stripped):
            raise ValueError("options must not contain duplicate values")
        return stripped


class ExtractedQuestionSet(BaseModel):
    questions: list[ExtractedQuestion]

    @field_validator("questions")
    @classmethod
    def _drop_duplicate_numbers(cls, questions: list[ExtractedQuestion]) -> list[ExtractedQuestion]:
        seen: set[int] = set()
        unique: list[ExtractedQuestion] = []
        for question in questions:
            if question.number in seen:
                continue
            seen.add(question.number)
            unique.append(question)
        return unique

    def numbers(self) -> list[int]:
        return [q.number for q in self.questions]
