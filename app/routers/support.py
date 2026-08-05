from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_teacher
from app.auth.protocol import TeacherClaims
from app.schemas.support import SupportRequest, SupportResponse
from app.services import support_service

router = APIRouter(prefix="/support", tags=["support"])


@router.post("", response_model=SupportResponse)
def submit_support_request(
    payload: SupportRequest, claims: TeacherClaims = Depends(get_current_teacher)
) -> SupportResponse:
    return support_service.submit_support_request(claims.sub, payload)
