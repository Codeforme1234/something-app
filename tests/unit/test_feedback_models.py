"""v1-compat: rows written before the v2 prompt/schema shipped
(app.llm.feedback_schemas.GeneratedFeedback) are still sitting in the dev
table with only the old flat summary/strengths/areas_to_improve/focus_topics
shape -- no improvement_areas/study_plan/topic_breakdown keys at all. Both
the storage model and the teacher-facing view must keep reading them.
"""

import json

from app.core.clock import now
from app.models.feedback import FeedbackContent, FeedbackStatus, StudentFeedback
from app.schemas.results import FeedbackView

# Exactly the shape app.llm.feedback_schemas.GeneratedFeedback (v1) produced,
# with no trace of the v2 fields -- as if read straight out of an old row's
# `data` blob (app.repositories.store._encode/model_dump_json).
V1_CONTENT_JSON = json.dumps(
    {
        "summary": "You did well overall.",
        "strengths": ["Strong grasp of basics"],
        "areas_to_improve": ["Watch your signs"],
        "focus_topics": ["Negative numbers"],
    }
)


def test_v1_shaped_content_deserializes_with_empty_v2_sections():
    content = FeedbackContent.model_validate_json(V1_CONTENT_JSON)

    assert content.summary == "You did well overall."
    assert content.strengths == ["Strong grasp of basics"]
    assert content.areas_to_improve == ["Watch your signs"]
    assert content.focus_topics == ["Negative numbers"]
    assert content.improvement_areas == []
    assert content.study_plan == []
    assert content.topic_breakdown == []


def test_v1_shaped_studentfeedback_round_trips_through_model_dump_json():
    """The same shape, one level up: a whole StudentFeedback row as
    store.get would hand it back after model_validate_json on the stored
    blob."""
    content = FeedbackContent.model_validate_json(V1_CONTENT_JSON)
    feedback = StudentFeedback(
        session_id="01SESSION",
        test_id="01TEST",
        status=FeedbackStatus.ready,
        generated_at=now(),
        content=content,
    )

    reloaded = StudentFeedback.model_validate_json(feedback.model_dump_json())

    assert reloaded.content is not None
    assert reloaded.content.areas_to_improve == ["Watch your signs"]
    assert reloaded.content.improvement_areas == []


def test_feedback_view_from_model_returns_legacy_lists_and_empty_new_lists():
    content = FeedbackContent.model_validate_json(V1_CONTENT_JSON)
    feedback = StudentFeedback(
        session_id="01SESSION",
        test_id="01TEST",
        status=FeedbackStatus.ready,
        generated_at=now(),
        content=content,
    )

    view = FeedbackView.from_model(feedback)

    assert view.status == FeedbackStatus.ready
    assert view.summary == "You did well overall."
    assert view.strengths == ["Strong grasp of basics"]
    assert view.areas_to_improve == ["Watch your signs"]
    assert view.focus_topics == ["Negative numbers"]
    assert view.improvement_areas == []
    assert view.study_plan == []
    assert view.topic_breakdown == []
