"""Integration tests against DynamoDB Local, run with LLM_MODE=fake (the
process-wide default -- see .env / CLAUDE.md). These never call the real
OpenAI API; FakeMCQGenerator is deterministic and needs no key.
"""

import uuid

import httpx
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


def _generate(headers: dict, test_id: str, **overrides) -> httpx.Response:
    payload = {"topic": "photosynthesis", "count": 5, "difficulty": "medium"}
    payload.update(overrides)
    return client.post(f"/api/v1/tests/{test_id}/generate-questions", json=payload, headers=headers)


def test_generate_questions_returns_valid_draft_questions():
    headers = _headers()
    test_id = _create_test(headers)["test_id"]

    resp = _generate(headers, test_id, topic="photosynthesis", count=5, difficulty="medium")
    assert resp.status_code == 200, resp.text

    questions = resp.json()["questions"]
    assert len(questions) == 5
    stems = set()
    for q in questions:
        assert set(q.keys()) == {"stem", "options", "correct_index"}
        assert len(q["options"]) == 4
        assert len(set(q["options"])) == 4
        assert 0 <= q["correct_index"] <= 3
        assert "photosynthesis" in q["stem"]
        stems.add(q["stem"])
    assert len(stems) == 5  # no duplicate stems


def test_generate_questions_persists_nothing():
    headers = _headers()
    test_id = _create_test(headers)["test_id"]

    resp = _generate(headers, test_id, count=5)
    assert resp.status_code == 200, resp.text

    detail = client.get(f"/api/v1/tests/{test_id}", headers=headers).json()
    assert detail["question_count"] == 0
    assert detail["questions"] == []


def test_generate_questions_count_out_of_range_rejected():
    headers = _headers()
    test_id = _create_test(headers)["test_id"]

    resp = _generate(headers, test_id, count=25)
    assert resp.status_code == 422, resp.text


def test_generate_questions_on_published_test_is_conflict():
    headers = _headers()
    test_id = _create_test(headers)["test_id"]

    from app.models.test import TestStatus
    from app.repositories import tests_repo

    sub = headers["Authorization"].removeprefix("Bearer ")
    stored = tests_repo.get_test(sub, test_id)
    published = stored.model.model_copy(update={"status": TestStatus.published})
    tests_repo.update_test(sub, published, stored.version)

    resp = _generate(headers, test_id)
    assert resp.status_code == 409, resp.text


def test_generate_questions_for_foreign_teacher_is_not_found():
    alice = _headers()
    bob = _headers()
    test_id = _create_test(alice)["test_id"]

    resp = _generate(bob, test_id)
    assert resp.status_code == 404, resp.text


def test_generate_questions_for_missing_test_is_not_found():
    headers = _headers()
    resp = _generate(headers, "does-not-exist")
    assert resp.status_code == 404, resp.text
