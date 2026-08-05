"""Response DTOs for teacher-facing results: per-student review and test
analytics. Built by app/services/results_service.py from sessions,
submissions, and questions -- there are no stored aggregates.
"""

from pydantic import BaseModel

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


class StudentDetail(BaseModel):
    session: SessionRow
    # None unless the session is completed -- there is nothing to review
    # for a student who hasn't submitted yet.
    review: list[QuestionReview] | None


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
