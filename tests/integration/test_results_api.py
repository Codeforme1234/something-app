"""Integration tests against DynamoDB Local for the teacher-facing results
endpoints: derived effective_status on the roster, per-student review, and
test analytics. Run `docker compose up -d` first (see CLAUDE.md);
conftest.py creates the throwaway table.
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
    return {"Authorization": f"Bearer dev-{uuid.uuid4().hex[:12]}"}


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


def _add_student(headers: dict, test_id: str, name: str, email: str) -> None:
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


def _token_for(email: str) -> str:
    for message in _outbox():
        if message["to"] == email:
            link = message["student_link"]
            assert link is not None
            return link.rsplit("/t/", 1)[-1]
    raise AssertionError(f"no outbox message for {email}")


def _complete_attempt(token: str, first_correct: bool, second_correct: bool) -> None:
    start = client.post(f"/api/v1/take/{token}/start")
    assert start.status_code == 200, start.text
    questions = start.json()["questions"]
    answers = {
        questions[0]["question_id"]: 1 if first_correct else 0,
        questions[1]["question_id"]: 1 if second_correct else 0,
    }
    submit = client.post(f"/api/v1/take/{token}/submit", json={"answers": answers})
    assert submit.status_code == 200, submit.text


def _setup_two_students_one_completed(headers: dict) -> tuple[str, str, str]:
    """Returns (test_id, completed_session_id, invited_session_id)."""
    test = _create_test(headers)
    test_id = test["test_id"]
    _put_two_questions(headers, test_id)
    _add_student(headers, test_id, "Ada Lovelace", "ada@example.com")
    _add_student(headers, test_id, "Bob Smith", "bob@example.com")
    _publish(headers, test_id)

    ada_token = _token_for("ada@example.com")
    _complete_attempt(ada_token, first_correct=True, second_correct=False)  # score 50

    rows = client.get(f"/api/v1/tests/{test_id}/students", headers=headers).json()
    completed_row = next(r for r in rows if r["student_email"] == "ada@example.com")
    invited_row = next(r for r in rows if r["student_email"] == "bob@example.com")
    return test_id, completed_row["session_id"], invited_row["session_id"]


# --- students list: effective_status ------------------------------------------


def test_students_list_shows_effective_status_and_score():
    headers = _headers()
    test_id, completed_id, invited_id = _setup_two_students_one_completed(headers)

    rows = client.get(f"/api/v1/tests/{test_id}/students", headers=headers).json()
    by_id = {r["session_id"]: r for r in rows}

    assert by_id[completed_id]["status"] == "completed"
    assert by_id[completed_id]["effective_status"] == "completed"
    assert by_id[completed_id]["score"] == 50

    assert by_id[invited_id]["status"] == "invited"
    assert by_id[invited_id]["effective_status"] == "invited"
    assert by_id[invited_id]["score"] is None


def test_invited_session_past_deadline_shows_link_expired():
    headers = _headers()
    test = _create_test(headers)
    test_id = test["test_id"]
    _put_two_questions(headers, test_id)
    _add_student(headers, test_id, "Eve", "eve@example.com")
    _publish(headers, test_id)

    from app.repositories import tests_repo

    sub = headers["Authorization"].removeprefix("Bearer ")
    stored = tests_repo.get_test(sub, test_id)
    past_deadline = datetime.now(UTC) - timedelta(days=1)
    updated = stored.model.model_copy(update={"deadline": past_deadline})
    tests_repo.update_test(sub, updated, stored.version)

    rows = client.get(f"/api/v1/tests/{test_id}/students", headers=headers).json()
    assert rows[0]["status"] == "invited"
    assert rows[0]["effective_status"] == "link_expired"


def test_started_session_past_grace_shows_expired():
    headers = _headers()
    test = _create_test(headers)
    test_id = test["test_id"]
    _put_two_questions(headers, test_id)
    _add_student(headers, test_id, "Eve", "eve@example.com")
    _publish(headers, test_id)
    token = _token_for("eve@example.com")

    start = client.post(f"/api/v1/take/{token}/start")
    assert start.status_code == 200, start.text

    from app.repositories import sessions_repo

    lookup_stored = sessions_repo.get_token_lookup(token)
    lookup = lookup_stored.model
    stored = sessions_repo.get_session(lookup.test_id, lookup.session_id)
    updated = stored.model.model_copy(update={"ends_at": datetime.now(UTC) - timedelta(minutes=5)})
    sessions_repo.update_session(updated, stored.version)

    rows = client.get(f"/api/v1/tests/{test_id}/students", headers=headers).json()
    assert rows[0]["status"] == "started"
    assert rows[0]["effective_status"] == "expired"
    assert rows[0]["score"] is None


# --- per-student detail ---------------------------------------------------------


def test_student_detail_shows_review_with_chosen_vs_correct():
    headers = _headers()
    test_id, completed_id, _ = _setup_two_students_one_completed(headers)

    resp = client.get(f"/api/v1/tests/{test_id}/students/{completed_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["session"]["session_id"] == completed_id
    assert body["session"]["effective_status"] == "completed"
    assert body["session"]["score"] == 50

    review = body["review"]
    assert review is not None
    assert len(review) == 2
    review_by_order = {q["order"]: q for q in review}
    # First question answered correctly (correct_index 1, chosen 1).
    assert review_by_order[1]["correct_index"] == 1
    assert review_by_order[1]["chosen_index"] == 1
    assert review_by_order[1]["is_correct"] is True
    # Second answered incorrectly (correct_index 1, chosen 0).
    assert review_by_order[2]["correct_index"] == 1
    assert review_by_order[2]["chosen_index"] == 0
    assert review_by_order[2]["is_correct"] is False


def test_student_detail_for_invited_session_has_null_review():
    headers = _headers()
    test_id, _, invited_id = _setup_two_students_one_completed(headers)

    resp = client.get(f"/api/v1/tests/{test_id}/students/{invited_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["session"]["session_id"] == invited_id
    assert body["session"]["effective_status"] == "invited"
    assert body["review"] is None


def test_student_detail_session_from_different_test_returns_404():
    headers = _headers()
    test_id_a, completed_id, _ = _setup_two_students_one_completed(headers)

    other_test = _create_test(headers, title="Other Test")
    resp = client.get(
        f"/api/v1/tests/{other_test['test_id']}/students/{completed_id}", headers=headers
    )
    assert resp.status_code == 404, resp.text


# --- analytics -------------------------------------------------------------------


def test_analytics_returns_correct_aggregates():
    headers = _headers()
    test_id, _, _ = _setup_two_students_one_completed(headers)

    resp = client.get(f"/api/v1/tests/{test_id}/analytics", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["student_count"] == 2
    assert body["completed_count"] == 1
    assert body["completion_rate"] == 50
    assert body["average_score"] == 50
    assert body["highest_score"] == 50
    assert body["lowest_score"] == 50

    stats_by_order = {qs["order"]: qs for qs in body["question_stats"]}
    assert stats_by_order[1]["correct_count"] == 1
    assert stats_by_order[1]["attempt_count"] == 1
    assert stats_by_order[1]["correct_rate"] == 100
    assert stats_by_order[2]["correct_count"] == 0
    assert stats_by_order[2]["attempt_count"] == 1
    assert stats_by_order[2]["correct_rate"] == 0
    # Hardest first: question 2 (0% correct) before question 1 (100%).
    assert [qs["order"] for qs in body["question_stats"]] == [2, 1]


def test_analytics_with_no_students_is_all_zero_and_no_scores():
    headers = _headers()
    test = _create_test(headers)
    _put_two_questions(headers, test["test_id"])

    resp = client.get(f"/api/v1/tests/{test['test_id']}/analytics", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["student_count"] == 0
    assert body["completed_count"] == 0
    assert body["completion_rate"] == 0
    assert body["average_score"] is None
    assert body["highest_score"] is None
    assert body["lowest_score"] is None
    assert all(qs["attempt_count"] == 0 and qs["correct_rate"] == 0 for qs in body["question_stats"])


# --- ownership: teacher B gets 404 on both new endpoints ------------------------


def test_teacher_b_gets_404_on_student_detail_and_analytics():
    alice = _headers()
    bob = _headers()
    test_id, completed_id, _ = _setup_two_students_one_completed(alice)

    assert (
        client.get(f"/api/v1/tests/{test_id}/students/{completed_id}", headers=bob).status_code == 404
    )
    assert client.get(f"/api/v1/tests/{test_id}/analytics", headers=bob).status_code == 404
