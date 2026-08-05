from datetime import datetime

from pydantic import BaseModel


class Teacher(BaseModel):
    sub: str
    # Plain str, not EmailStr: this value comes from the identity provider,
    # which has already verified it. Strict validation belongs on student
    # emails, which a teacher types or uploads.
    email: str
    name: str
    # Optional so a teacher record stored before multi-tenancy existed still
    # parses; teachers_repo.upsert_teacher backfills it on that admin's next
    # login rather than requiring a migration.
    company_id: str | None = None
    created_at: datetime
