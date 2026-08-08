"""Integration tests (moto-backed; see tests/conftest.py) for the
multi-tenant credit system: /me provisions a company, test creation spends one credit from it,
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


# --- AI credits ---------------------------------------------------------------
#
# A second pool, metered separately because an AI run costs real money per call
# while a test-creation credit is only about how many tests a company may have.


def _generate(headers: dict, **overrides) -> "object":
    payload = {"topic": "Photosynthesis"}
    payload.update(overrides)
    return client.post("/api/v1/tests/generate", json=payload, headers=headers)


def _drain_ai_credits(headers: dict, to: int) -> None:
    """Set the company's AI balance directly -- there is no API for granting."""
    from app.models.teacher import Teacher
    from app.repositories import companies_repo, keys, store

    sub = headers["Authorization"].removeprefix("Bearer ")
    teacher = store.get(keys.teacher_pk(sub), keys.PROFILE_SK, Teacher)
    stored = companies_repo.get_company(teacher.model.company_id)
    companies_repo.update_company(
        stored.model.model_copy(update={"ai_credit_balance": to}), stored.version
    )


def test_me_reports_the_ai_balance_and_the_price_list():
    headers = _headers()
    body = _me(headers)

    assert body["ai_credit_balance"] == 20  # Settings.starting_ai_credits default
    assert body["ai_credit_cost"] == {"prompt": 1, "pdf": 2}


def test_a_prompt_only_run_spends_one_of_each_pool():
    headers = _headers()
    before = _me(headers)

    assert _generate(headers).status_code == 202

    after = _me(headers)
    assert after["credit_balance"] == before["credit_balance"] - 1
    assert after["ai_credit_balance"] == before["ai_credit_balance"] - 1


def test_a_document_grounded_run_spends_two_ai_credits():
    headers = _headers()
    before = _me(headers)

    resp = _generate(headers, knowledge_base="Chloroplasts convert light to sugar.")
    assert resp.status_code == 202, resp.text

    after = _me(headers)
    assert after["credit_balance"] == before["credit_balance"] - 1  # still exactly one
    assert after["ai_credit_balance"] == before["ai_credit_balance"] - 2


def test_zero_ai_credits_refuses_with_its_own_code():
    headers = _headers()
    _me(headers)
    _drain_ai_credits(headers, 0)

    resp = _generate(headers)

    assert resp.status_code == 402
    # A distinct code so the UI can name the right pool rather than
    # string-matching a message.
    assert resp.json()["code"] == "insufficient_ai_credits"


def test_one_ai_credit_allows_a_prompt_run_but_not_a_document_run():
    """The guard is about the mode's price, not a blanket minimum."""
    headers = _headers()
    _me(headers)
    _drain_ai_credits(headers, 1)

    refused = _generate(headers, knowledge_base="source text")
    assert refused.status_code == 402
    assert refused.json()["code"] == "insufficient_ai_credits"

    assert _generate(headers).status_code == 202


def test_a_refused_run_creates_no_test_and_spends_nothing():
    headers = _headers()
    _me(headers)
    _drain_ai_credits(headers, 0)
    before = _me(headers)

    assert _generate(headers).status_code == 402

    after = _me(headers)
    assert after["credit_balance"] == before["credit_balance"]
    assert client.get("/api/v1/tests", headers=headers).json() == []
