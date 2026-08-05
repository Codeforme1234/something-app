from datetime import datetime

from pydantic import BaseModel


class Submission(BaseModel):
    """Stored at TEST#<test_id> / SUB#<session_id>.

    Written exactly once, atomically together with the session it completes
    -- see app.repositories.submissions_repo.create_submission_and_complete_session.
    `answers` and `per_question` are keyed by question_id.
    """

    session_id: str
    test_id: str
    submitted_at: datetime
    answers: dict[str, int]
    per_question: dict[str, bool]
    score: int
    correct_count: int
    total_questions: int
