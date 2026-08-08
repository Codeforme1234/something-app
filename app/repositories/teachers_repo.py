from app.core.clock import now
from app.core.config import get_settings
from app.core.exceptions import ConflictError
from app.core.ids import new_ulid
from app.models.company import Company
from app.models.teacher import Teacher
from app.repositories import companies_repo, keys, store

ENTITY = "TEACHER"


def get_teacher(sub: str) -> Teacher | None:
    stored = store.get(keys.teacher_pk(sub), keys.PROFILE_SK, Teacher)
    return stored.model if stored else None


def upsert_teacher(sub: str, email: str, name: str) -> Teacher:
    """Idempotent profile write from JWT claims, preserving created_at.

    Every admin belongs to a company that holds their credit balance. A
    brand-new admin gets a brand-new company provisioned with the starting
    grant right here. An admin who already exists but predates multi-tenancy
    (company_id is None) gets one backfilled on this call too, so no
    migration script is needed -- every admin ends up with a company the
    next time they load the app.
    """
    existing = get_teacher(sub)
    company_id = existing.company_id if existing else None
    if not company_id:
        company_id = _provision_company(name).company_id
    else:
        _backfill_ai_credits(company_id)

    teacher = Teacher(
        sub=sub,
        email=email,
        # Once onboarded, the stored name is the one the teacher typed and this
        # call must leave it alone. Refreshing it from the JWT on every /me --
        # which is what this used to do unconditionally -- would silently
        # overwrite their choice with the IdP's value on the very next page load.
        name=existing.name if existing and existing.onboarded else name,
        company_id=company_id,
        created_at=existing.created_at if existing else now(),
        onboarded=existing.onboarded if existing else False,
    )
    store.put_overwrite(keys.teacher_pk(sub), keys.PROFILE_SK, ENTITY, teacher)
    return teacher


def complete_onboarding(sub: str, name: str, company_name: str) -> Teacher:
    """Record the name and company the teacher chose on first login.

    Marks the teacher onboarded, which is what stops upsert_teacher reverting
    `name` to the JWT claim on the next request.
    """
    existing = get_teacher(sub)
    if existing is None:
        raise ConflictError("no profile to onboard; load /me first")

    teacher = existing.model_copy(update={"name": name, "onboarded": True})
    store.put_overwrite(keys.teacher_pk(sub), keys.PROFILE_SK, ENTITY, teacher)

    if teacher.company_id:
        company_stored = companies_repo.get_company(teacher.company_id)
        if company_stored is not None:
            companies_repo.update_company(
                company_stored.model.model_copy(update={"name": company_name}),
                company_stored.version,
            )
    return teacher


def _provision_company(admin_name: str) -> Company:
    settings = get_settings()
    company = Company(
        company_id=new_ulid(),
        name=f"{admin_name}'s company",
        credit_balance=settings.starting_credits,
        ai_credit_balance=settings.starting_ai_credits,
        created_at=now(),
    )
    companies_repo.create_company(company)
    return company


def _backfill_ai_credits(company_id: str) -> None:
    """One-time AI-credit grant for a company provisioned before AI runs
    existed. Idempotent by the None sentinel: a company that already has a
    balance -- including 0 -- is left alone, so this never refills a drained
    pool. A concurrent /me that already granted wins the version race, and
    losing it is a no-op, so the ConflictError is swallowed: a duplicate login
    must never surface an error to the teacher.
    """
    stored = companies_repo.get_company(company_id)
    if stored is None or stored.model.ai_credit_balance is not None:
        return
    granted = stored.model.model_copy(
        update={"ai_credit_balance": get_settings().starting_ai_credits}
    )
    try:
        companies_repo.update_company(granted, stored.version)
    except ConflictError:
        pass
