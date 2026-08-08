"""Teacher actions on a student's feedback: emailing it to the student, and
kicking off a fresh generation run.

Ownership follows CLAUDE.md rule 3 exactly like results_service.get_student_detail:
a test is read with the CALLER's sub, so a test_id/session_id pair belonging to
someone else's test simply misses as NotFoundError -- there is no separate
if-statement doing the ownership check.
"""

import logging

from app.core.clock import now
from app.core.config import get_settings
from app.core.exceptions import AppError, ConflictError, NotFoundError, UpstreamError
from app.models.feedback import FeedbackStatus, StudentFeedback
from app.models.session import SessionStatus, StudentSession
from app.models.test import Test
from app.repositories import feedback_repo, sessions_repo, tests_repo
from app.services import feedback_job
from app.services.email.feedback import send_feedback

logger = logging.getLogger(__name__)


def _get_owned_session(teacher_sub: str, test_id: str, session_id: str) -> tuple[Test, StudentSession]:
    stored_test = tests_repo.get_test(teacher_sub, test_id)
    if stored_test is None:
        raise NotFoundError("test not found")
    session_stored = sessions_repo.get_session(test_id, session_id)
    if session_stored is None:
        raise NotFoundError("session not found")
    return stored_test.model, session_stored.model


def email_feedback(teacher_sub: str, test_id: str, session_id: str) -> StudentFeedback:
    test, session = _get_owned_session(teacher_sub, test_id, session_id)

    stored_feedback = feedback_repo.get_feedback(test_id, session_id)
    view = feedback_job.presented(stored_feedback.model if stored_feedback else None, session)
    if view is None or view.status != FeedbackStatus.ready or view.content is None:
        raise ConflictError("feedback is not ready to send")

    # Same shape as student_service._send_invitations builds an invite link:
    # the student's own take-page, which shows their score again on revisit.
    link = f"{get_settings().frontend_origin}/t/{session.link_token}"
    try:
        send_feedback(
            student_name=session.student_name,
            student_email=session.student_email,
            test_title=test.title,
            score=session.score or 0,
            correct_count=session.correct_count or 0,
            total_questions=session.total_questions or 0,
            content=view.content,
            link=link,
        )
    except AppError:
        # None of today's senders raise one, but if a future sender starts
        # raising a domain error deliberately (e.g. a bounce classified as
        # BadRequestError), let it through as-is rather than relabeling it.
        raise
    except Exception as exc:
        raise UpstreamError("could not send the email; please try again") from exc

    # view.status == ready guarantees stored_feedback is not None: presented()
    # only ever returns `ready` by passing an existing row through unchanged.
    assert stored_feedback is not None
    updated = stored_feedback.model.model_copy(update={"email_sent_at": now()})
    try:
        feedback_repo.update_feedback(updated, stored_feedback.version)
    except ConflictError:
        # The mail already went out -- a write conflict here must not surface
        # as a failure to the teacher, who correctly believes the send
        # succeeded.
        logger.warning(
            "email_sent_at write lost a race test=%s session=%s (mail was still sent)",
            test_id,
            session_id,
        )
    return updated


def regenerate(teacher_sub: str, test_id: str, session_id: str) -> StudentFeedback:
    _, session = _get_owned_session(teacher_sub, test_id, session_id)
    if session.status != SessionStatus.completed:
        raise NotFoundError("session not found")

    stored_feedback = feedback_repo.get_feedback(test_id, session_id)
    view = feedback_job.presented(stored_feedback.model if stored_feedback else None, session)
    assert view is not None  # session is completed, so presented() always returns a row
    if view.status == FeedbackStatus.generating:
        raise ConflictError("feedback is still generating")
    if view.status == FeedbackStatus.ready:
        raise ConflictError("feedback is already ready")

    if stored_feedback is None:
        placeholder = StudentFeedback(
            session_id=session_id,
            test_id=test_id,
            status=FeedbackStatus.generating,
            generation_started_at=now(),
        )
        feedback_repo.create_feedback(placeholder)
        return placeholder

    # Reset in place, but never touch email_sent_at: a regenerate must not
    # erase the record that this student was already mailed once.
    reset = stored_feedback.model.model_copy(
        update={
            "status": FeedbackStatus.generating,
            "generation_started_at": now(),
            "error": None,
            "content": None,
            "generated_at": None,
        }
    )
    # ConflictError propagates as-is: a concurrent regenerate racing this one
    # should see a 409, not silently overwrite the other's write.
    feedback_repo.update_feedback(reset, stored_feedback.version)
    return reset
