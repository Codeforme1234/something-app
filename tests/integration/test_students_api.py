"""Integration tests against DynamoDB Local for the students/publish/invite
flow. Run `docker compose up -d` first (see CLAUDE.md); conftest.py creates
the throwaway table.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_outbox(monkeypatch, tmp_path):
    """Point the dev outbox at a throwaway directory so these tests never
    write into (or read stale files from) the real .dev/outbox a developer
    might be looking at."""
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


def _add_one_question(headers: dict, test_id: str) -> None:
    payload = {
        "questions": [
            {"stem": "2 + 2?", "options": ["3", "4", "5", "6"], "correct_index": 1},
        ]
    }
    resp = client.put(f"/api/v1/tests/{test_id}/questions", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text


def _students_payload(*pairs: tuple[str, str]) -> dict:
    return {"students": [{"name": n, "email": e} for n, e in pairs]}


def _future_deadline() -> str:
    return (datetime.now(UTC) + timedelta(days=7)).isoformat()


def _outbox() -> list[dict]:
    resp = client.get("/api/v1/dev/outbox")
    assert resp.status_code == 200
    return resp.json()


def test_add_students_and_list_them():
    headers = _headers()
    test = _create_test(headers)

    resp = client.post(
        f"/api/v1/tests/{test['test_id']}/students",
        json=_students_payload(("Ada Lovelace", "ada@example.com"), ("Bob Smith", "bob@example.com")),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["added"]) == 2
    assert body["skipped_emails"] == []
    for row in body["added"]:
        assert "link_token" not in row
        assert row["status"] == "invited"

    list_resp = client.get(f"/api/v1/tests/{test['test_id']}/students", headers=headers)
    assert list_resp.status_code == 200
    rows = list_resp.json()
    assert {r["student_email"] for r in rows} == {"ada@example.com", "bob@example.com"}
    for row in rows:
        assert "link_token" not in row
        assert row["score"] is None

    # A draft test has no deadline yet, so nothing should have been mailed.
    assert _outbox() == []


def test_duplicate_email_is_skipped_case_insensitively():
    headers = _headers()
    test = _create_test(headers)

    client.post(
        f"/api/v1/tests/{test['test_id']}/students",
        json=_students_payload(("Ada Lovelace", "ada@example.com")),
        headers=headers,
    )
    resp = client.post(
        f"/api/v1/tests/{test['test_id']}/students",
        json=_students_payload(("Ada Again", "ADA@EXAMPLE.COM"), ("Bob Smith", "bob@example.com")),
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    # EmailStr lowercases the domain (domains are case-insensitive) but keeps
    # the local part as typed -- the dedupe check still catches it because it
    # compares case-insensitively itself.
    assert body["skipped_emails"] == ["ADA@example.com"]
    assert [row["student_email"] for row in body["added"]] == ["bob@example.com"]


def test_publish_sends_invitations_to_outbox_with_student_links():
    headers = _headers()
    test = _create_test(headers)
    _add_one_question(headers, test["test_id"])
    client.post(
        f"/api/v1/tests/{test['test_id']}/students",
        json=_students_payload(("Ada Lovelace", "ada@example.com"), ("Bob Smith", "bob@example.com")),
        headers=headers,
    )

    resp = client.post(
        f"/api/v1/tests/{test['test_id']}/publish",
        json={"deadline": _future_deadline()},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["status"] == "published"
    assert summary["deadline"] is not None

    messages = _outbox()
    assert len(messages) == 2
    recipients = {m["to"] for m in messages}
    assert recipients == {"ada@example.com", "bob@example.com"}
    for message in messages:
        assert message["student_link"] is not None
        assert "/t/" in message["student_link"]
        assert message["subject"]
        assert message["html"]
        assert message["text"]
        assert message["sent_at"]

    # GET /students still reflects the roster (still "invited" -- Phase 3
    # flips this to "started"/"completed").
    rows = client.get(f"/api/v1/tests/{test['test_id']}/students", headers=headers).json()
    assert {r["status"] for r in rows} == {"invited"}


def test_add_students_after_publish_sends_invitation_immediately():
    headers = _headers()
    test = _create_test(headers)
    _add_one_question(headers, test["test_id"])
    client.post(
        f"/api/v1/tests/{test['test_id']}/publish",
        json={"deadline": _future_deadline()},
        headers=headers,
    )
    assert _outbox() == []  # no students yet

    resp = client.post(
        f"/api/v1/tests/{test['test_id']}/students",
        json=_students_payload(("Late Student", "late@example.com")),
        headers=headers,
    )
    assert resp.status_code == 201

    messages = _outbox()
    assert len(messages) == 1
    assert messages[0]["to"] == "late@example.com"


def test_publish_twice_returns_conflict():
    headers = _headers()
    test = _create_test(headers)
    _add_one_question(headers, test["test_id"])

    first = client.post(
        f"/api/v1/tests/{test['test_id']}/publish",
        json={"deadline": _future_deadline()},
        headers=headers,
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/v1/tests/{test['test_id']}/publish",
        json={"deadline": _future_deadline()},
        headers=headers,
    )
    assert second.status_code == 409


def test_publish_without_questions_fails():
    headers = _headers()
    test = _create_test(headers)

    resp = client.post(
        f"/api/v1/tests/{test['test_id']}/publish",
        json={"deadline": _future_deadline()},
        headers=headers,
    )
    assert resp.status_code == 409


def test_publish_with_past_deadline_fails():
    headers = _headers()
    test = _create_test(headers)
    _add_one_question(headers, test["test_id"])

    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    resp = client.post(
        f"/api/v1/tests/{test['test_id']}/publish", json={"deadline": past}, headers=headers
    )
    assert resp.status_code == 400


def test_teacher_b_gets_404_on_teacher_a_students_endpoints():
    alice = _headers()
    bob = _headers()
    test = _create_test(alice)
    _add_one_question(alice, test["test_id"])

    assert (
        client.post(
            f"/api/v1/tests/{test['test_id']}/students",
            json=_students_payload(("Eve", "eve@example.com")),
            headers=bob,
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/tests/{test['test_id']}/students", headers=bob).status_code == 404
    assert (
        client.post(
            f"/api/v1/tests/{test['test_id']}/publish",
            json={"deadline": _future_deadline()},
            headers=bob,
        ).status_code
        == 404
    )


def test_student_count_is_kept_in_sync_on_the_test_meta():
    headers = _headers()
    test = _create_test(headers)
    assert test["student_count"] == 0

    client.post(
        f"/api/v1/tests/{test['test_id']}/students",
        json=_students_payload(("Ada", "ada@example.com"), ("Bob", "bob@example.com")),
        headers=headers,
    )

    detail = client.get(f"/api/v1/tests/{test['test_id']}", headers=headers).json()
    assert detail["student_count"] == 2
