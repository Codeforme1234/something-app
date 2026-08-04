from app.core.clock import now
from app.models.teacher import Teacher
from app.repositories import keys, store

ENTITY = "TEACHER"


def get_teacher(sub: str) -> Teacher | None:
    stored = store.get(keys.teacher_pk(sub), keys.PROFILE_SK, Teacher)
    return stored.model if stored else None


def upsert_teacher(sub: str, email: str, name: str) -> Teacher:
    """Idempotent profile write from JWT claims; preserves original created_at."""
    existing = get_teacher(sub)
    teacher = Teacher(
        sub=sub,
        email=email,
        name=name,
        created_at=existing.created_at if existing else now(),
    )
    store.put_overwrite(keys.teacher_pk(sub), keys.PROFILE_SK, ENTITY, teacher)
    return teacher
