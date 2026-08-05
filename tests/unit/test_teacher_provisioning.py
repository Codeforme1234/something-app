"""Unit tests for the company-provisioning logic in teachers_repo.upsert_teacher.
The repository layer is monkeypatched so these run without DynamoDB -- the
real round trip (a fresh /me call actually creating a Company item, a second
admin getting an isolated one) is covered by tests/integration/test_credits_api.py."""

from app.core.clock import now
from app.models.company import Company
from app.models.teacher import Teacher
from app.repositories import companies_repo, teachers_repo


def test_first_login_provisions_a_new_company(monkeypatch):
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: None)
    created: list[Company] = []
    monkeypatch.setattr(companies_repo, "create_company", lambda c: created.append(c))
    monkeypatch.setattr("app.repositories.store.put_overwrite", lambda *a, **k: None)

    teacher = teachers_repo.upsert_teacher("dev-new", "new@x.com", "Newbie")

    assert len(created) == 1
    assert teacher.company_id == created[0].company_id
    assert created[0].credit_balance == 20  # Settings.starting_credits default
    assert created[0].name == "Newbie's company"


def test_returning_admin_with_a_company_keeps_it_and_creates_no_new_one(monkeypatch):
    existing = Teacher(
        sub="dev-alice", email="old@x.com", name="Old Name", company_id="COMP1", created_at=now()
    )
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: existing)
    created: list[Company] = []
    monkeypatch.setattr(companies_repo, "create_company", lambda c: created.append(c))
    monkeypatch.setattr("app.repositories.store.put_overwrite", lambda *a, **k: None)

    teacher = teachers_repo.upsert_teacher("dev-alice", "new@x.com", "New Name")

    assert created == []  # no duplicate company created on a repeat login
    assert teacher.company_id == "COMP1"
    assert teacher.created_at == existing.created_at  # preserved, not refreshed
    assert teacher.email == "new@x.com"  # claims still refresh


def test_legacy_admin_without_company_id_gets_one_backfilled(monkeypatch):
    """An admin created before multi-tenancy existed has company_id=None on
    their stored record. No migration script runs -- the next /me call fixes
    it in place."""
    legacy = Teacher(sub="dev-legacy", email="l@x.com", name="Legacy", company_id=None, created_at=now())
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: legacy)
    created: list[Company] = []
    monkeypatch.setattr(companies_repo, "create_company", lambda c: created.append(c))
    monkeypatch.setattr("app.repositories.store.put_overwrite", lambda *a, **k: None)

    teacher = teachers_repo.upsert_teacher("dev-legacy", "l@x.com", "Legacy")

    assert len(created) == 1
    assert teacher.company_id == created[0].company_id
    assert teacher.created_at == legacy.created_at
