"""Integration tests against DynamoDB Local for the multi-tenant credit
system: /me provisions a company, test creation spends one credit from it,
and two different admins never share a balance."""

import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _headers() -> dict:
    return {"Authorization": f"Bearer dev-{uuid.uuid4().hex[:12]}"}


def _me(headers: dict) -> dict:
    resp = client.get("/api/v1/me", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _create_test(headers: dict) -> dict:
    return client.post(
        "/api/v1/tests",
        json={"title": "Sample", "difficulty": "medium", "duration_seconds": 600},
        headers=headers,
    )


def test_first_me_call_provisions_a_company_with_starting_credits():
    me = _me(_headers())
    assert me["credit_balance"] == 20
    assert me["company_name"].endswith("'s company")


def test_repeated_me_calls_keep_the_same_balance():
    headers = _headers()
    first = _me(headers)
    second = _me(headers)
    assert first["company_name"] == second["company_name"]
    assert first["credit_balance"] == second["credit_balance"]


def test_creating_a_test_spends_exactly_one_credit():
    headers = _headers()
    _me(headers)

    resp = _create_test(headers)
    assert resp.status_code == 201, resp.text

    assert _me(headers)["credit_balance"] == 19


def test_two_admins_have_isolated_companies_and_balances():
    alice, bob = _headers(), _headers()
    _me(alice)
    _me(bob)

    assert _create_test(alice).status_code == 201
    assert _create_test(alice).status_code == 201

    # Bob's balance is untouched by Alice spending two of her own credits.
    assert _me(alice)["credit_balance"] == 18
    assert _me(bob)["credit_balance"] == 20
    assert _me(alice)["company_name"] != _me(bob)["company_name"]


def test_running_out_of_credits_blocks_test_creation_with_402():
    headers = _headers()
    _me(headers)

    for _ in range(20):
        assert _create_test(headers).status_code == 201

    assert _me(headers)["credit_balance"] == 0

    resp = _create_test(headers)
    assert resp.status_code == 402
    assert resp.json()["code"] == "insufficient_credits"

    # The failed attempt spent nothing further.
    assert _me(headers)["credit_balance"] == 0
