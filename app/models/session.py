from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class SessionStatus(StrEnum):
    invited = "invited"
    started = "started"
    completed = "completed"


class StudentSession(BaseModel):
    """One invited student for one test, stored at TEST#<test_id> / SESSION#<session_id>.

    `link_token` is the same token minted for the TOKEN#<token>/LOOKUP item
    (see app/models/token.py) -- it lives here too because resending an
    invitation at publish time needs to rebuild the student's link, and with
    no GSI a token -> session lookup can't be reversed back to session ->
    token. It must never appear on a teacher-facing response (see
    app/schemas/students.py::SessionRow).
    """

    session_id: str
    test_id: str
    student_name: str
    student_email: str
    status: SessionStatus
    link_token: str
    invited_at: datetime
    # Filled in by later phases (attempt flow, grading); always None here.
    started_at: datetime | None = None
    ends_at: datetime | None = None
    completed_at: datetime | None = None
    score: int | None = None
    correct_count: int | None = None
    total_questions: int | None = None
