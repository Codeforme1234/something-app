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


def format_deadline(deadline: datetime) -> str:
    """A human-readable deadline, e.g. "Aug 14, 2026, 20:47 UTC".

    Deadlines are always stored in UTC (app.core.clock.now()), and the label
    spells that out explicitly rather than trusting the reader to know or
    guess it. `%-d` (no leading zero) is a glibc/BSD strftime extension, not
    POSIX -- fine here since this only ever runs on the mac/Linux boxes this
    service is developed and deployed on, never Windows.
    """
    return deadline.strftime("%b %-d, %Y, %H:%M UTC")


def format_duration(duration_seconds: int) -> str:
    minutes = round(duration_seconds / 60)
    return f"{minutes} minute{'' if minutes == 1 else 's'}"


def format_question_count(question_count: int) -> str:
    return f"{question_count} question{'' if question_count == 1 else 's'}"


def send_invitation(
    *,
    student_name: str,
    student_email: str,
    test_title: str,
    deadline: datetime,
    duration_seconds: int,
    question_count: int,
    link: str,
) -> None:
    deadline_text = format_deadline(deadline)
    duration_text = format_duration(duration_seconds)
    questions_text = format_question_count(question_count)
    html = _INVITATION_TEMPLATE.render(
        student_name=student_name,
        test_title=test_title,
        deadline=deadline_text,
        duration=duration_text,
        questions=questions_text,
        link=link,
    )
    text = (
        f"Hi {student_name},\n\n"
        f'You have been invited to take "{test_title}".\n'
        f"Deadline: {deadline_text}\n"
        f"Duration: {duration_text}\n"
        f"Questions: {questions_text}\n\n"
        f"Start your test: {link}\n"
    )
    subject = f"You're invited: {test_title}"
    get_email_sender().send(to=student_email, subject=subject, html=html, text=text)
