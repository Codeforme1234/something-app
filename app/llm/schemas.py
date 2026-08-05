"""Strict validation layer for LLM output. Generated questions land in the
same question editor as manually authored ones, so they must satisfy exactly
the same caps as app.schemas.tests.QuestionInput (4 options, each 1-300
chars stripped and unique within the question, stem 1-1000 chars, correct
index 0-3) plus one extra rule that only makes sense for a *set* of
generated questions: no duplicate stems across the set.

LLM output is untrusted input -- these validators are what make it safe to
hand to the frontend without ever touching the database.
"""

from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class GeneratedMCQWire(BaseModel):
    """The shape sent to OpenAI as the structured-output schema. Deliberately
    UNCONSTRAINED: constraint keywords like minLength/maxItems that Pydantic
    Field caps would emit are rejected by OpenAI's strict schema mode on some
    models, and that failure would be deterministic — every call, both
    retries. Structure comes from here; the caps are enforced by validating
    into the strict models below after parsing."""

    stem: str
    options: list[str]
    correct_index: int


class GeneratedMCQSetWire(BaseModel):
    questions: list[GeneratedMCQWire]


class GeneratedMCQ(BaseModel):
    stem: Annotated[str, Field(min_length=1, max_length=1000)]
    options: Annotated[list[str], Field(min_length=4, max_length=4)]
    correct_index: Annotated[int, Field(ge=0, le=3)]

    @field_validator("stem")
    @classmethod
    def _strip_stem(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("stem must not be blank")
        return stripped

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


class GeneratedMCQSet(BaseModel):
    questions: list[GeneratedMCQ]

    @field_validator("questions")
    @classmethod
    def _no_duplicate_stems(cls, questions: list[GeneratedMCQ]) -> list[GeneratedMCQ]:
        stems = [q.stem for q in questions]
        if len(set(stems)) != len(stems):
            raise ValueError("questions must not contain duplicate stems")
        return questions

    def validate_count(self, expected: int) -> None:
        """Enforce "exactly N questions". Kept as a separate explicit check
        rather than a field constraint because the expected count is a
        per-request parameter, not something the schema alone can express --
        it's also what OpenAI's structured-output JSON schema is built from,
        so it must stay static."""
        if len(self.questions) != expected:
            raise ValueError(f"expected exactly {expected} questions, got {len(self.questions)}")
