from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.dependencies import get_current_teacher
from app.auth.protocol import TeacherClaims
from app.repositories import teachers_repo

router = APIRouter(tags=["me"])


class MeResponse(BaseModel):
    sub: str
    email: str
    name: str


@router.get("/me", response_model=MeResponse)
def get_me(claims: TeacherClaims = Depends(get_current_teacher)) -> MeResponse:
    teacher = teachers_repo.upsert_teacher(claims.sub, claims.email, claims.name)
    return MeResponse(sub=teacher.sub, email=teacher.email, name=teacher.name)
