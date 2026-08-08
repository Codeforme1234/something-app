from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.auth.dependencies import get_current_teacher
from app.auth.protocol import TeacherClaims
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.repositories import companies_repo, teachers_repo

router = APIRouter(tags=["me"])


class MeResponse(BaseModel):
    sub: str
    email: str
    name: str
    # Every authenticated caller is the sole admin of their own company (see
    # CLAUDE.md rule 8) -- a real, if currently single-valued, role. Sourced
    # from the backend rather than hardcoded client-side so a future actual
    # roles system has one place to change.
    role: Literal["admin"] = "admin"
    company_name: str
    credit_balance: int
    ai_credit_balance: int
    # What an AI run costs, keyed by mode ("prompt" / "pdf"). Served rather than
    # hardcoded in the UI so pricing lives in exactly one place.
    ai_credit_cost: dict[str, int]
    # True until the teacher has confirmed their name and company. `name` and
    # `company_name` above are provisional while this is true -- derived from
    # the identity provider, which for a Cognito pool without the `profile`
    # scope is just their email address.
    needs_onboarding: bool


class OnboardingRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    company_name: Annotated[str, Field(min_length=1, max_length=100)]

    @field_validator("name", "company_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


def _me_response(teacher) -> MeResponse:
    # upsert_teacher always provisions/backfills a company before returning,
    # so a miss here would mean the company record itself vanished.
    assert teacher.company_id is not None
    company_stored = companies_repo.get_company(teacher.company_id)
    if company_stored is None:
        raise NotFoundError("company not found")

    settings = get_settings()
    return MeResponse(
        sub=teacher.sub,
        email=teacher.email,
        name=teacher.name,
        company_name=company_stored.model.name,
        credit_balance=company_stored.model.credit_balance,
        # None means "never granted" on the model; the wire contract is a plain
        # int, so an ungranted balance reads as 0 rather than null.
        ai_credit_balance=company_stored.model.ai_credit_balance or 0,
        ai_credit_cost={
            "prompt": settings.ai_credit_cost_prompt,
            "pdf": settings.ai_credit_cost_pdf,
        },
        needs_onboarding=not teacher.onboarded,
    )


@router.get("/me", response_model=MeResponse)
def get_me(claims: TeacherClaims = Depends(get_current_teacher)) -> MeResponse:
    teacher = teachers_repo.upsert_teacher(claims.sub, claims.email, claims.name)
    return _me_response(teacher)


@router.post("/me/onboarding", response_model=MeResponse)
def complete_onboarding(
    payload: OnboardingRequest, claims: TeacherClaims = Depends(get_current_teacher)
) -> MeResponse:
    """First-login setup: the teacher names themselves and their company.

    Before this, both are provisional -- the name comes from the identity
    provider and the company is "<name>'s company". Idempotent, so re-posting
    is a rename rather than an error; the frontend only shows the form while
    `needs_onboarding` is true.
    """
    # upsert first, so a teacher who somehow reaches this before /me still has a
    # profile and a company to update.
    teachers_repo.upsert_teacher(claims.sub, claims.email, claims.name)
    teacher = teachers_repo.complete_onboarding(
        claims.sub, payload.name, payload.company_name
    )
    return _me_response(teacher)
