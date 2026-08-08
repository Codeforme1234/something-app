"""Integration tests for post-submission LLM feedback: the full submit ->
generate -> teacher-view -> email/regenerate flow. Backed by moto (see
tests/conftest.py); tests/integration/conftest.py pins LLM_MODE=fake and
EMAIL_MODE=outbox for the whole package, and TestClient runs BackgroundTasks
synchronously, so feedback is ready right after submit -- no polling needed.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_outbox(monkeypatch, tmp_path):
    """Same isolation as test_students_api.py / test_take_api.py."""
    from app.core.config import get_settings

    monkeypatch.setenv("OUTBOX_DIR", str(tmp_path / "outbox"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _headers() -> dict:
    headers = {"Authorization": f"Bearer dev-{uuid.uuid4().hex[:12]}"}
    client.get("/api/v1/me", headers=headers)
    return headers


def _create_test(headers: dict, **overrides) -> dict:
    payload = {"title": "Algebra Basics", "difficulty": "medium", "duration_seconds": 1800}
    payload.update(overrides)
    resp = client.post("/api/v1/tests", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _put_two_questions(headers: dict, test_id: str) -> None:
    payload = {
        "questions": [
            {"stem": "2 + 2?", "options": ["3", "4", "5", "6"], "correct_index": 1},
            {"stem": "3 + 3?", "options": ["5", "6", "7", "8"], "correct_index": 1},
        ]
    }
    resp = client.put(f"/api/v1/tests/{test_id}/questions", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text


def _add_student(headers: dict, test_id: str, name: str = "Ada Lovelace", email: str = "ada@example.com") -> None:
    resp = client.post(
        f"/api/v1/tests/{test_id}/students",
        json={"students": [{"name": name, "email": email}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


def _publish(headers: dict, test_id: str, deadline: datetime | None = None) -> dict:
    deadline = deadline or (datetime.now(UTC) + timedelta(days=7))
    resp = client.post(
        f"/api/v1/tests/{test_id}/publish", json={"deadline": deadline.isoformat()}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _outbox() -> list[dict]:
    resp = client.get("/api/v1/dev/outbox")
    assert resp.status_code == 200
    return resp.json()


def _newest_outbox_token() -> str:
    messages = _outbox()
    assert messages, "expected at least one outbox message"
    link = messages[0]["student_link"]
    assert link is not None
    return link.rsplit("/t/", 1)[-1]


def _setup_completed_student(headers: dict, *, first_correct: bool = True, second_correct: bool = False):
    """Creates a 2-question published test, invites one student, and submits
    a graded attempt for them. Returns (test_id, session_id, token)."""
    test = _create_test(headers)
    test_id = test["test_id"]
    _put_two_questions(headers, test_id)
    _add_student(headers, test_id)
    _publish(headers, test_id)
    token = _newest_outbox_token()

    take_info = client.get(f"/api/v1/take/{token}")
    assert take_info.status_code == 200, take_info.text
    assert take_info.json()["score"] is None  # not completed yet

    start = client.post(f"/api/v1/take/{token}/start")
    assert start.status_code == 200, start.text
    questions = start.json()["questions"]
    answers = {
        questions[0]["question_id"]: 1 if first_correct else 0,
        questions[1]["question_id"]: 1 if second_correct else 0,
    }
    submit = client.post(f"/api/v1/take/{token}/submit", json={"answers": answers})
    assert submit.status_code == 200, submit.text
    assert submit.json()["score"] == 50

    rows = client.get(f"/api/v1/tests/{test_id}/students", headers=headers).json()
    session_id = rows[0]["session_id"]
    return test_id, session_id, token


def _feedback_stored(test_id: str, session_id: str):
    from app.repositories import feedback_repo

    return feedback_repo.get_feedback(test_id, session_id)


# --- submit carries score, background feedback generation ----------------------


def test_submit_response_carries_score_and_counts():
    headers = _headers()
    _, _, _ = _setup_completed_student(headers)  # assertions live inside the helper


def test_completed_session_gets_ready_feedback_with_a_summary():
    headers = _headers()
    test_id, session_id, _ = _setup_completed_student(headers)

    detail = client.get(f"/api/v1/tests/{test_id}/students/{session_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    feedback = detail.json()["feedback"]
    assert feedback["status"] == "ready"
    assert feedback["summary"]
    assert isinstance(feedback["strengths"], list)
    assert isinstance(feedback["improvement_areas"], list)
    assert isinstance(feedback["study_plan"], list)
    assert isinstance(feedback["topic_breakdown"], list)
    # v2 never produces the v1-compat flat lists -- empty, not populated.
    assert feedback["areas_to_improve"] == []
    assert feedback["focus_topics"] == []
    assert feedback["email_sent_at"] is None


def test_mixed_score_feedback_has_topic_breakdown_and_improvement_areas():
    """One question right, one wrong -- v2's structured sections should
    reflect that mix: a non-empty topic_breakdown, and at least one
    improvement_area for the missed question."""
    headers = _headers()
    test_id, session_id, _ = _setup_completed_student(headers, first_correct=True, second_correct=False)

    detail = client.get(f"/api/v1/tests/{test_id}/students/{session_id}", headers=headers)
    feedback = detail.json()["feedback"]

    assert len(feedback["topic_breakdown"]) >= 1
    for row in feedback["topic_breakdown"]:
        assert row["total"] >= 1
        assert 0 <= row["correct"] <= row["total"]
    assert len(feedback["improvement_areas"]) >= 1
    for area in feedback["improvement_areas"]:
        assert area["topic"]
        assert area["diagnosis"]
        assert area["action"]


def test_non_completed_session_has_no_feedback():
    headers = _headers()
    test = _create_test(headers)
    test_id = test["test_id"]
    _put_two_questions(headers, test_id)
    _add_student(headers, test_id)
    _publish(headers, test_id)

    rows = client.get(f"/api/v1/tests/{test_id}/students", headers=headers).json()
    session_id = rows[0]["session_id"]

    detail = client.get(f"/api/v1/tests/{test_id}/students/{session_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["feedback"] is None


def test_missing_feedback_item_presents_as_failed():
    """Simulates the placeholder write in feedback_job.start having failed --
    the teacher must see an explanation, not a blank."""
    from app.repositories import keys, store

    headers = _headers()
    test_id, session_id, _ = _setup_completed_student(headers)
    store.delete(keys.test_pk(test_id), keys.feedback_sk(session_id))

    detail = client.get(f"/api/v1/tests/{test_id}/students/{session_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    feedback = detail.json()["feedback"]
    assert feedback["status"] == "failed"
    assert feedback["error"]


# --- POST .../feedback/email ----------------------------------------------------


def test_email_feedback_sends_and_records_email_sent_at():
    headers = _headers()
    test_id, session_id, _ = _setup_completed_student(headers)

    resp = client.post(f"/api/v1/tests/{test_id}/students/{session_id}/feedback/email", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["email_sent_at"] is not None

    messages = _outbox()
    assert messages, "expected an outbox message"
    newest = messages[0]
    assert newest["to"] == "ada@example.com"
    assert "Algebra Basics" in newest["subject"]
    assert "50%" in newest["text"]
    # v2 sections: an improvement area's action line, and a topic-breakdown
    # count in "topic — correct/total" shape.
    assert "Try this:" in newest["text"]
    assert "— 1/1" in newest["text"] or "— 0/1" in newest["text"]
    # feedback_service.email_feedback always builds the student's take-page
    # link, the same way student_service builds an invite link.
    assert "View your test:" in newest["text"]
    assert "/t/" in newest["text"]

    detail = client.get(f"/api/v1/tests/{test_id}/students/{session_id}", headers=headers).json()
    assert detail["feedback"]["email_sent_at"] is not None


def test_email_feedback_can_be_resent():
    headers = _headers()
    test_id, session_id, _ = _setup_completed_student(headers)

    first = client.post(f"/api/v1/tests/{test_id}/students/{session_id}/feedback/email", headers=headers)
    assert first.status_code == 200, first.text
    second = client.post(f"/api/v1/tests/{test_id}/students/{session_id}/feedback/email", headers=headers)
    assert second.status_code == 200, second.text

    # The invitation email plus both feedback sends.
    assert len(_outbox()) == 3


def test_email_feedback_returns_409_when_not_ready():
    headers = _headers()
    test_id, session_id, _ = _setup_completed_student(headers)

    stored = _feedback_stored(test_id, session_id)
    assert stored is not None
    from app.models.feedback import FeedbackStatus
    from app.repositories import feedback_repo

    generating = stored.model.model_copy(update={"status": FeedbackStatus.generating})
    feedback_repo.update_feedback(generating, stored.version)

    resp = client.post(f"/api/v1/tests/{test_id}/students/{session_id}/feedback/email", headers=headers)
    assert resp.status_code == 409, resp.text


def test_email_feedback_unknown_session_returns_404():
    headers = _headers()
    test = _create_test(headers)
    _put_two_questions(headers, test["test_id"])

    resp = client.post(
        f"/api/v1/tests/{test['test_id']}/students/nonexistent-session/feedback/email", headers=headers
    )
    assert resp.status_code == 404, resp.text


def test_feedback_routes_return_404_for_another_teachers_test():
    alice = _headers()
    bob = _headers()
    test_id, session_id, _ = _setup_completed_student(alice)

    assert (
        client.post(f"/api/v1/tests/{test_id}/students/{session_id}/feedback/email", headers=bob).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/tests/{test_id}/students/{session_id}/feedback/regenerate", headers=bob
        ).status_code
        == 404
    )


# --- POST .../feedback/regenerate -----------------------------------------------


def test_regenerate_a_failed_item_becomes_ready():
    headers = _headers()
    test_id, session_id, _ = _setup_completed_student(headers)

    stored = _feedback_stored(test_id, session_id)
    assert stored is not None
    from app.models.feedback import FeedbackStatus
    from app.repositories import feedback_repo

    failed = stored.model.model_copy(
        update={"status": FeedbackStatus.failed, "error": "boom", "content": None}
    )
    feedback_repo.update_feedback(failed, stored.version)

    resp = client.post(
        f"/api/v1/tests/{test_id}/students/{session_id}/feedback/regenerate", headers=headers
    )
    assert resp.status_code == 202, resp.text

    detail = client.get(f"/api/v1/tests/{test_id}/students/{session_id}", headers=headers).json()
    assert detail["feedback"]["status"] == "ready"
    assert detail["feedback"]["summary"]
