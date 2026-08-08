"""Personalized post-submission feedback for one student's completed attempt.

Stored at TEST#<test_id> / FEEDBACK#<session_id> -- same partition as the
submission it is about. The `generating` placeholder row is created
synchronously at submit time (see app.services.feedback_job.start) so the
teacher's student-detail page has something to show immediately rather than a
missing row; `content` is filled in afterwards by app.services.feedback_job.run,
which runs in a background task after the response is sent. Staleness (a run
that died mid-flight, e.g. a process restart) is derived on read -- the same
approach app.services.test_service._presented takes for a generating test --
nothing here is written back by a reaper.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class FeedbackStatus(StrEnum):
    generating = "generating"
    ready = "ready"
    failed = "failed"


class ImprovementArea(BaseModel):
    """One weak concept: the topic, a diagnosis of what the student's actual
    answers reveal about the misconception, and one concrete action."""

    topic: str
    diagnosis: str
    action: str


class TopicMastery(BaseModel):
    """One topic's tally within a single attempt: how many of the questions
    assigned to it the student got right."""

    topic: str
    correct: int
    total: int


class FeedbackContent(BaseModel):
    summary: str
    strengths: list[str]
    # v1-compat only: the v1 prompt/schema produced these two flat lists. The
    # v2 prompt (app.llm.feedback_schemas.GeneratedFeedback) no longer
    # produces them -- replaced by improvement_areas/study_plan/topic_breakdown
    # below -- but rows written under v1 still have them, so they must keep
    # deserializing, and FeedbackView still shows them for those old rows.
    areas_to_improve: list[str] = []
    focus_topics: list[str] = []
    improvement_areas: list[ImprovementArea] = []
    study_plan: list[str] = []
    topic_breakdown: list[TopicMastery] = []


class StudentFeedback(BaseModel):
    session_id: str
    test_id: str
    status: FeedbackStatus
    generation_started_at: datetime | None = None
    generated_at: datetime | None = None
    # Truncated to 500 characters by writers (app.services.feedback_job),
    # matching Test.generation_error's cap.
    error: str | None = None
    content: FeedbackContent | None = None
    email_sent_at: datetime | None = None
