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


def test_generate_questions_with_knowledge_base_reaches_the_generator():
    headers = _headers()
    test_id = _create_test(headers)["test_id"]

    resp = _generate(
        headers,
        test_id,
        count=2,
        knowledge_base="Mitochondria are the powerhouse of the cell.",
    )
    assert resp.status_code == 200, resp.text
    for q in resp.json()["questions"]:
        assert "uploaded material" in q["stem"]  # FakeMCQGenerator's tell


def test_generate_questions_knowledge_base_too_long_is_rejected():
    headers = _headers()
    test_id = _create_test(headers)["test_id"]

    resp = _generate(headers, test_id, knowledge_base="x" * 20_001)
    assert resp.status_code == 422, resp.text


# --- POST /tests/generate: the "Generate with AI" workflow -----------------


def _generate_test(headers: dict, **overrides) -> httpx.Response:
    payload = {"topic": "Photosynthesis", "count": 3, "difficulty": "medium"}
    payload.update(overrides)
    return client.post("/api/v1/tests/generate", json=payload, headers=headers)


def test_generate_test_creates_a_draft_with_ai_authored_questions():
    headers = _headers()
    before = client.get("/api/v1/me", headers=headers).json()["credit_balance"]

    resp = _generate_test(headers, topic="Photosynthesis", count=3, difficulty="medium")
    assert resp.status_code == 201, resp.text
    test = resp.json()

    assert test["title"] == "Photosynthesis"
    assert test["status"] == "draft"
    assert test["question_count"] == 3
    assert len(test["questions"]) == 3
    for q in test["questions"]:
        assert "photosynthesis" in q["stem"].lower()
        assert "correct_index" in q  # teacher-facing detail, unlike the student view

    after = client.get("/api/v1/me", headers=headers).json()["credit_balance"]
    assert after == before - 1

    # It's a normal draft afterwards -- reachable through the ordinary get/list.
    get_resp = client.get(f"/api/v1/tests/{test['test_id']}", headers=headers)
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["question_count"] == 3


def test_generate_test_with_knowledge_base_reaches_the_generator():
    headers = _headers()
    resp = _generate_test(
        headers, count=1, knowledge_base="Mitochondria are the powerhouse of the cell."
    )
    assert resp.status_code == 201, resp.text
    assert "uploaded material" in resp.json()["questions"][0]["stem"]


def test_generate_test_with_zero_credits_is_rejected_and_creates_nothing():
    headers = _headers()
    for _ in range(20):
        assert client.post("/api/v1/tests", json={}, headers=headers).status_code == 201
    assert client.get("/api/v1/me", headers=headers).json()["credit_balance"] == 0

    resp = _generate_test(headers)
    assert resp.status_code == 402, resp.text

    # Nothing new was created, and the balance stayed at zero (not negative).
    assert client.get("/api/v1/me", headers=headers).json()["credit_balance"] == 0
    tests_after = client.get("/api/v1/tests", headers=headers).json()
    assert all(t["title"] != "Photosynthesis" for t in tests_after)


def test_generate_test_topic_over_200_chars_is_truncated_for_the_title():
    headers = _headers()
    long_topic = "Ancient Roman Aqueducts and Their Engineering " * 5  # well over 200 chars
    assert len(long_topic) > 200

    resp = _generate_test(headers, topic=long_topic, count=1)
    assert resp.status_code == 201, resp.text
    assert resp.json()["title"] == long_topic[:200]
