from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.dependencies import get_current_teacher
from app.auth.protocol import TeacherClaims
from app.core.exceptions import NotFoundError
from app.repositories import companies_repo, teachers_repo

router = APIRouter(tags=["me"])


class MeResponse(BaseModel):
    sub: str
    email: str
    name: str
    company_name: str
    credit_balance: int


@router.get("/me", response_model=MeResponse)
def get_me(claims: TeacherClaims = Depends(get_current_teacher)) -> MeResponse:
    teacher = teachers_repo.upsert_teacher(claims.sub, claims.email, claims.name)

    # upsert_teacher always provisions/backfills a company before returning,
    # so a miss here would mean the company record itself vanished.
    assert teacher.company_id is not None
    company_stored = companies_repo.get_company(teacher.company_id)
    if company_stored is None:
        raise NotFoundError("company not found")

    return MeResponse(
        sub=teacher.sub,
        email=teacher.email,
        name=teacher.name,
        company_name=company_stored.model.name,
        credit_balance=company_stored.model.credit_balance,
    )
