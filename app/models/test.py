from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class TestStatus(StrEnum):
    #: An AI run is in flight. The test row and its credit debit already exist,
    #: but the questions do not yet. Deliberately a real stored status rather
    #: than an in-memory job registry, so the card survives a page reload and is
    #: visible from any browser the teacher is signed in on.
    generating = "generating"
    #: The run failed, twice. Credits have been refunded (see
    #: app/services/generation_job.py) -- this status is a receipt, not a debt.
    generation_failed = "generation_failed"
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
    # Set while status is `generating`. There is no scheduler to reap a run that
    # died with its process, so staleness is derived at read time from this
    # timestamp -- the same approach results_service takes for effective_status.
    generation_started_at: datetime | None = None
    # Why the run failed, shown on the card. Only set with generation_failed.
    generation_error: str | None = None
