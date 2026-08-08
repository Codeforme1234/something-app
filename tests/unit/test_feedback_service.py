"""Unit tests for feedback_service: the teacher-facing email/regenerate
actions. Repositories, feedback_job.presented, and the email sender are all
monkeypatched, so nothing here touches DynamoDB or writes a real email."""

import pytest

from app.core.clock import now
from app.core.exceptions import ConflictError, NotFoundError, UpstreamError
from app.models.feedback import FeedbackContent, FeedbackStatus, StudentFeedback
from app.models.session import SessionStatus, StudentSession
from app.models.test import Difficulty, Test, TestStatus
from app.repositories import feedback_repo, sessions_repo, store, tests_repo
from app.services import feedback_service

TEACHER_SUB = "dev-alice"
TEST_ID = "01TESTID"
SESSION_ID = "01SESSIONID"


def _test_model(**overrides) -> Test:
    defaults = dict(
        test_id=TEST_ID,
        teacher_sub=TEACHER_SUB,
        title="Algebra Basics",
        difficulty=Difficulty.medium,
        duration_seconds=900,
        status=TestStatus.published,
        created_at=now(),
    )
    defaults.update(overrides)
    return Test(**defaults)


def _session(**overrides) -> StudentSession:
    defaults = dict(
        session_id=SESSION_ID,
        test_id=TEST_ID,
        student_name="Ada Lovelace",
        student_email="ada@example.com",
        status=SessionStatus.completed,
        link_token="tok",
        invited_at=now(),
        score=50,
        correct_count=1,
        total_questions=2,
    )
    defaults.update(overrides)
    return StudentSession(**defaults)


def _content(**overrides) -> FeedbackContent:
    defaults = dict(
        summary="Nice work.", strengths=["Basics"], areas_to_improve=["Signs"], focus_topics=["Negatives"]
    )
    defaults.update(overrides)
    return FeedbackContent(**defaults)


def _feedback(**overrides) -> StudentFeedback:
    defaults = dict(session_id=SESSION_ID, test_id=TEST_ID, status=FeedbackStatus.generating)
    defaults.update(overrides)
    return StudentFeedback(**defaults)


def _patch_ownership(monkeypatch, *, test=None, session=None) -> None:
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: store.Stored(test or _test_model(), 1))
    monkeypatch.setattr(sessions_repo, "get_session", lambda tid, sid: store.Stored(session or _session(), 1))


# --- email_feedback: ownership ---------------------------------------------------


def test_email_feedback_unknown_test_raises_not_found(monkeypatch):
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: None)

    with pytest.raises(NotFoundError):
        feedback_service.email_feedback(TEACHER_SUB, TEST_ID, SESSION_ID)


def test_email_feedback_unknown_session_raises_not_found(monkeypatch):
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: store.Stored(_test_model(), 1))
    monkeypatch.setattr(sessions_repo, "get_session", lambda tid, sid: None)

    with pytest.raises(NotFoundError):
        feedback_service.email_feedback(TEACHER_SUB, TEST_ID, SESSION_ID)


# --- email_feedback: readiness ---------------------------------------------------


@pytest.mark.parametrize(
    "feedback",
    [
        _feedback(status=FeedbackStatus.generating, generation_started_at=now()),
        _feedback(status=FeedbackStatus.failed, error="model unavailable"),
    ],
)
def test_email_feedback_raises_conflict_when_not_ready(monkeypatch, feedback):
    _patch_ownership(monkeypatch)
    monkeypatch.setattr(feedback_repo, "get_feedback", lambda tid, sid: store.Stored(feedback, 1))

    with pytest.raises(ConflictError):
        feedback_service.email_feedback(TEACHER_SUB, TEST_ID, SESSION_ID)


def test_email_feedback_raises_conflict_when_the_item_is_missing(monkeypatch):
    _patch_ownership(monkeypatch)
    monkeypatch.setattr(feedback_repo, "get_feedback", lambda tid, sid: None)

    with pytest.raises(ConflictError):
        feedback_service.email_feedback(TEACHER_SUB, TEST_ID, SESSION_ID)


def test_email_feedback_raises_conflict_when_the_session_is_not_completed(monkeypatch):
    _patch_ownership(monkeypatch, session=_session(status=SessionStatus.started))
    ready = _feedback(status=FeedbackStatus.ready, content=_content(), generated_at=now())
    monkeypatch.setattr(feedback_repo, "get_feedback", lambda tid, sid: store.Stored(ready, 1))

    with pytest.raises(ConflictError):
        feedback_service.email_feedback(TEACHER_SUB, TEST_ID, SESSION_ID)


# --- email_feedback: sending ------------------------------------------------------


def test_email_feedback_send_failure_raises_upstream_and_does_not_write_email_sent_at(monkeypatch):
    _patch_ownership(monkeypatch)
    ready = _feedback(status=FeedbackStatus.ready, content=_content(), generated_at=now())
    monkeypatch.setattr(feedback_repo, "get_feedback", lambda tid, sid: store.Stored(ready, 1))
    monkeypatch.setattr(
        feedback_service, "send_feedback", lambda **kw: (_ for _ in ()).throw(RuntimeError("smtp down"))
    )
    writes = []
    monkeypatch.setattr(feedback_repo, "update_feedback", lambda fb, v: writes.append(fb) or v + 1)

    with pytest.raises(UpstreamError):
        feedback_service.email_feedback(TEACHER_SUB, TEST_ID, SESSION_ID)

    assert writes == []


def test_email_feedback_success_sends_and_records_email_sent_at(monkeypatch):
    _patch_ownership(monkeypatch)
    ready = _feedback(status=FeedbackStatus.ready, content=_content(), generated_at=now())
    monkeypatch.setattr(feedback_repo, "get_feedback", lambda tid, sid: store.Stored(ready, 3))
    sent = []
    monkeypatch.setattr(feedback_service, "send_feedback", lambda **kw: sent.append(kw))
    writes = []
    monkeypatch.setattr(feedback_repo, "update_feedback", lambda fb, v: writes.append((fb, v)) or v + 1)

    result = feedback_service.email_feedback(TEACHER_SUB, TEST_ID, SESSION_ID)

    assert len(sent) == 1
    assert sent[0]["student_email"] == "ada@example.com"
    assert sent[0]["score"] == 50
    assert len(writes) == 1
    written_fb, written_version = writes[0]
    assert written_version == 3
    assert written_fb.email_sent_at is not None
    assert result.email_sent_at is not None


def test_email_feedback_write_conflict_on_email_sent_at_is_swallowed(monkeypatch):
    """The mail already went out -- a write conflict recording that must not
    surface as a failure."""
    _patch_ownership(monkeypatch)
    ready = _feedback(status=FeedbackStatus.ready, content=_content(), generated_at=now())
    monkeypatch.setattr(feedback_repo, "get_feedback", lambda tid, sid: store.Stored(ready, 3))
    monkeypatch.setattr(feedback_service, "send_feedback", lambda **kw: None)
    monkeypatch.setattr(
        feedback_repo, "update_feedback", lambda fb, v: (_ for _ in ()).throw(ConflictError("conflict"))
    )

    result = feedback_service.email_feedback(TEACHER_SUB, TEST_ID, SESSION_ID)  # must not raise
    assert result.email_sent_at is not None


# --- regenerate: ownership and readiness -----------------------------------------


def test_regenerate_unknown_test_raises_not_found(monkeypatch):
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: None)

    with pytest.raises(NotFoundError):
        feedback_service.regenerate(TEACHER_SUB, TEST_ID, SESSION_ID)


def test_regenerate_session_not_completed_raises_not_found(monkeypatch):
    _patch_ownership(monkeypatch, session=_session(status=SessionStatus.started))

    with pytest.raises(NotFoundError):
        feedback_service.regenerate(TEACHER_SUB, TEST_ID, SESSION_ID)


def test_regenerate_raises_conflict_when_still_generating(monkeypatch):
    _patch_ownership(monkeypatch)
    generating = _feedback(status=FeedbackStatus.generating, generation_started_at=now())
    monkeypatch.setattr(feedback_repo, "get_feedback", lambda tid, sid: store.Stored(generating, 1))

    with pytest.raises(ConflictError):
        feedback_service.regenerate(TEACHER_SUB, TEST_ID, SESSION_ID)


def test_regenerate_raises_conflict_when_already_ready(monkeypatch):
    _patch_ownership(monkeypatch)
    ready = _feedback(status=FeedbackStatus.ready, content=_content(), generated_at=now())
    monkeypatch.setattr(feedback_repo, "get_feedback", lambda tid, sid: store.Stored(ready, 1))

    with pytest.raises(ConflictError):
        feedback_service.regenerate(TEACHER_SUB, TEST_ID, SESSION_ID)


# --- regenerate: resetting -------------------------------------------------------


def test_regenerate_resets_a_failed_row_and_preserves_email_sent_at(monkeypatch):
    _patch_ownership(monkeypatch)
    sent_at = now()
    failed = _feedback(status=FeedbackStatus.failed, error="model unavailable", email_sent_at=sent_at)
    monkeypatch.setattr(feedback_repo, "get_feedback", lambda tid, sid: store.Stored(failed, 4))
    writes = []
    monkeypatch.setattr(feedback_repo, "update_feedback", lambda fb, v: writes.append((fb, v)) or v + 1)

    result = feedback_service.regenerate(TEACHER_SUB, TEST_ID, SESSION_ID)

    assert result.status == FeedbackStatus.generating
    assert result.error is None
    assert result.content is None
    assert result.generated_at is None
    assert result.generation_started_at is not None
    assert result.email_sent_at == sent_at
    assert writes[0][1] == 4


def test_regenerate_creates_a_placeholder_when_the_item_is_missing(monkeypatch):
    _patch_ownership(monkeypatch)
    monkeypatch.setattr(feedback_repo, "get_feedback", lambda tid, sid: None)
    created = []
    monkeypatch.setattr(feedback_repo, "create_feedback", lambda fb: created.append(fb))

    result = feedback_service.regenerate(TEACHER_SUB, TEST_ID, SESSION_ID)

    assert result.status == FeedbackStatus.generating
    assert len(created) == 1
    assert created[0].test_id == TEST_ID
    assert created[0].session_id == SESSION_ID


def test_regenerate_concurrent_write_conflict_propagates(monkeypatch):
    _patch_ownership(monkeypatch)
    failed = _feedback(status=FeedbackStatus.failed, error="oops")
    monkeypatch.setattr(feedback_repo, "get_feedback", lambda tid, sid: store.Stored(failed, 4))
    monkeypatch.setattr(
        feedback_repo, "update_feedback", lambda fb, v: (_ for _ in ()).throw(ConflictError("conflict"))
    )

    with pytest.raises(ConflictError):
        feedback_service.regenerate(TEACHER_SUB, TEST_ID, SESSION_ID)
