"""Unit tests for app/services/email/feedback.py: the topic-breakdown color
helper, the legacy-section gating, and the optional take-page link (both in
the plain-text body and the HTML secondary CTA). The email sender is
monkeypatched so nothing here touches the dev outbox or AWS."""

from app.models.feedback import FeedbackContent, ImprovementArea, TopicMastery
from app.services.email import feedback as feedback_module
from app.services.email.feedback import _topic_color, send_feedback


def _content(**overrides) -> FeedbackContent:
    defaults = dict(
        summary="You did well overall.",
        strengths=["Basics"],
        improvement_areas=[
            ImprovementArea(topic="Signs", diagnosis="Chose the positive option.", action="Practice signs.")
        ],
        study_plan=["Revisit signs."],
        topic_breakdown=[TopicMastery(topic="Signs", correct=1, total=2)],
    )
    defaults.update(overrides)
    return FeedbackContent(**defaults)


# --- _topic_color --------------------------------------------------------------


def test_topic_color_strong_at_or_above_seventy_percent():
    assert _topic_color(7, 10) == "#1b6e4a"
    assert _topic_color(10, 10) == "#1b6e4a"


def test_topic_color_weak_below_forty_percent():
    assert _topic_color(0, 10) == "#b3261e"
    assert _topic_color(3, 10) == "#b3261e"


def test_topic_color_neutral_in_between():
    assert _topic_color(5, 10) == "#1c1b1f"


def test_topic_color_neutral_when_total_is_zero():
    assert _topic_color(0, 0) == "#1c1b1f"


# --- send_feedback ---------------------------------------------------------------


class _FakeSender:
    def __init__(self):
        self.sent: dict = {}

    def send(self, to, subject, html, text):
        self.sent.update(to=to, subject=subject, html=html, text=text)


def _send(monkeypatch, **overrides) -> dict:
    sender = _FakeSender()
    monkeypatch.setattr(feedback_module, "get_email_sender", lambda: sender)

    defaults = dict(
        student_name="Ada Lovelace",
        student_email="ada@example.com",
        test_title="Algebra Basics",
        score=67,
        correct_count=8,
        total_questions=12,
        content=_content(),
        link=None,
    )
    defaults.update(overrides)
    send_feedback(**defaults)
    return sender.sent


def test_link_line_present_in_text_when_link_given(monkeypatch):
    sent = _send(monkeypatch, link="https://app.quizdeck.example/t/abc123")
    assert "View your test: https://app.quizdeck.example/t/abc123" in sent["text"]


def test_link_line_absent_from_text_when_link_is_none(monkeypatch):
    sent = _send(monkeypatch, link=None)
    assert "View your test" not in sent["text"]


def test_html_secondary_cta_present_when_link_given(monkeypatch):
    sent = _send(monkeypatch, link="https://app.quizdeck.example/t/abc123")
    assert "View your test" in sent["html"]
    assert "https://app.quizdeck.example/t/abc123" in sent["html"]


def test_html_secondary_cta_absent_when_link_is_none(monkeypatch):
    sent = _send(monkeypatch, link=None)
    assert "View your test" not in sent["html"]


def test_legacy_sections_hidden_when_structured_fields_are_present(monkeypatch):
    v2_with_stray_legacy_data = _content(areas_to_improve=["Old field"], focus_topics=["Old topic"])
    sent = _send(monkeypatch, content=v2_with_stray_legacy_data)

    assert "Old field" not in sent["text"]
    assert "Old topic" not in sent["text"]
    assert "Old field" not in sent["html"]
    assert "Old topic" not in sent["html"]


def test_legacy_sections_shown_when_structured_fields_are_all_empty(monkeypatch):
    v1_content = FeedbackContent(
        summary="You did well overall.",
        strengths=["Basics"],
        areas_to_improve=["Watch your signs"],
        focus_topics=["Negative numbers"],
    )
    sent = _send(monkeypatch, content=v1_content)

    assert "Watch your signs" in sent["text"]
    assert "Negative numbers" in sent["text"]
    assert "Watch your signs" in sent["html"]
    assert "Negative numbers" in sent["html"]


def test_topic_breakdown_row_color_reflected_in_html(monkeypatch):
    content = _content(topic_breakdown=[TopicMastery(topic="Strong topic", correct=9, total=10)])
    sent = _send(monkeypatch, content=content)
    assert "#1b6e4a" in sent["html"]


def test_subject_names_the_test(monkeypatch):
    sent = _send(monkeypatch, test_title="Cell Biology Midterm")
    assert sent["subject"] == "Your results & feedback: Cell Biology Midterm"


def test_html_escapes_llm_generated_text(monkeypatch):
    """content is LLM output, untrusted for HTML the same as a roster name."""
    content = _content(strengths=["<img src=x onerror=alert(1)>"])
    sent = _send(monkeypatch, content=content)
    assert "<img src=x onerror=" not in sent["html"]
    assert "&lt;img" in sent["html"]
