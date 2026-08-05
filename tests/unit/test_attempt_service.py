"""Unit tests for attempt_service. The repository layer is monkeypatched so
these run without DynamoDB -- the full round trip (token issued by
student_service, GET/start/submit against the real store) is covered by
tests/integration/test_take_api.py instead."""

from datetime import timedelta

import pytest

from app.core.clock import now
from app.core.exceptions import ConflictError, GoneError, NotFoundError
from app.models.question import Question
from app.models.session import SessionStatus, StudentSession
from app.models.test import Difficulty, Test, TestStatus
from app.models.token import TokenLookup
from app.repositories import sessions_repo, store, submissions_repo, tests_repo
from app.schemas.take import SubmitRequest
from app.services import attempt_service

TEACHER_SUB = "dev-alice"
TEST_ID = "01TESTID"
SESSION_ID = "01SESSIONID"
TOKEN = "test-token"


def _test(**overrides) -> Test:
    defaults = dict(
        test_id=TEST_ID,
        teacher_sub=TEACHER_SUB,
        title="Sample",
        difficulty=Difficulty.easy,
        duration_seconds=600,
        status=TestStatus.published,
        deadline=now() + timedelta(days=1),
        question_count=2,
        created_at=now(),
        published_at=now(),
    )
    defaults.update(overrides)
    return Test(**defaults)


def _session(**overrides) -> StudentSession:
    defaults = dict(
        session_id=SESSION_ID,
        test_id=TEST_ID,
        student_name="Ada Lovelace",
        student_email="ada@example.com",
        status=SessionStatus.invited,
        link_token=TOKEN,
        invited_at=now(),
    )
    defaults.update(overrides)
    return StudentSession(**defaults)


def _questions(n: int = 2) -> list[Question]:
    return [
        Question(question_id=f"q{i}", order=i, stem=f"Q{i}?", options=["a", "b", "c", "d"], correct_index=1)
        for i in range(1, n + 1)
    ]


def _patch(monkeypatch, *, test: Test, session: StudentSession, session_version: int = 1, questions=None):
    lookup = TokenLookup(test_id=test.test_id, session_id=session.session_id, teacher_sub=TEACHER_SUB)
    monkeypatch.setattr(sessions_repo, "get_token_lookup", lambda token: store.Stored(lookup, 1))
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: store.Stored(test, 1))
    monkeypatch.setattr(
        sessions_repo, "get_session", lambda tid, sid: store.Stored(session, session_version)
    )
    monkeypatch.setattr(tests_repo, "get_questions", lambda tid: questions if questions is not None else _questions())


# --- token resolution --------------------------------------------------------


def test_unknown_token_raises_not_found(monkeypatch):
    monkeypatch.setattr(sessions_repo, "get_token_lookup", lambda token: None)

    with pytest.raises(NotFoundError):
        attempt_service.get_info("nope")


def test_draft_test_token_raises_not_found(monkeypatch):
    _patch(monkeypatch, test=_test(status=TestStatus.draft, deadline=None), session=_session())

    with pytest.raises(NotFoundError):
        attempt_service.get_info(TOKEN)


# --- get_info -----------------------------------------------------------------


def test_get_info_on_invited_session_within_deadline_succeeds(monkeypatch):
    _patch(monkeypatch, test=_test(), session=_session())

    info = attempt_service.get_info(TOKEN)

    assert info.session_status == SessionStatus.invited
    assert info.test_title == "Sample"
    assert info.question_count == 2
    assert info.ends_at is None


def test_get_info_past_deadline_never_started_raises_gone(monkeypatch):
    _patch(
        monkeypatch,
        test=_test(deadline=now() - timedelta(days=1)),
        session=_session(status=SessionStatus.invited),
    )

    with pytest.raises(GoneError):
        attempt_service.get_info(TOKEN)


def test_get_info_completed_session_still_returns_info_even_past_deadline(monkeypatch):
    _patch(
        monkeypatch,
        test=_test(deadline=now() - timedelta(days=1)),
        session=_session(status=SessionStatus.completed, score=80),
    )

    info = attempt_service.get_info(TOKEN)
    assert info.session_status == SessionStatus.completed


# --- start_attempt --------------------------------------------------------------


def test_start_attempt_on_completed_session_raises_gone(monkeypatch):
    _patch(monkeypatch, test=_test(), session=_session(status=SessionStatus.completed))

    with pytest.raises(GoneError):
        attempt_service.start_attempt(TOKEN)


def test_start_attempt_past_deadline_never_started_raises_gone(monkeypatch):
    _patch(monkeypatch, test=_test(deadline=now() - timedelta(days=1)), session=_session())

    with pytest.raises(GoneError):
        attempt_service.start_attempt(TOKEN)


def test_start_attempt_first_time_writes_started_and_returns_questions(monkeypatch):
    _patch(monkeypatch, test=_test(duration_seconds=600), session=_session())
    calls = []
    monkeypatch.setattr(
        sessions_repo, "update_session", lambda session, version: calls.append((session, version))
    )

    resp = attempt_service.start_attempt(TOKEN)

    assert len(resp.questions) == 2
    assert all("correct_index" not in q.model_dump() for q in resp.questions)
    assert len(calls) == 1
    written_session, written_version = calls[0]
    assert written_session.status == SessionStatus.started
    assert written_session.started_at is not None
    assert written_session.ends_at == written_session.started_at + timedelta(seconds=600)
    assert resp.ends_at == written_session.ends_at
    assert written_version == 1


def test_start_attempt_already_started_within_grace_is_idempotent(monkeypatch):
    started_at = now() - timedelta(minutes=1)
    ends_at = started_at + timedelta(minutes=10)
    _patch(
        monkeypatch,
        test=_test(duration_seconds=600),
        session=_session(status=SessionStatus.started, started_at=started_at, ends_at=ends_at),
    )

    resp = attempt_service.start_attempt(TOKEN)

    assert resp.ends_at == ends_at


def test_start_attempt_already_started_past_grace_raises_gone(monkeypatch):
    ends_at = now() - timedelta(minutes=5)
    _patch(
        monkeypatch,
        test=_test(),
        session=_session(status=SessionStatus.started, started_at=ends_at - timedelta(minutes=10), ends_at=ends_at),
    )

    with pytest.raises(GoneError):
        attempt_service.start_attempt(TOKEN)


def test_start_attempt_version_conflict_falls_through_to_idempotent_reread(monkeypatch):
    """Two tabs racing: the write conflicts, so start_attempt re-reads and
    returns the session another request already started, instead of
    erroring."""
    invited_session = _session()
    started_at = now()
    ends_at = started_at + timedelta(seconds=600)
    already_started = invited_session.model_copy(
        update={"status": SessionStatus.started, "started_at": started_at, "ends_at": ends_at}
    )

    _patch(monkeypatch, test=_test(duration_seconds=600), session=invited_session, session_version=1)

    def _raise_conflict(session, version):
        raise ConflictError("item was modified concurrently")

    monkeypatch.setattr(sessions_repo, "update_session", _raise_conflict)
    monkeypatch.setattr(sessions_repo, "get_session", lambda tid, sid: store.Stored(already_started, 2))

    resp = attempt_service.start_attempt(TOKEN)
    assert resp.ends_at == ends_at


# --- submit_attempt -------------------------------------------------------------


def test_submit_on_completed_session_raises_conflict(monkeypatch):
    _patch(monkeypatch, test=_test(), session=_session(status=SessionStatus.completed))

    with pytest.raises(ConflictError):
        attempt_service.submit_attempt(TOKEN, SubmitRequest(answers={}))


def test_submit_never_started_raises_gone(monkeypatch):
    _patch(monkeypatch, test=_test(), session=_session(status=SessionStatus.invited))

    with pytest.raises(GoneError):
        attempt_service.submit_attempt(TOKEN, SubmitRequest(answers={}))


def test_submit_past_grace_raises_gone_and_does_not_write(monkeypatch):
    ends_at = now() - timedelta(minutes=5)
    _patch(
        monkeypatch,
        test=_test(),
        session=_session(status=SessionStatus.started, started_at=ends_at - timedelta(minutes=10), ends_at=ends_at),
    )
    calls = []
    monkeypatch.setattr(
        submissions_repo,
        "create_submission_and_complete_session",
        lambda sub, sess, v: calls.append((sub, sess, v)),
    )

    with pytest.raises(GoneError):
        attempt_service.submit_attempt(TOKEN, SubmitRequest(answers={}))
    assert calls == []


def test_submit_within_grace_grades_and_writes_atomically(monkeypatch):
    started_at = now() - timedelta(minutes=1)
    ends_at = started_at + timedelta(minutes=10)
    questions = _questions(2)
    _patch(
        monkeypatch,
        test=_test(),
        session=_session(status=SessionStatus.started, started_at=started_at, ends_at=ends_at),
        session_version=3,
        questions=questions,
    )
    calls = []
    monkeypatch.setattr(
        submissions_repo,
        "create_submission_and_complete_session",
        lambda sub, sess, v: calls.append((sub, sess, v)) or (v + 1),
    )

    # q1's correct_index is 1, q2's is 1 -- answer q1 right, q2 wrong.
    resp = attempt_service.submit_attempt(TOKEN, SubmitRequest(answers={"q1": 1, "q2": 0}))

    assert resp.status == "submitted"
    assert len(calls) == 1
    submission, completed_session, version = calls[0]
    assert version == 3
    assert submission.score == 50
    assert submission.correct_count == 1
    assert submission.total_questions == 2
    assert submission.per_question == {"q1": True, "q2": False}
    assert completed_session.status == SessionStatus.completed
    assert completed_session.score == 50


def test_submit_concurrent_write_conflict_propagates(monkeypatch):
    started_at = now() - timedelta(minutes=1)
    ends_at = started_at + timedelta(minutes=10)
    _patch(
        monkeypatch,
        test=_test(),
        session=_session(status=SessionStatus.started, started_at=started_at, ends_at=ends_at),
    )

    def _raise_conflict(sub, sess, v):
        raise ConflictError("already submitted")

    monkeypatch.setattr(submissions_repo, "create_submission_and_complete_session", _raise_conflict)

    with pytest.raises(ConflictError):
        attempt_service.submit_attempt(TOKEN, SubmitRequest(answers={}))
