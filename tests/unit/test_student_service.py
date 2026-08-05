"""Unit tests for student_service business rules. The repository layer and
the email sender are monkeypatched so these run without DynamoDB or a real
send -- the full round trip (sessions persisted, emails landing in the
outbox, GET /students reflecting them) is covered by
tests/integration/test_students_api.py instead."""

from datetime import timedelta

import pytest

from app.core.clock import now
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.session import SessionStatus, StudentSession
from app.models.test import Difficulty, Test, TestStatus
from app.repositories import sessions_repo, store, tests_repo
from app.schemas.students import AddStudentsRequest, PublishRequest, StudentInput
from app.services import student_service


def _test(**overrides) -> Test:
    defaults = dict(
        test_id="01TESTID",
        teacher_sub="dev-alice",
        title="Sample",
        difficulty=Difficulty.easy,
        duration_seconds=600,
        status=TestStatus.draft,
        question_count=1,
        created_at=now(),
    )
    defaults.update(overrides)
    return Test(**defaults)


def _existing_session(email: str, **overrides) -> StudentSession:
    defaults = dict(
        session_id="01EXISTING",
        test_id="01TESTID",
        student_name="Existing Student",
        student_email=email,
        status=SessionStatus.invited,
        link_token="existing-token",
        invited_at=now(),
    )
    defaults.update(overrides)
    return StudentSession(**defaults)


def _patch_test(monkeypatch, test: Test, version: int = 1):
    stored = store.Stored(test, version)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: stored)
    monkeypatch.setattr(tests_repo, "update_test", lambda sub, t, v: v + 1)


def _request(*pairs: tuple[str, str]) -> AddStudentsRequest:
    return AddStudentsRequest(students=[StudentInput(name=n, email=e) for n, e in pairs])


# --- add_students dedupe ---------------------------------------------------


def test_add_students_skips_email_already_invited_case_insensitively(monkeypatch):
    _patch_test(monkeypatch, _test())
    monkeypatch.setattr(
        sessions_repo, "list_sessions", lambda tid: [_existing_session("Ada@Example.com")]
    )
    monkeypatch.setattr(sessions_repo, "create_sessions", lambda sessions, sub: None)

    result = student_service.add_students(
        "dev-alice", "01TESTID", _request(("Ada", "ada@example.com"), ("Bob", "bob@example.com"))
    )

    assert result.skipped_emails == ["ada@example.com"]
    assert [row.student_email for row in result.added] == ["bob@example.com"]


def test_add_students_skips_duplicates_within_the_same_batch(monkeypatch):
    _patch_test(monkeypatch, _test())
    monkeypatch.setattr(sessions_repo, "list_sessions", lambda tid: [])
    monkeypatch.setattr(sessions_repo, "create_sessions", lambda sessions, sub: None)

    result = student_service.add_students(
        "dev-alice",
        "01TESTID",
        _request(("Ada", "ada@example.com"), ("Ada Again", "ADA@EXAMPLE.COM")),
    )

    # EmailStr lowercases the domain, so the stored value is "ADA@example.com".
    assert result.skipped_emails == ["ADA@example.com"]
    assert len(result.added) == 1


def test_add_students_missing_test_raises_not_found(monkeypatch):
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: None)

    with pytest.raises(NotFoundError):
        student_service.add_students("dev-alice", "nope", _request(("Ada", "ada@example.com")))


def test_add_students_on_draft_test_does_not_send_invitations(monkeypatch):
    _patch_test(monkeypatch, _test(status=TestStatus.draft))
    monkeypatch.setattr(sessions_repo, "list_sessions", lambda tid: [])
    monkeypatch.setattr(sessions_repo, "create_sessions", lambda sessions, sub: None)
    sent = []
    monkeypatch.setattr(student_service, "send_invitation", lambda **kwargs: sent.append(kwargs))

    student_service.add_students("dev-alice", "01TESTID", _request(("Ada", "ada@example.com")))

    assert sent == []


def test_add_students_on_published_test_sends_invitations_immediately(monkeypatch):
    deadline = now() + timedelta(days=1)
    _patch_test(monkeypatch, _test(status=TestStatus.published, deadline=deadline))
    monkeypatch.setattr(sessions_repo, "list_sessions", lambda tid: [])
    monkeypatch.setattr(sessions_repo, "create_sessions", lambda sessions, sub: None)
    sent = []
    monkeypatch.setattr(student_service, "send_invitation", lambda **kwargs: sent.append(kwargs))

    student_service.add_students("dev-alice", "01TESTID", _request(("Ada", "ada@example.com")))

    assert len(sent) == 1
    assert sent[0]["student_email"] == "ada@example.com"
    assert sent[0]["link"].endswith(sent[0]["link"].rsplit("/", 1)[-1])  # has a /t/<token> suffix
    assert "/t/" in sent[0]["link"]


def test_add_students_bumps_student_count(monkeypatch):
    _patch_test(monkeypatch, _test(student_count=2))
    monkeypatch.setattr(sessions_repo, "list_sessions", lambda tid: [])
    monkeypatch.setattr(sessions_repo, "create_sessions", lambda sessions, sub: None)
    updates = []
    monkeypatch.setattr(
        tests_repo, "update_test", lambda sub, t, v: updates.append(t) or v + 1
    )

    student_service.add_students(
        "dev-alice", "01TESTID", _request(("Ada", "ada@example.com"), ("Bob", "bob@example.com"))
    )

    assert updates[0].student_count == 4


# --- publish_test rules -----------------------------------------------------


def test_publish_already_published_test_raises_conflict(monkeypatch):
    _patch_test(monkeypatch, _test(status=TestStatus.published, deadline=now() + timedelta(days=1)))

    with pytest.raises(ConflictError):
        student_service.publish_test(
            "dev-alice", "01TESTID", PublishRequest(deadline=now() + timedelta(days=2))
        )


def test_publish_without_questions_raises_conflict(monkeypatch):
    _patch_test(monkeypatch, _test(question_count=0))

    with pytest.raises(ConflictError):
        student_service.publish_test(
            "dev-alice", "01TESTID", PublishRequest(deadline=now() + timedelta(days=1))
        )


def test_publish_with_past_deadline_raises_bad_request(monkeypatch):
    _patch_test(monkeypatch, _test())

    with pytest.raises(BadRequestError):
        student_service.publish_test(
            "dev-alice", "01TESTID", PublishRequest(deadline=now() - timedelta(days=1))
        )


def test_publish_sends_invitations_only_to_invited_sessions(monkeypatch):
    _patch_test(monkeypatch, _test())
    invited = _existing_session("invited@example.com", status=SessionStatus.invited)
    started = _existing_session(
        "started@example.com", session_id="01STARTED", status=SessionStatus.started
    )
    monkeypatch.setattr(sessions_repo, "list_sessions", lambda tid: [invited, started])
    sent = []
    monkeypatch.setattr(student_service, "send_invitation", lambda **kwargs: sent.append(kwargs))

    deadline = now() + timedelta(days=1)
    result = student_service.publish_test("dev-alice", "01TESTID", PublishRequest(deadline=deadline))

    assert result.status == TestStatus.published
    assert len(sent) == 1
    assert sent[0]["student_email"] == "invited@example.com"


def test_publish_email_failure_does_not_raise(monkeypatch):
    _patch_test(monkeypatch, _test())
    invited = _existing_session("invited@example.com", status=SessionStatus.invited)
    monkeypatch.setattr(sessions_repo, "list_sessions", lambda tid: [invited])

    def _boom(**kwargs):
        raise RuntimeError("smtp is down")

    monkeypatch.setattr(student_service, "send_invitation", _boom)

    # Must not raise -- the test is already published and the invitation
    # records already exist; a transport failure is logged, not propagated.
    result = student_service.publish_test(
        "dev-alice", "01TESTID", PublishRequest(deadline=now() + timedelta(days=1))
    )
    assert result.status == TestStatus.published
