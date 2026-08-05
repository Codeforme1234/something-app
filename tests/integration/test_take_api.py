"""Integration tests against DynamoDB Local for the student attempt flow:
token resolution, timed start, and server-side grading. Run `docker compose
up -d` first (see CLAUDE.md); conftest.py creates the throwaway table.

Deadline-passed and time-up paths are exercised by writing an
already-in-the-past deadline/ends_at directly through the repository layer
(same pattern as test_tests_api.py's `test_draft_only_mutation_rules`)
rather than sleeping.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_outbox(monkeypatch, tmp_path):
    """Point the dev outbox at a throwaway directory, same as
    test_students_api.py, so these tests never read stale invitations."""
    from app.core.config import get_settings

    monkeypatch.setenv("OUTBOX_DIR", str(tmp_path / "outbox"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _headers() -> dict:
    headers = {"Authorization": f"Bearer dev-{uuid.uuid4().hex[:12]}"}
    # Provisions this admin's company, exactly as the real app's AppShell
    # does (it always calls /me) before any page that could create a test.
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


def _newest_outbox_token() -> str:
    resp = client.get("/api/v1/dev/outbox")
    assert resp.status_code == 200
    messages = resp.json()
    assert messages, "expected at least one outbox message"
    link = messages[0]["student_link"]
    assert link is not None
    return link.rsplit("/t/", 1)[-1]


def _set_test_deadline(headers: dict, test_id: str, deadline: datetime) -> None:
    from app.repositories import tests_repo

    sub = headers["Authorization"].removeprefix("Bearer ")
    stored = tests_repo.get_test(sub, test_id)
    updated = stored.model.model_copy(update={"deadline": deadline})
    tests_repo.update_test(sub, updated, stored.version)


def _set_session_ends_at(token: str, ends_at: datetime) -> None:
    from app.repositories import sessions_repo

    lookup_stored = sessions_repo.get_token_lookup(token)
    assert lookup_stored is not None
    lookup = lookup_stored.model
    stored = sessions_repo.get_session(lookup.test_id, lookup.session_id)
    assert stored is not None
    updated = stored.model.model_copy(update={"ends_at": ends_at})
    sessions_repo.update_session(updated, stored.version)


def _setup_published_test_with_student(headers: dict) -> tuple[str, str]:
    """Creates a 2-question published test with one invited student.
    Returns (test_id, student_link_token)."""
    test = _create_test(headers)
    _put_two_questions(headers, test["test_id"])
    _add_student(headers, test["test_id"])
    _publish(headers, test["test_id"])
    token = _newest_outbox_token()
    return test["test_id"], token


# --- happy path ----------------------------------------------------------------


def test_full_happy_path_get_start_submit():
    headers = _headers()
    test_id, token = _setup_published_test_with_student(headers)

    info = client.get(f"/api/v1/take/{token}")
    assert info.status_code == 200, info.text
    info_body = info.json()
    assert info_body["session_status"] == "invited"
    assert info_body["ends_at"] is None
    assert info_body["question_count"] == 2
    assert info_body["test_title"] == "Algebra Basics"
    assert info_body["student_name"] == "Ada Lovelace"
    assert "server_now" in info_body

    start = client.post(f"/api/v1/take/{token}/start")
    assert start.status_code == 200, start.text
    start_body = start.json()
    assert len(start_body["questions"]) == 2
    for q in start_body["questions"]:
        assert "correct_index" not in q
        assert set(q.keys()) == {"question_id", "order", "stem", "options"}
    assert start_body["ends_at"] is not None

    # Both questions were authored with correct_index=1: answer the first
    # right, the second wrong, expect exactly 50%.
    question_ids = [q["question_id"] for q in start_body["questions"]]
    answers = {question_ids[0]: 1, question_ids[1]: 0}
    submit = client.post(f"/api/v1/take/{token}/submit", json={"answers": answers})
    assert submit.status_code == 200, submit.text
    assert submit.json() == {"status": "submitted"}

    rows = client.get(f"/api/v1/tests/{test_id}/students", headers=headers).json()
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["score"] == 50

    # Double submit must not overwrite the stored result.
    second_submit = client.post(f"/api/v1/take/{token}/submit", json={"answers": answers})
    assert second_submit.status_code == 409, second_submit.text

    info_again = client.get(f"/api/v1/take/{token}")
    assert info_again.status_code == 200
    assert info_again.json()["session_status"] == "completed"


def test_start_twice_is_idempotent_and_returns_the_same_ends_at():
    headers = _headers()
    _, token = _setup_published_test_with_student(headers)

    first = client.post(f"/api/v1/take/{token}/start")
    assert first.status_code == 200, first.text
    second = client.post(f"/api/v1/take/{token}/start")
    assert second.status_code == 200, second.text

    assert first.json()["ends_at"] == second.json()["ends_at"]
    assert [q["question_id"] for q in first.json()["questions"]] == [
        q["question_id"] for q in second.json()["questions"]
    ]


def test_submit_without_start_returns_410():
    headers = _headers()
    _, token = _setup_published_test_with_student(headers)

    resp = client.post(f"/api/v1/take/{token}/submit", json={"answers": {}})
    assert resp.status_code == 410, resp.text


# --- not-found / draft ----------------------------------------------------------


def test_bad_token_returns_404_on_every_endpoint():
    bogus = "this-token-does-not-exist"
    assert client.get(f"/api/v1/take/{bogus}").status_code == 404
    assert client.post(f"/api/v1/take/{bogus}/start").status_code == 404
    assert client.post(f"/api/v1/take/{bogus}/submit", json={"answers": {}}).status_code == 404


def test_draft_test_token_returns_404():
    headers = _headers()
    test = _create_test(headers)
    _put_two_questions(headers, test["test_id"])
    # The session (and its token) exists once a student is added, even
    # though a draft test has no deadline yet and sends no invitation.
    _add_student(headers, test["test_id"])

    from app.repositories import sessions_repo

    token = sessions_repo.list_sessions(test["test_id"])[0].link_token

    resp = client.get(f"/api/v1/take/{token}")
    assert resp.status_code == 404, resp.text


# --- deadline / time-up ---------------------------------------------------------


def test_deadline_passed_never_started_returns_410():
    headers = _headers()
    test_id, token = _setup_published_test_with_student(headers)
    _set_test_deadline(headers, test_id, datetime.now(UTC) - timedelta(days=1))

    info = client.get(f"/api/v1/take/{token}")
    assert info.status_code == 410, info.text

    start = client.post(f"/api/v1/take/{token}/start")
    assert start.status_code == 410, start.text


def test_time_up_after_grace_returns_410_and_never_stores_a_late_submission():
    headers = _headers()
    test_id, token = _setup_published_test_with_student(headers)

    started = client.post(f"/api/v1/take/{token}/start")
    assert started.status_code == 200, started.text

    # Push the session's stored ends_at well into the past (past the grace
    # window) without sleeping.
    _set_session_ends_at(token, datetime.now(UTC) - timedelta(minutes=5))

    submit = client.post(f"/api/v1/take/{token}/submit", json={"answers": {}})
    assert submit.status_code == 410, submit.text

    start_again = client.post(f"/api/v1/take/{token}/start")
    assert start_again.status_code == 410, start_again.text

    rows = client.get(f"/api/v1/tests/{test_id}/students", headers=headers).json()
    assert rows[0]["status"] == "started"
    assert rows[0]["score"] is None
