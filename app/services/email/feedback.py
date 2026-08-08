"""Renders and sends the post-submission feedback email: score, the LLM's
summary, then topic breakdown, strengths, improvement areas, and study plan
(v2's structured sections) -- plus the v1-compat flat lists, which render
only when every structured section is empty (a row generated before v2
shipped).

Autoescape stays on for the same reason invitations.py uses it: the student's
name is a teacher-typed or CSV-uploaded roster field, and every piece of
`content` is LLM output -- neither is trusted for HTML, so rendering either
without escaping would be an injection hole the moment a real inbox or the
dev outbox renders the message.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.models.feedback import FeedbackContent, ImprovementArea, TopicMastery
from app.services.email import get_email_sender

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=True)
_FEEDBACK_TEMPLATE = _env.get_template("feedback.html.j2")

#: Topic-breakdown count color thresholds, by correct/total ratio.
_STRONG_RATIO = 0.7
_WEAK_RATIO = 0.4
_COLOR_STRONG = "#1b6e4a"
_COLOR_WEAK = "#b3261e"
_COLOR_NEUTRAL = "#1c1b1f"


def _topic_color(correct: int, total: int) -> str:
    if total <= 0:
        return _COLOR_NEUTRAL
    ratio = correct / total
    if ratio >= _STRONG_RATIO:
        return _COLOR_STRONG
    if ratio < _WEAK_RATIO:
        return _COLOR_WEAK
    return _COLOR_NEUTRAL


def _topic_rows(rows: list[TopicMastery]) -> list[dict]:
    """Pre-colored rows for the template, so the .j2 file stays plain
    iteration with no arithmetic of its own."""
    return [
        {"topic": row.topic, "correct": row.correct, "total": row.total, "color": _topic_color(row.correct, row.total)}
        for row in rows
    ]


def _bulleted_section(label: str, items: list[str]) -> str:
    """A "Label:\\n- one\\n- two" block, or "" when the list is empty -- the
    plain-text mirror of the template's `{% if %}`-wrapped <ul> sections."""
    if not items:
        return ""
    lines = "\n".join(f"- {item}" for item in items)
    return f"\n\n{label}:\n{lines}"


def _numbered_section(label: str, items: list[str]) -> str:
    """The plain-text mirror of the template's `{% if %}`-wrapped <ol>."""
    if not items:
        return ""
    lines = "\n".join(f"{i}. {item}" for i, item in enumerate(items, start=1))
    return f"\n\n{label}:\n{lines}"


def _topic_breakdown_section(rows: list[TopicMastery]) -> str:
    if not rows:
        return ""
    lines = "\n".join(f"- {row.topic} — {row.correct}/{row.total}" for row in rows)
    return f"\n\nTopic breakdown:\n{lines}"


def _improvement_areas_section(areas: list[ImprovementArea]) -> str:
    if not areas:
        return ""
    lines = "\n".join(f"- {a.topic}: {a.diagnosis} Try this: {a.action}" for a in areas)
    return f"\n\nImprovement areas:\n{lines}"


def _legacy_sections(content: FeedbackContent) -> str:
    """The v1-compat flat lists, but ONLY when every v2 structured section is
    empty -- same gating the template applies -- so a row can never show both
    the old and new shapes at once."""
    if content.improvement_areas or content.study_plan or content.topic_breakdown:
        return ""
    return (
        f"{_bulleted_section('Areas to improve', content.areas_to_improve)}"
        f"{_bulleted_section('Suggested focus topics', content.focus_topics)}"
    )


def _link_line(link: str | None) -> str:
    if not link:
        return ""
    return f"\n\nView your test: {link}\n"


def send_feedback(
    *,
    student_name: str,
    student_email: str,
    test_title: str,
    score: int,
    correct_count: int,
    total_questions: int,
    content: FeedbackContent,
    # The student's take-page link, so they can revisit their score --
    # optional because a caller may not always have one to hand (e.g. a
    # future non-web delivery path).
    link: str | None = None,
) -> None:
    html = _FEEDBACK_TEMPLATE.render(
        student_name=student_name,
        test_title=test_title,
        score=score,
        correct_count=correct_count,
        total_questions=total_questions,
        content=content,
        topic_rows=_topic_rows(content.topic_breakdown),
        link=link,
    )
    text = (
        f"Hi {student_name},\n\n"
        f'You scored {score}% on "{test_title}" — {correct_count} of {total_questions} correct.\n\n'
        f"{content.summary}"
        f"{_topic_breakdown_section(content.topic_breakdown)}"
        f"{_bulleted_section('Strengths', content.strengths)}"
        f"{_improvement_areas_section(content.improvement_areas)}"
        f"{_numbered_section('Study plan', content.study_plan)}"
        f"{_legacy_sections(content)}"
        f"{_link_line(link)}"
        "\n"
    )
    subject = f"Your results & feedback: {test_title}"
    get_email_sender().send(to=student_email, subject=subject, html=html, text=text)
