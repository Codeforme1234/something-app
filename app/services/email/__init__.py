"""Email sending: a real SES sender and a dev outbox sender behind one
Protocol, chosen once from Settings. Same lazy-import shape as
app/auth/dependencies.py::get_verifier, so a dev box never has to import
boto3's sesv2 client or hold SES credentials.
"""

from functools import lru_cache

from app.core.config import get_settings
from app.services.email.protocol import EmailSender


@lru_cache
def get_email_sender() -> EmailSender:
    settings = get_settings()
    if settings.email_mode == "ses":
        from app.services.email.ses import SesEmailSender

        return SesEmailSender()
    # outbox mode is only reachable in dev: Settings refuses fake modes in prod
    from app.services.email.outbox import OutboxEmailSender

    return OutboxEmailSender()
