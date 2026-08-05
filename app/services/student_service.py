"""Business rules for rostering students, sending invitations, and
publishing a test.

Invitation sends happen strictly after the session records are durably
written: a send failure must not lose the invitation, so failures are
logged here rather than raised (see `_send_invitations`).
"""

import logging

from app.core.clock import now
from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.ids import new_link_token, new_ulid
from app.models.session import SessionStatus, StudentSession
from app.models.test import Test, TestStatus
from app.repositories import sessions_repo, store, tests_repo
from app.schemas.students import (
    AddStudentsRequest,
    AddStudentsResponse,
    PublishRequest,
    SessionRow,
)
from app.schemas.tests import TestSummary
from app.services.email.invitations import send_invitation

logger = logging.getLogger(__name__)


def _get_owned_test(teacher_sub: str, test_id: str) -> store.Stored[Test]:
    stored = tests_repo.get_test(teacher_sub, test_id)
    if stored is None:
        raise NotFoundError("test not found")
    return stored


def _send_invitations(test: Test, sessions: list[StudentSession]) -> None:
    assert test.deadline is not None, "invitations are only sent for published tests"
    frontend_origin = get_settings().frontend_origin
    for session in sessions:
        link = f"{frontend_origin}/t/{session.link_token}"
        try:
            send_invitation(
                student_name=session.student_name,
                student_email=session.student_email,
                test_title=test.title,
                deadline=test.deadline,
                link=link,
            )
        except Exception:
            logger.exception(
                "failed to send invitation to %s for test %s (session %s)",
                session.student_email,
                test.test_id,
                session.session_id,
            )


def add_students(teacher_sub: str, test_id: str, payload: AddStudentsRequest) -> AddStudentsResponse:
    stored = _get_owned_test(teacher_sub, test_id)
    test = stored.model

    existing_emails = {s.student_email.lower() for s in sessions_repo.list_sessions(test_id)}

    skipped_emails: list[str] = []
    seen_in_batch: set[str] = set()
    new_sessions: list[StudentSession] = []
    timestamp = now()
    for student in payload.students:
        email_key = student.email.lower()
        if email_key in existing_emails or email_key in seen_in_batch:
            skipped_emails.append(student.email)
            continue
        seen_in_batch.add(email_key)
        new_sessions.append(
            StudentSession(
                session_id=new_ulid(),
                test_id=test_id,
                student_name=student.name,
                student_email=student.email,
                status=SessionStatus.invited,
                link_token=new_link_token(),
                invited_at=timestamp,
            )
        )

    if new_sessions:
        sessions_repo.create_sessions(new_sessions, teacher_sub)

        updated_test = test.model_copy(update={"student_count": test.student_count + len(new_sessions)})
        tests_repo.update_test(teacher_sub, updated_test, stored.version)

        # A draft test has no deadline yet -- those invitations go out on publish.
        if test.status == TestStatus.published:
            _send_invitations(updated_test, new_sessions)

    return AddStudentsResponse(
        added=[SessionRow.from_model(s) for s in new_sessions],
        skipped_emails=skipped_emails,
    )


def list_students(teacher_sub: str, test_id: str) -> list[SessionRow]:
    _get_owned_test(teacher_sub, test_id)
    return [SessionRow.from_model(s) for s in sessions_repo.list_sessions(test_id)]


def publish_test(teacher_sub: str, test_id: str, payload: PublishRequest) -> TestSummary:
    stored = _get_owned_test(teacher_sub, test_id)
    test = stored.model

    if test.status != TestStatus.draft:
        raise ConflictError("test is already published")
    if test.question_count < 1:
        raise ConflictError("test needs at least one question before publishing")
    if payload.deadline <= now():
        raise BadRequestError("deadline must be in the future")

    updated_test = test.model_copy(
        update={"status": TestStatus.published, "published_at": now(), "deadline": payload.deadline}
    )
    tests_repo.update_test(teacher_sub, updated_test, stored.version)

    invited_sessions = [s for s in sessions_repo.list_sessions(test_id) if s.status == SessionStatus.invited]
    if invited_sessions:
        _send_invitations(updated_test, invited_sessions)

    return TestSummary.from_model(updated_test)
