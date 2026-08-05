"""Integration tests against DynamoDB Local. Run `docker compose up -d`
first (see CLAUDE.md); tests/integration/conftest.py creates the table.

Each test uses a freshly-generated teacher token so tests don't see each
other's data even though they share one on-disk table across runs.
"""

import time
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


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


def _questions_payload(n: int) -> dict:
    return {
        "questions": [
            {
                "stem": f"Question {i}?",
                "options": [f"opt{i}-a", f"opt{i}-b", f"opt{i}-c", f"opt{i}-d"],
                "correct_index": 0,
            }
            for i in range(n)
        ]
    }


def test_create_put_questions_get_list_delete_roundtrip():
    headers = _headers()
    created = _create_test(headers)
    test_id = created["test_id"]
    assert created["status"] == "draft"
    assert created["question_count"] == 0
    assert created["student_count"] == 0

    put_resp = client.put(
        f"/api/v1/tests/{test_id}/questions", json=_questions_payload(3), headers=headers
    )
    assert put_resp.status_code == 200, put_resp.text
    detail = put_resp.json()
    assert detail["question_count"] == 3
    assert [q["order"] for q in detail["questions"]] == [1, 2, 3]

    get_resp = client.get(f"/api/v1/tests/{test_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["question_count"] == 3
    assert len(get_resp.json()["questions"]) == 3

    list_resp = client.get("/api/v1/tests", headers=headers)
    assert list_resp.status_code == 200
    listed = list_resp.json()
    assert len(listed) == 1
    assert listed[0]["test_id"] == test_id

    delete_resp = client.delete(f"/api/v1/tests/{test_id}", headers=headers)
    assert delete_resp.status_code == 204

    missing = client.get(f"/api/v1/tests/{test_id}", headers=headers)
    assert missing.status_code == 404

    empty_list = client.get("/api/v1/tests", headers=headers)
    assert empty_list.json() == []


def test_list_is_newest_first():
    headers = _headers()
    first = _create_test(headers, title="First")
    time.sleep(0.01)  # ensure a distinct ULID millisecond
    second = _create_test(headers, title="Second")

    resp = client.get("/api/v1/tests", headers=headers)
    ids = [t["test_id"] for t in resp.json()]
    assert ids == [second["test_id"], first["test_id"]]


def test_teacher_b_cannot_read_teacher_a_test():
    alice = _headers()
    bob = _headers()
    created = _create_test(alice)
    test_id = created["test_id"]

    resp = client.get(f"/api/v1/tests/{test_id}", headers=bob)
    assert resp.status_code == 404

    # Bob's own list must not include Alice's test either.
    bob_list = client.get("/api/v1/tests", headers=bob)
    assert bob_list.json() == []


def test_put_questions_renumbers_order_from_one_on_replace():
    headers = _headers()
    created = _create_test(headers)
    test_id = created["test_id"]

    client.put(f"/api/v1/tests/{test_id}/questions", json=_questions_payload(5), headers=headers)
    replaced = client.put(
        f"/api/v1/tests/{test_id}/questions", json=_questions_payload(2), headers=headers
    )
    assert replaced.status_code == 200
    detail = replaced.json()
    assert detail["question_count"] == 2
    assert [q["order"] for q in detail["questions"]] == [1, 2]

    # The leftover Q#003..Q#005 keys from the first PUT must be gone, not just
    # unreferenced -- re-fetching the test proves it.
    get_resp = client.get(f"/api/v1/tests/{test_id}", headers=headers)
    assert len(get_resp.json()["questions"]) == 2


def test_draft_only_mutation_rules():
    headers = _headers()
    created = _create_test(headers)
    test_id = created["test_id"]
    client.put(f"/api/v1/tests/{test_id}/questions", json=_questions_payload(4), headers=headers)

    # No publish endpoint exists yet (later phase); flip status directly via
    # the repository to exercise the draft-only guard.
    from app.models.test import TestStatus
    from app.repositories import tests_repo

    sub = headers["Authorization"].removeprefix("Bearer ")
    stored = tests_repo.get_test(sub, test_id)
    published = stored.model.model_copy(update={"status": TestStatus.published})
    tests_repo.update_test(sub, published, stored.version)

    assert client.patch(f"/api/v1/tests/{test_id}", json={"title": "New title"}, headers=headers).status_code == 409
    assert (
        client.put(
            f"/api/v1/tests/{test_id}/questions", json=_questions_payload(1), headers=headers
        ).status_code
        == 409
    )
    assert client.delete(f"/api/v1/tests/{test_id}", headers=headers).status_code == 409
