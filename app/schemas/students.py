"""Request/response DTOs for students, invitations, and publishing.

`StudentInput` validates a teacher's typed or CSV-uploaded roster, so it
caps input hard, same rationale as app/schemas/tests.py. Unlike
`TeacherClaims.email` (trusted, from the identity provider), student emails
are untrusted input, so this is the one place in the API that uses
Pydantic's `EmailStr`.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.session import SessionStatus, StudentSession


def _stripped_nonblank(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be blank")
    return stripped


class StudentInput(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    email: EmailStr

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return _stripped_nonblank(v, "name")


class AddStudentsRequest(BaseModel):
    students: Annotated[list[StudentInput], Field(min_length=1, max_length=200)]


class SessionRow(BaseModel):
    """A teacher-facing view of a session. Never include `link_token` here --
    that would leak a working student link to anyone who can read the roster."""

    session_id: str
    student_name: str
    student_email: str
    status: SessionStatus
    invited_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    score: int | None

    @classmethod
    def from_model(cls, session: StudentSession) -> "SessionRow":
        return cls(
            session_id=session.session_id,
            student_name=session.student_name,
            student_email=session.student_email,
            status=session.status,
            invited_at=session.invited_at,
            started_at=session.started_at,
            completed_at=session.completed_at,
            score=session.score,
        )


class AddStudentsResponse(BaseModel):
    added: list[SessionRow]
    skipped_emails: list[str]


class PublishRequest(BaseModel):
    # Must be in the future, but that depends on server time, so it's checked
    # in student_service against app.core.clock.now(), not here.
    deadline: datetime
