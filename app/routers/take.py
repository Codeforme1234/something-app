"""Student-facing attempt endpoints.

No auth dependency here on purpose: the token in the path *is* the
authorization (see app/services/attempt_service.py). This must never gain a
`Depends(get_current_teacher)` or similar -- students have no account.
"""

from fastapi import APIRouter

from app.schemas.take import StartAttemptResponse, SubmitRequest, SubmitResponse, TakeInfo
from app.services import attempt_service

router = APIRouter(prefix="/take", tags=["take"])


@router.get("/{token}", response_model=TakeInfo)
def get_take_info(token: str) -> TakeInfo:
    return attempt_service.get_info(token)


@router.post("/{token}/start", response_model=StartAttemptResponse)
def start_attempt(token: str) -> StartAttemptResponse:
    return attempt_service.start_attempt(token)


@router.post("/{token}/submit", response_model=SubmitResponse)
def submit_attempt(token: str, payload: SubmitRequest) -> SubmitResponse:
    return attempt_service.submit_attempt(token, payload)
