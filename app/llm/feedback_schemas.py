"""Two-tier schema for LLM-generated post-submission feedback. Same split as
app.llm.schemas and app.llm.extraction_schemas, for the same reason: the class
handed to OpenAI as the structured-output schema must be UNCONSTRAINED,
because constraint keywords (minLength / maxItems) are rejected by strict
schema mode on some models -- and that rejection is deterministic, a 100%
outage rather than an intermittent flake. Structure comes from the wire tier
(including its nested sub-models); the caps are enforced by validating into
the strict tier below after parsing.

v2: the model is handed full depth -- every question's options, the student's
chosen option, and the correct option (see FeedbackQuestionResult) -- and may
explain the concept behind a missed question. The teacher-review-before-email
step is the safeguard against a bad take, not withholding the answer from the
model. Output is five structured sections rather than the v1 flat
strengths/areas_to_improve/focus_topics; every list may legitimately come back
empty (a perfect score has no improvement_areas), so none carry a
`min_length`.
"""

from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

#: Caps on each list, tuned per section -- generous enough to be useful,
#: short enough to stay readable in an email.
_MAX_STRENGTHS = 5
_MAX_IMPROVEMENT_AREAS = 5
_MAX_STUDY_PLAN = 6
_MAX_TOPIC_BREAKDOWN = 6


class FeedbackQuestionResult(BaseModel):
    """One graded question, at full depth: its options, what the student
    chose, and which option was correct.

    Unlike v1, the correct answer IS handed to the model -- see
    app.llm.protocol.FeedbackGenerator's docstring for why that's safe (the
    teacher reviews every result before it is emailed). `chosen_index` is
    None when the student left the question unanswered.
    """

    order: int
    # Plain text (app.core.rich_text.rich_text_to_plain), not the sanitized
    # HTML fragment Question.stem stores -- a model should never be handed
    # markup to describe in prose.
    stem: str
    options: list[str]
    chosen_index: int | None
    correct_index: int


class FeedbackInput(BaseModel):
    """Everything app.llm.protocol.FeedbackGenerator.generate needs for one
    completed attempt."""

    test_title: str
    difficulty: str
    score: int
    correct_count: int
    total_questions: int
    duration_seconds: int
    # None when either session.started_at or session.completed_at is missing
    # (should not happen for a real completed attempt, but the model carries
    # the same optionality as the session fields it is derived from).
    elapsed_seconds: int | None
    results: list[FeedbackQuestionResult]


class ImprovementAreaWire(BaseModel):
    """No Field constraints -- see the module docstring."""

    topic: str
    diagnosis: str
    action: str


class TopicMasteryWire(BaseModel):
    topic: str
    correct: int
    total: int


class GeneratedFeedbackWire(BaseModel):
    """Sent to OpenAI as the response schema. No Field constraints anywhere
    in this tree, including the nested sub-models above -- see the module
    docstring."""

    summary: str
    strengths: list[str]
    improvement_areas: list[ImprovementAreaWire]
    study_plan: list[str]
    topic_breakdown: list[TopicMasteryWire]


class GeneratedImprovementArea(BaseModel):
    """The strict tier of ImprovementAreaWire."""

    topic: str
    diagnosis: str
    action: str

    @field_validator("topic")
    @classmethod
    def _validate_topic(cls, v: str) -> str:
        stripped = v.strip()
        if not (1 <= len(stripped) <= 100):
            raise ValueError("topic must be 1-100 characters after stripping")
        return stripped

    @field_validator("diagnosis")
    @classmethod
    def _validate_diagnosis(cls, v: str) -> str:
        stripped = v.strip()
        if not (1 <= len(stripped) <= 400):
            raise ValueError("diagnosis must be 1-400 characters after stripping")
        return stripped

    @field_validator("action")
    @classmethod
    def _validate_action(cls, v: str) -> str:
        stripped = v.strip()
        if not (1 <= len(stripped) <= 300):
            raise ValueError("action must be 1-300 characters after stripping")
        return stripped


class GeneratedTopicMastery(BaseModel):
    """The strict tier of TopicMasteryWire."""

    topic: str
    correct: int
    total: int

    @field_validator("topic")
    @classmethod
    def _validate_topic(cls, v: str) -> str:
        stripped = v.strip()
        if not (1 <= len(stripped) <= 100):
            raise ValueError("topic must be 1-100 characters after stripping")
        return stripped

    @model_validator(mode="after")
    def _validate_counts(self) -> "GeneratedTopicMastery":
        # A cross-field rule (correct vs total on the SAME row), so it has to
        # be a model validator rather than a per-field one.
        if not (0 <= self.correct <= self.total):
            raise ValueError("correct must be between 0 and total, inclusive")
        return self


class GeneratedFeedback(BaseModel):
    """The strict tier, validated locally after parsing (and on every fake
    generator's output -- see app.llm.fake_feedback)."""

    summary: str
    strengths: Annotated[list[str], Field(max_length=_MAX_STRENGTHS)]
    improvement_areas: Annotated[
        list[GeneratedImprovementArea], Field(max_length=_MAX_IMPROVEMENT_AREAS)
    ]
    study_plan: Annotated[list[str], Field(max_length=_MAX_STUDY_PLAN)]
    topic_breakdown: Annotated[list[GeneratedTopicMastery], Field(max_length=_MAX_TOPIC_BREAKDOWN)]

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        stripped = v.strip()
        if not (1 <= len(stripped) <= 2000):
            raise ValueError("summary must be 1-2000 characters after stripping")
        return stripped

    @field_validator("strengths", "study_plan")
    @classmethod
    def _validate_items(cls, items: list[str]) -> list[str]:
        stripped: list[str] = []
        for item in items:
            s = item.strip()
            if not (1 <= len(s) <= 300):
                raise ValueError("each item must be 1-300 characters after stripping")
            stripped.append(s)
        return stripped
