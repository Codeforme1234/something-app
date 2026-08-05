"""Renders and sends a support request email from an admin to the support
inbox. The message body embeds admin-typed text (subject/message), so the
HTML side renders through the same autoescaping Jinja environment as
invitations -- see app/services/email/invitations.py.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.core.config import get_settings
from app.schemas.support import SupportCategory
from app.services.email import get_email_sender

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=True)
_SUPPORT_TEMPLATE = _env.get_template("support_request.html.j2")


def send_support_request(
    *,
    category: SupportCategory,
    subject: str,
    message: str,
    admin_name: str,
    admin_email: str,
    company_name: str,
) -> None:
    html = _SUPPORT_TEMPLATE.render(
        category=category.value,
        subject=subject,
        message=message,
        admin_name=admin_name,
        admin_email=admin_email,
        company_name=company_name,
    )
    text = (
        f"Category: {category.value}\n"
        f"From: {admin_name} <{admin_email}> ({company_name})\n"
        f"Subject: {subject}\n\n"
        f"{message}\n"
    )
    get_email_sender().send(
        to=get_settings().support_email,
        subject=f"[{category.value}] {subject}",
        html=html,
        text=text,
    )
