from app.core.clock import now
from app.core.config import get_settings
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

    teacher = Teacher(
        sub=sub,
        email=email,
        name=name,
        company_id=company_id,
        created_at=existing.created_at if existing else now(),
    )
    store.put_overwrite(keys.teacher_pk(sub), keys.PROFILE_SK, ENTITY, teacher)
    return teacher


def _provision_company(admin_name: str) -> Company:
    company = Company(
        company_id=new_ulid(),
        name=f"{admin_name}'s company",
        credit_balance=get_settings().starting_credits,
        created_at=now(),
    )
    companies_repo.create_company(company)
    return company
