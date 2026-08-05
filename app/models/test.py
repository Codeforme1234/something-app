from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class TestStatus(StrEnum):
    draft = "draft"
    published = "published"


class Difficulty(StrEnum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class Test(BaseModel):
    test_id: str
    teacher_sub: str
    # Denormalized from the owning admin's Teacher record at creation time --
    # not used for access control (ownership is still by teacher_sub/key) but
    # kept for company-level cost reporting. Optional so tests created before
    # multi-tenancy existed still parse; never backfilled retroactively.
    company_id: str | None = None
    title: str
    difficulty: Difficulty
    duration_seconds: int
    status: TestStatus
    deadline: datetime | None = None
    question_count: int = 0
    # Kept in sync by app.services.student_service.add_students whenever new
    # student sessions are created.
    student_count: int = 0
    created_at: datetime
    published_at: datetime | None = None
