"""Support requests have no persistence layer of their own -- this
resolves the caller's own name/email/company and sends one email. Unlike
an invitation (whose session record survives a failed send), there is
nothing else recording the message, so a send failure propagates as
UpstreamError rather than being logged and swallowed.
"""

import logging

from app.core.exceptions import ConflictError, NotFoundError, UpstreamError
from app.repositories import companies_repo, teachers_repo
from app.schemas.support import SupportRequest, SupportResponse
from app.services.email import support as support_email

logger = logging.getLogger(__name__)


def submit_support_request(teacher_sub: str, payload: SupportRequest) -> SupportResponse:
    teacher = teachers_repo.get_teacher(teacher_sub)
    if teacher is None or not teacher.company_id:
        # Unreachable in practice: GET /me provisions the company on every
        # login, before the Help & Support page is ever reachable.
        raise ConflictError("admin has no company assigned yet")

    company_stored = companies_repo.get_company(teacher.company_id)
    if company_stored is None:
        raise NotFoundError("company not found")

    try:
        support_email.send_support_request(
            category=payload.category,
            subject=payload.subject,
            message=payload.message,
            admin_name=teacher.name,
            admin_email=teacher.email,
            company_name=company_stored.model.name,
        )
    except Exception as e:
        logger.error("failed to send support request (subject=%r): %s", payload.subject, e)
        raise UpstreamError("failed to send your message, please try again") from e

    return SupportResponse()
