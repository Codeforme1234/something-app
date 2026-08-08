"""Unit tests for app/services/email/invitations.py: the human-readable
formatting helpers, and the rendered email content (HTML + plain text) they
feed into. The email sender is monkeypatched so nothing here touches the
dev outbox or AWS."""

from datetime import UTC, datetime

from app.services.email import invitations as invitations_module
from app.services.email.invitations import (
    format_deadline,
    format_duration,
    format_question_count,
    send_invitation,
)


def test_format_deadline_is_human_readable_and_explicitly_utc():
    assert format_deadline(datetime(2026, 8, 14, 20, 47, tzinfo=UTC)) == "Aug 14, 2026, 20:47 UTC"


def test_format_duration_pluralizes_correctly():
    assert format_duration(60) == "1 minute"
    assert format_duration(1800) == "30 minutes"
    assert format_duration(0) == "0 minutes"


def test_format_question_count_pluralizes_correctly():
    assert format_question_count(1) == "1 question"
    assert format_question_count(4) == "4 questions"


class _FakeSender:
    def __init__(self):
        self.sent: dict = {}

    def send(self, to, subject, html, text):
        self.sent.update(to=to, subject=subject, html=html, text=text)


def _send(monkeypatch, **overrides) -> dict:
    sender = _FakeSender()
    monkeypatch.setattr(invitations_module, "get_email_sender", lambda: sender)

    defaults = dict(
        student_name="Ada Lovelace",
        student_email="ada@example.com",
        test_title="Algebra Basics",
        deadline=datetime(2026, 8, 14, 20, 47, tzinfo=UTC),
        duration_seconds=1800,
        question_count=4,
        link="https://app.quizdeck.example/t/abc123",
    )
    defaults.update(overrides)
    send_invitation(**defaults)
    return sender.sent


def test_invitation_text_contains_human_deadline_duration_and_question_count(monkeypatch):
    sent = _send(monkeypatch)

    assert "Aug 14, 2026, 20:47 UTC" in sent["text"]
    assert "30 minutes" in sent["text"]
    assert "4 questions" in sent["text"]
    assert "https://app.quizdeck.example/t/abc123" in sent["text"]


def test_invitation_html_contains_human_deadline_duration_and_question_count(monkeypatch):
    sent = _send(monkeypatch)

    assert "Aug 14, 2026, 20:47 UTC" in sent["html"]
    assert "30 minutes" in sent["html"]
    assert "4 questions" in sent["html"]
    assert "https://app.quizdeck.example/t/abc123" in sent["html"]


def test_invitation_subject_names_the_test(monkeypatch):
    sent = _send(monkeypatch, test_title="Cell Biology Midterm")
    assert sent["subject"] == "You're invited: Cell Biology Midterm"


def test_invitation_html_escapes_a_student_name_with_markup(monkeypatch):
    """Autoescape must actually be on -- a roster name is untrusted input."""
    sent = _send(monkeypatch, student_name="<img src=x onerror=alert(1)>")

    assert "<img src=x onerror=" not in sent["html"]
    assert "&lt;img" in sent["html"]


def test_invitation_html_extends_the_shared_base_wordmark(monkeypatch):
    sent = _send(monkeypatch)
    assert ">Quiz</span><span" in sent["html"]
    assert ">Deck</span>" in sent["html"]
