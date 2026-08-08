"""AI credits: the pool metered separately from test-creation credits.

Both are spent by an AI run, and both live on the same Company item, so a single
`model_copy` debits them together and the existing
tests_repo.create_test_and_spend_credit transaction covers both atomically.

Why two pools at all: a test-creation credit is about how many tests a company
may have (CLAUDE.md rule 8), while an AI run costs real money per call. A company
can be allowed plenty of the former and few of the latter.
"""

from enum import StrEnum

from app.core.config import get_settings
from app.models.company import Company
from app.schemas.tests import GenerateQuestionsRequest


class GenerationMode(StrEnum):
    #: Topic and guidelines only -- one cheap model call.
    prompt = "prompt"
    #: Grounded in an uploaded document. Costs more: the source text makes the
    #: prompt far larger, and reading the file may itself have needed a vision
    #: call (see app/services/knowledge_base.py).
    document = "document"


def mode_for(payload: GenerateQuestionsRequest) -> GenerationMode:
    """Derived from the request, never declared by the client -- otherwise a
    caller could claim the cheaper mode while attaching a document."""
    if payload.knowledge_base or payload.knowledge_base_key:
        return GenerationMode.document
    return GenerationMode.prompt


def cost(mode: GenerationMode) -> int:
    settings = get_settings()
    if mode is GenerationMode.document:
        return settings.ai_credit_cost_pdf
    return settings.ai_credit_cost_prompt


def available(company: Company) -> int:
    """`None` on the model means "never granted", which spends like zero."""
    return company.ai_credit_balance or 0


def debited(company: Company, *, ai_credits: int, test_credits: int = 1) -> Company:
    """Both balances debited in one copy, so they ride in a single item write."""
    return company.model_copy(
        update={
            "credit_balance": company.credit_balance - test_credits,
            "ai_credit_balance": available(company) - ai_credits,
        }
    )


def refunded(company: Company, *, ai_credits: int, test_credits: int = 1) -> Company:
    """The exact inverse of `debited`, for a run that failed after the debit had
    already landed (app/services/generation_job.py).

    A failed run leaves the teacher with no usable test, so charging for it
    would mean our own bug costs them credit. Written as its own function rather
    than `debited(-n)` so that a refund is greppable, and can never be misread
    as a charge in a diff.
    """
    return company.model_copy(
        update={
            "credit_balance": company.credit_balance + test_credits,
            "ai_credit_balance": available(company) + ai_credits,
        }
    )
