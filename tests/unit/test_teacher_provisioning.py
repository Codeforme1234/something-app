"""Unit tests for the company-provisioning logic in teachers_repo.upsert_teacher.
The repository layer is monkeypatched so these run without DynamoDB -- the
real round trip (a fresh /me call actually creating a Company item, a second
admin getting an isolated one) is covered by tests/integration/test_credits_api.py."""

from app.core.clock import now
from app.models.company import Company
from app.models.teacher import Teacher
from app.core.exceptions import ConflictError
from app.repositories import companies_repo, store, teachers_repo


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
    # A returning admin now also runs the AI-credit backfill, which reads the
    # company. Stub it, or this "no DynamoDB" unit test quietly starts needing
    # a reachable table.
    monkeypatch.setattr(companies_repo, "get_company", lambda cid: None)

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


# --- AI-credit backfill ------------------------------------------------------
#
# The None sentinel on Company.ai_credit_balance is what makes this safe to run
# on every /me: None means "never granted", 0 means "granted and spent".


def _stub_returning_admin(monkeypatch, company: Company):
    """A returning admin whose company is `company`, with the write paths stubbed
    so only the backfill's own behaviour is under test."""
    existing = Teacher(
        sub="dev-alice",
        email="a@x.com",
        name="Alice",
        company_id=company.company_id,
        created_at=now(),
    )
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: existing)
    monkeypatch.setattr("app.repositories.store.put_overwrite", lambda *a, **k: None)
    monkeypatch.setattr(companies_repo, "get_company", lambda cid: store.Stored(company, 3))
    written: list[tuple[Company, int]] = []
    monkeypatch.setattr(
        companies_repo, "update_company", lambda c, v: written.append((c, v)) or 4
    )
    return written


def _company(ai_credit_balance: int | None) -> Company:
    return Company(
        company_id="COMP1",
        name="Alice's company",
        credit_balance=20,
        ai_credit_balance=ai_credit_balance,
        created_at=now(),
    )


def test_new_company_is_provisioned_with_ai_credits(monkeypatch):
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: None)
    created: list[Company] = []
    monkeypatch.setattr(companies_repo, "create_company", lambda c: created.append(c))
    monkeypatch.setattr("app.repositories.store.put_overwrite", lambda *a, **k: None)

    teachers_repo.upsert_teacher("dev-new", "new@x.com", "Newbie")

    assert created[0].ai_credit_balance == 20  # Settings.starting_ai_credits default


def test_legacy_company_without_ai_credits_is_backfilled(monkeypatch):
    written = _stub_returning_admin(monkeypatch, _company(None))

    teachers_repo.upsert_teacher("dev-alice", "a@x.com", "Alice")

    assert len(written) == 1
    company, expected_version = written[0]
    assert company.ai_credit_balance == 20
    assert expected_version == 3  # optimistic write against the version it read
    assert company.credit_balance == 20  # the other balance is untouched


def test_company_with_a_drained_ai_balance_is_not_refilled(monkeypatch):
    """The whole point of the None sentinel: 0 must not look like "never granted",
    or every /me would hand a teacher their credits back."""
    written = _stub_returning_admin(monkeypatch, _company(0))

    teachers_repo.upsert_teacher("dev-alice", "a@x.com", "Alice")

    assert written == []


def test_company_that_already_has_ai_credits_is_left_alone(monkeypatch):
    written = _stub_returning_admin(monkeypatch, _company(7))

    teachers_repo.upsert_teacher("dev-alice", "a@x.com", "Alice")

    assert written == []


def test_backfill_swallows_a_lost_version_race(monkeypatch):
    """Two concurrent /me calls: the loser must not surface a 409 on login."""
    _stub_returning_admin(monkeypatch, _company(None))

    def _conflict(*_args, **_kwargs):
        raise ConflictError("item was modified concurrently")

    monkeypatch.setattr(companies_repo, "update_company", _conflict)

    teacher = teachers_repo.upsert_teacher("dev-alice", "a@x.com", "Alice")

    assert teacher.company_id == "COMP1"


def test_backfill_is_skipped_when_the_company_row_is_missing(monkeypatch):
    monkeypatch.setattr(companies_repo, "get_company", lambda cid: None)
    existing = Teacher(
        sub="dev-alice", email="a@x.com", name="Alice", company_id="GONE", created_at=now()
    )
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: existing)
    monkeypatch.setattr("app.repositories.store.put_overwrite", lambda *a, **k: None)

    def _must_not_write(*_args, **_kwargs):
        raise AssertionError("must not write when the company row is absent")

    monkeypatch.setattr(companies_repo, "update_company", _must_not_write)

    teachers_repo.upsert_teacher("dev-alice", "a@x.com", "Alice")
