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
    title: str
    difficulty: Difficulty
    duration_seconds: int
    status: TestStatus
    deadline: datetime | None = None
    question_count: int = 0
    # Maintained by a later phase (students + invites); always 0 in Phase 1.
    student_count: int = 0
    created_at: datetime
    published_at: datetime | None = None
