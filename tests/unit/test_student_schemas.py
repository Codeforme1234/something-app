"""Unit tests for the students DTOs -- the strict input caps (see
app/schemas/students.py) and the no-token guarantee on the teacher-facing
SessionRow response."""

import pytest
from pydantic import ValidationError

from app.core.clock import now
from app.models.session import SessionStatus, StudentSession
from app.schemas.students import AddStudentsRequest, SessionRow, StudentInput


def _session(**overrides) -> StudentSession:
    defaults = dict(
        session_id="01SESSION",
        test_id="01TEST",
        student_name="Ada Lovelace",
        student_email="ada@example.com",
        status=SessionStatus.invited,
        link_token="super-secret-token",
        invited_at=now(),
    )
    defaults.update(overrides)
    return StudentSession(**defaults)


def test_student_name_must_be_nonblank_after_strip():
    with pytest.raises(ValidationError):
        StudentInput(name="   ", email="a@example.com")


def test_student_name_is_stripped():
    student = StudentInput(name="  Ada  ", email="a@example.com")
    assert student.name == "Ada"


def test_student_name_max_length_enforced():
    with pytest.raises(ValidationError):
        StudentInput(name="x" * 121, email="a@example.com")


def test_student_email_must_be_valid():
    with pytest.raises(ValidationError):
        StudentInput(name="Ada", email="not-an-email")


def test_add_students_request_rejects_empty_list():
    with pytest.raises(ValidationError):
        AddStudentsRequest(students=[])


def test_add_students_request_rejects_more_than_200():
    students = [{"name": f"S{i}", "email": f"s{i}@example.com"} for i in range(201)]
    with pytest.raises(ValidationError):
        AddStudentsRequest(students=students)


def test_add_students_request_accepts_exactly_200():
    students = [{"name": f"S{i}", "email": f"s{i}@example.com"} for i in range(200)]
    AddStudentsRequest(students=students)


def test_session_row_has_no_token_field():
    row = SessionRow.from_model(_session(), "invited")
    assert "link_token" not in SessionRow.model_fields
    assert "link_token" not in row.model_dump()


def test_session_row_from_model_round_trips_visible_fields():
    session = _session(status=SessionStatus.completed, score=80)
    row = SessionRow.from_model(session, "completed")
    assert row.session_id == session.session_id
    assert row.student_name == session.student_name
    assert row.student_email == session.student_email
    assert row.status == SessionStatus.completed
    assert row.effective_status == "completed"
    assert row.score == 80
