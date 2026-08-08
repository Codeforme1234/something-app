"""Unit tests for the path-aware body-size middleware in app/main.py.
No auth or routing needs to succeed here -- these only check which ceiling
the middleware applied, via the presence or absence of a 413 before the
request ever reaches a route handler.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

_TWO_MB = b"x" * 2_000_000


def test_a_2mb_post_to_an_upload_path_is_not_rejected_by_the_body_limit():
    # No such route exists yet (routes land in a later task) -- the only
    # thing under test is that the middleware let the request through to
    # the router instead of short-circuiting with 413.
    response = client.post(
        "/api/v1/tests/t1/questions/q1/question-images",
        content=_TWO_MB,
        headers={"content-type": "application/octet-stream"},
    )
    assert response.status_code != 413


def test_a_2mb_post_to_a_json_route_still_returns_413():
    response = client.post(
        "/api/v1/tests",
        content=_TWO_MB,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"
