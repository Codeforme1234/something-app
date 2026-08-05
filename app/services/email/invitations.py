"""Renders and sends the one email this phase needs: a student invitation.

Student names come from a teacher-typed or CSV-uploaded roster, so the HTML
side is rendered through a Jinja environment with autoescape on -- otherwise
a name like `<img src=x onerror=...>` would be an HTML injection hole the
moment a teacher opens the dev outbox or their real inbox renders it.
"""

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.services.email import get_email_sender

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=True)
_INVITATION_TEMPLATE = _env.get_template("invitation.html.j2")


def send_invitation(
    *, student_name: str, student_email: str, test_title: str, deadline: datetime, link: str
) -> None:
    deadline_text = deadline.isoformat(timespec="minutes")
    html = _INVITATION_TEMPLATE.render(
        student_name=student_name, test_title=test_title, deadline=deadline_text, link=link
    )
    text = (
        f"Hi {student_name},\n\n"
        f'You have been invited to take "{test_title}".\n'
        f"Deadline: {deadline_text}\n\n"
        f"Start your test: {link}\n"
    )
    subject = f"You're invited: {test_title}"
    get_email_sender().send(to=student_email, subject=subject, html=html, text=text)
