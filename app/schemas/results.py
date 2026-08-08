"""Response DTOs for teacher-facing results: per-student review and test
analytics. Built by app/services/results_service.py from sessions,
submissions, and questions -- there are no stored aggregates.
"""

from datetime import datetime

from pydantic import BaseModel

from app.models.feedback import FeedbackStatus, ImprovementArea, StudentFeedback, TopicMastery
from app.schemas.students import SessionRow


class QuestionReview(BaseModel):
    """The TEACHER view of one question in a student's completed attempt.

    Unlike app/schemas/take.py (which must never expose `correct_index` to a
    student), including it here is correct and required -- this model is
    only ever returned to the owning teacher.
    """

    question_id: str
    order: int
    stem: str
    options: list[str]
    correct_index: int
    chosen_index: int | None
    is_correct: bool
    # Resolved URL only, like TakeQuestion -- the teacher's review page renders
    # the image but has no reason to hold the storage key.
    image_url: str | None = None
    image_alt: str | None = None


class FeedbackView(BaseModel):
    """The teacher-facing view of one student's LLM feedback.

    `status` is the PRESENTED status (app.services.feedback_job.presented) --
    staleness has already been applied, so a `generating` row that died
    mid-run without a reaper ever touching it shows here as `failed`, not
    stuck forever. `content` fields are flattened rather than nested so a
    `generating`/`failed` row (content is None on the model) still round-trips
    as empty lists instead of nulls.

    `improvement_areas`/`study_plan`/`topic_breakdown` are v2's structured
    sections. `areas_to_improve`/`focus_topics` are v1-compat: the v2 prompt
    no longer produces them (app.llm.feedback_schemas.GeneratedFeedback), but
    a row generated before v2 shipped still has them, so they still round-trip
    here rather than silently vanishing for old rows.
    """

    status: FeedbackStatus
    summary: str | None
    strengths: list[str]
    improvement_areas: list[ImprovementArea]
    study_plan: list[str]
    topic_breakdown: list[TopicMastery]
    areas_to_improve: list[str]
    focus_topics: list[str]
    generated_at: datetime | None
    error: str | None
    email_sent_at: datetime | None

    @classmethod
    def from_model(cls, feedback: StudentFeedback) -> "FeedbackView":
        content = feedback.content
        return cls(
            status=feedback.status,
            summary=content.summary if content else None,
            strengths=content.strengths if content else [],
            improvement_areas=content.improvement_areas if content else [],
            study_plan=content.study_plan if content else [],
            topic_breakdown=content.topic_breakdown if content else [],
            areas_to_improve=content.areas_to_improve if content else [],
            focus_topics=content.focus_topics if content else [],
            generated_at=feedback.generated_at,
            error=feedback.error,
            email_sent_at=feedback.email_sent_at,
        )


class StudentDetail(BaseModel):
    session: SessionRow
    # None unless the session is completed -- there is nothing to review
    # for a student who hasn't submitted yet.
    review: list[QuestionReview] | None
    # None unless the session is completed, same as `review` -- see
    # app.services.results_service.get_student_detail.
    feedback: FeedbackView | None = None


class QuestionStat(BaseModel):
    question_id: str
    order: int
    stem: str
    correct_count: int
    attempt_count: int
    correct_rate: int


class TestAnalytics(BaseModel):
    student_count: int
    completed_count: int
    completion_rate: int
    average_score: int | None
    highest_score: int | None
    lowest_score: int | None
    # Hardest first (lowest correct_rate first).
    question_stats: list[QuestionStat]
