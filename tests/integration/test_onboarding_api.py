"""Integration tests (moto-backed; see tests/conftest.py) for first-login
onboarding: a new teacher names themselves and their company instead of being
given values derived from their identity provider.
"""

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


def _onboard(headers: dict, name: str, company: str):
    return client.post(
        "/api/v1/me/onboarding",
        json={"name": name, "company_name": company},
        headers=headers,
    )


def test_a_brand_new_teacher_needs_onboarding():
    assert _me(_headers())["needs_onboarding"] is True


def test_onboarding_sets_the_name_and_company_and_clears_the_flag():
    headers = _headers()
    _me(headers)

    resp = _onboard(headers, "Devesh Tiwari", "Mindtickle")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["name"] == "Devesh Tiwari"
    assert body["company_name"] == "Mindtickle"
    assert body["needs_onboarding"] is False


def test_a_later_me_call_does_not_clobber_the_chosen_name():
    """The regression this feature is most likely to grow: upsert_teacher used
    to rewrite `name` from the JWT on every single /me."""
    headers = _headers()
    _me(headers)
    _onboard(headers, "Devesh Tiwari", "Mindtickle")

    again = _me(headers)

    assert again["name"] == "Devesh Tiwari"
    assert again["company_name"] == "Mindtickle"
    assert again["needs_onboarding"] is False


def test_onboarding_is_idempotent_and_acts_as_a_rename():
    headers = _headers()
    _me(headers)
    _onboard(headers, "First Name", "First Co")

    resp = _onboard(headers, "Second Name", "Second Co")

    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Second Name"
    assert resp.json()["company_name"] == "Second Co"


def test_onboarding_does_not_disturb_the_credit_balances():
    """Renaming the company writes the same item the balances live on, so a
    careless copy here would reset them to the starting grant."""
    headers = _headers()
    before = _me(headers)
    client.post("/api/v1/tests", json={"title": "T"}, headers=headers)
    spent = _me(headers)
    assert spent["credit_balance"] == before["credit_balance"] - 1

    _onboard(headers, "Devesh", "Mindtickle")

    after = _me(headers)
    assert after["credit_balance"] == spent["credit_balance"]
    assert after["ai_credit_balance"] == spent["ai_credit_balance"]


def test_onboarding_works_before_the_first_me_call():
    """The frontend always calls /me first, but the endpoint must not depend on
    that ordering -- it provisions the profile itself if needed."""
    headers = _headers()

    resp = _onboard(headers, "Devesh", "Mindtickle")

    assert resp.status_code == 200, resp.text
    assert resp.json()["company_name"] == "Mindtickle"


def test_blank_values_are_rejected():
    headers = _headers()
    _me(headers)

    assert _onboard(headers, "   ", "Mindtickle").status_code == 422
    assert _onboard(headers, "Devesh", "   ").status_code == 422


def test_onboarding_requires_authentication():
    resp = client.post(
        "/api/v1/me/onboarding", json={"name": "X", "company_name": "Y"}
    )
    assert resp.status_code == 401


def test_two_teachers_onboard_independently():
    alice, bob = _headers(), _headers()
    _me(alice)
    _me(bob)

    _onboard(alice, "Alice", "Alice Co")

    assert _me(bob)["needs_onboarding"] is True
    assert _me(bob)["company_name"] != "Alice Co"
