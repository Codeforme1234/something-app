"""Business rules for the student attempt flow: resolving a link token,
starting a timed attempt, and grading a submission server-side.

The link token is a bearer credential (see app.core.ids.new_link_token) --
resolving it is the *only* authorization check these endpoints ever perform.
A miss must look identical no matter the underlying reason (unknown token,
wrong test, draft test), so every failure path below raises the same
NotFoundError with the same message. All timing decisions use
app.core.clock.now(); nothing here trusts a client-supplied timestamp.
"""

from datetime import timedelta

from app.core.clock import now
from app.core.config import get_settings
from app.core.exceptions import ConflictError, GoneError, NotFoundError
from app.models.session import SessionStatus, StudentSession
from app.models.submission import Submission
from app.models.test import Test, TestStatus
from app.repositories import sessions_repo, submissions_repo, tests_repo
from app.schemas.take import (
    StartAttemptResponse,
    SubmitRequest,
    SubmitResponse,
    TakeInfo,
    TakeQuestion,
)
from app.services.grading import grade

_NO_SUCH_LINK = "this test link is not valid"


class _Resolved:
    __slots__ = ("test", "session", "session_version")

    def __init__(self, test: Test, session: StudentSession, session_version: int):
        self.test = test
        self.session = session
        self.session_version = session_version


def _resolve(token: str) -> _Resolved:
    lookup_stored = sessions_repo.get_token_lookup(token)
    if lookup_stored is None:
        raise NotFoundError(_NO_SUCH_LINK)
    lookup = lookup_stored.model

    test_stored = tests_repo.get_test(lookup.teacher_sub, lookup.test_id)
    # A draft's link must not work, and the response must not reveal *why*
    # it doesn't -- both cases look exactly like an unknown token.
    if test_stored is None or test_stored.model.status != TestStatus.published:
        raise NotFoundError(_NO_SUCH_LINK)

    session_stored = sessions_repo.get_session(lookup.test_id, lookup.session_id)
    if session_stored is None:
        raise NotFoundError(_NO_SUCH_LINK)

    return _Resolved(test_stored.model, session_stored.model, session_stored.version)


def _grace() -> timedelta:
    return timedelta(seconds=get_settings().submit_grace_seconds)


def get_info(token: str) -> TakeInfo:
    resolved = _resolve(token)
    test, session = resolved.test, resolved.session

    if (
        session.status == SessionStatus.invited
        and test.deadline is not None
        and now() > test.deadline
    ):
        raise GoneError("this test's deadline has passed")

    return TakeInfo(
        test_title=test.title,
        duration_seconds=test.duration_seconds,
        question_count=test.question_count,
        deadline=test.deadline,
        session_status=session.status,
        student_name=session.student_name,
        ends_at=session.ends_at,
        server_now=now(),
    )


def start_attempt(token: str) -> StartAttemptResponse:
    resolved = _resolve(token)
    test, session, version = resolved.test, resolved.session, resolved.session_version

    if session.status == SessionStatus.completed:
        raise GoneError("already submitted")

    if session.status == SessionStatus.invited:
        if test.deadline is None or now() > test.deadline:
            raise GoneError("this test's deadline has passed")

        started_at = now()
        updated = session.model_copy(
            update={
                "status": SessionStatus.started,
                "started_at": started_at,
                "ends_at": started_at + timedelta(seconds=test.duration_seconds),
            }
        )
        try:
            sessions_repo.update_session(updated, version)
        except ConflictError:
            # A double click or two tabs raced us and already flipped this
            # session to started -- re-read and fall through to the
            # idempotent "already started" path below instead of erroring.
            reread = sessions_repo.get_session(test.test_id, session.session_id)
            assert reread is not None, "session cannot disappear between reads"
            session = reread.model
        else:
            session = updated

    if session.status != SessionStatus.started or session.ends_at is None:
        # Only reachable if the race above resolved to something other than
        # "started" (e.g. a concurrent submit already completed it).
        raise GoneError("already submitted")

    if now() > session.ends_at + _grace():
        raise GoneError("time is up")

    questions = tests_repo.get_questions(test.test_id)
    return StartAttemptResponse(
        questions=[TakeQuestion.from_model(q) for q in questions],
        ends_at=session.ends_at,
        server_now=now(),
    )


def submit_attempt(token: str, payload: SubmitRequest) -> SubmitResponse:
    resolved = _resolve(token)
    test, session, version = resolved.test, resolved.session, resolved.session_version

    if session.status == SessionStatus.completed:
        raise ConflictError("already submitted")
    if session.status != SessionStatus.started or session.ends_at is None:
        raise GoneError("test was not started")
    if now() > session.ends_at + _grace():
        # Never store a late submission.
        raise GoneError("time is up")

    questions = tests_repo.get_questions(test.test_id)
    per_question, correct_count, score = grade(questions, payload.answers)
    total = len(questions)

    submission = Submission(
        session_id=session.session_id,
        test_id=test.test_id,
        submitted_at=now(),
        answers=payload.answers,
        per_question=per_question,
        score=score,
        correct_count=correct_count,
        total_questions=total,
    )
    completed_session = session.model_copy(
        update={
            "status": SessionStatus.completed,
            "completed_at": now(),
            "score": score,
            "correct_count": correct_count,
            "total_questions": total,
        }
    )
    # Both writes land or neither does -- a ConflictError here (a concurrent
    # submit won the race) propagates as-is: "already submitted".
    submissions_repo.create_submission_and_complete_session(submission, completed_session, version)

    return SubmitResponse()
