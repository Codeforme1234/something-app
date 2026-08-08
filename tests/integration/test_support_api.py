"""Integration tests for the Help & Support form. Backed by moto (see
tests/conftest.py); conftest.py creates the table.
"""

import uuid

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
    client.get("/api/v1/me", headers=headers)
    return headers


def _outbox() -> list[dict]:
    resp = client.get("/api/v1/dev/outbox")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_support_request_is_sent_to_the_support_inbox():
    headers = _headers()
    me = client.get("/api/v1/me", headers=headers).json()

    resp = client.post(
        "/api/v1/support",
        json={"category": "bug", "subject": "Button broken", "message": "It doesn't submit"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "sent"}

    messages = _outbox()
    assert len(messages) == 1
    assert messages[0]["subject"] == "[bug] Button broken"
    assert me["email"] in messages[0]["text"]
    assert "It doesn't submit" in messages[0]["text"]


def test_support_request_blank_subject_is_rejected():
    headers = _headers()
    resp = client.post(
        "/api/v1/support",
        json={"category": "other", "subject": "   ", "message": "hi"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert _outbox() == []


def test_support_request_invalid_category_is_rejected():
    headers = _headers()
    resp = client.post(
        "/api/v1/support",
        json={"category": "not-real", "subject": "s", "message": "m"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
