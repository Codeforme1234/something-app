"""Unit tests for the LLM feedback output validation layer. Same two-tier
split as app.llm.schemas (see tests/unit/test_llm_schemas.py) -- the wire
model handed to OpenAI's structured-output mode (including its nested
sub-models) must carry no constraint metadata, and the strict model enforces
every real rule after parsing."""

import pytest
from pydantic import ValidationError

from app.llm.feedback_schemas import (
    GeneratedFeedback,
    GeneratedFeedbackWire,
    GeneratedImprovementArea,
    GeneratedTopicMastery,
    ImprovementAreaWire,
    TopicMasteryWire,
)


def _area(**overrides) -> GeneratedImprovementArea:
    defaults = dict(topic="Negative numbers", diagnosis="Chose the positive option twice.", action="Practice signs.")
    defaults.update(overrides)
    return GeneratedImprovementArea(**defaults)


def _topic(**overrides) -> GeneratedTopicMastery:
    defaults = dict(topic="Negative numbers", correct=1, total=2)
    defaults.update(overrides)
    return GeneratedTopicMastery(**defaults)


def _feedback(**overrides) -> GeneratedFeedback:
    defaults = dict(
        summary="You did well overall.",
        strengths=["Strong grasp of basics"],
        improvement_areas=[_area()],
        study_plan=["Revisit negative numbers."],
        topic_breakdown=[_topic()],
    )
    defaults.update(overrides)
    return GeneratedFeedback(**defaults)


# --- GeneratedFeedbackWire: no constraint metadata, including nested models ----


def test_wire_schema_has_no_constraint_metadata():
    """OpenAI's strict schema mode rejects minLength/maxItems-style keywords on
    some models -- the wire model must be free of them, at every nesting
    level, so it never generates a JSON schema carrying one."""
    schema = GeneratedFeedbackWire.model_json_schema()
    banned = ("minLength", "maxLength", "minItems", "maxItems")

    def _assert_clean(node) -> None:
        if isinstance(node, dict):
            for key in banned:
                assert key not in node
            for value in node.values():
                _assert_clean(value)
        elif isinstance(node, list):
            for item in node:
                _assert_clean(item)

    _assert_clean(schema)


def test_nested_wire_models_have_no_constraint_metadata():
    for model_cls in (ImprovementAreaWire, TopicMasteryWire):
        schema = model_cls.model_json_schema()
        for field_schema in schema["properties"].values():
            assert "minLength" not in field_schema
            assert "maxLength" not in field_schema


def test_wire_schema_accepts_arbitrarily_long_or_many_values():
    # No caps at all -- this must construct even wildly out-of-bounds input,
    # since bounds enforcement is entirely the strict tier's job.
    GeneratedFeedbackWire(
        summary="x" * 5000,
        strengths=["y" * 5000] * 50,
        improvement_areas=[ImprovementAreaWire(topic="t" * 5000, diagnosis="d" * 5000, action="a" * 5000)] * 50,
        study_plan=["z" * 5000] * 50,
        topic_breakdown=[TopicMasteryWire(topic="t", correct=-5, total=-1)] * 50,
    )


# --- GeneratedFeedback: summary --------------------------------------------------


def test_summary_must_be_nonblank_after_strip():
    with pytest.raises(ValidationError):
        _feedback(summary="   ")


def test_summary_is_stripped():
    feedback = _feedback(summary="  Hello.  ")
    assert feedback.summary == "Hello."


def test_summary_max_length_enforced():
    with pytest.raises(ValidationError):
        _feedback(summary="x" * 2001)


def test_summary_at_max_length_is_accepted():
    _feedback(summary="x" * 2000)


def test_summary_at_min_length_is_accepted():
    _feedback(summary="x")


# --- GeneratedFeedback: strengths / study_plan (flat string lists) --------------


@pytest.mark.parametrize("field", ["strengths", "study_plan"])
def test_flat_list_fields_allow_empty(field):
    """A perfect score has no remediation study_plan items; a total miss may
    have no strengths -- empty lists must validate, not be rejected."""
    _feedback(**{field: []})


@pytest.mark.parametrize(("field", "cap"), [("strengths", 5), ("study_plan", 6)])
def test_flat_list_fields_cap_at_their_max(field, cap):
    with pytest.raises(ValidationError):
        _feedback(**{field: [f"item {i}" for i in range(cap + 1)]})


@pytest.mark.parametrize(("field", "cap"), [("strengths", 5), ("study_plan", 6)])
def test_flat_list_fields_accept_exactly_the_cap(field, cap):
    _feedback(**{field: [f"item {i}" for i in range(cap)]})


@pytest.mark.parametrize("field", ["strengths", "study_plan"])
def test_flat_list_items_are_stripped(field):
    feedback = _feedback(**{field: ["  padded  "]})
    assert getattr(feedback, field) == ["padded"]


@pytest.mark.parametrize("field", ["strengths", "study_plan"])
def test_flat_list_items_reject_empty_after_strip(field):
    with pytest.raises(ValidationError):
        _feedback(**{field: ["   "]})


@pytest.mark.parametrize("field", ["strengths", "study_plan"])
def test_flat_list_items_max_length_enforced(field):
    with pytest.raises(ValidationError):
        _feedback(**{field: ["x" * 301]})


@pytest.mark.parametrize("field", ["strengths", "study_plan"])
def test_flat_list_items_at_max_length_accepted(field):
    _feedback(**{field: ["x" * 300]})


# --- GeneratedFeedback: improvement_areas ----------------------------------------


def test_improvement_areas_allow_empty():
    _feedback(improvement_areas=[])


def test_improvement_areas_cap_at_five():
    with pytest.raises(ValidationError):
        _feedback(improvement_areas=[_area() for _ in range(6)])


def test_improvement_areas_accept_exactly_five():
    _feedback(improvement_areas=[_area() for _ in range(5)])


def test_improvement_area_topic_length_bounds():
    _area(topic="x")
    _area(topic="x" * 100)
    with pytest.raises(ValidationError):
        _area(topic="   ")
    with pytest.raises(ValidationError):
        _area(topic="x" * 101)


def test_improvement_area_diagnosis_length_bounds():
    _area(diagnosis="x")
    _area(diagnosis="x" * 400)
    with pytest.raises(ValidationError):
        _area(diagnosis="   ")
    with pytest.raises(ValidationError):
        _area(diagnosis="x" * 401)


def test_improvement_area_action_length_bounds():
    _area(action="x")
    _area(action="x" * 300)
    with pytest.raises(ValidationError):
        _area(action="   ")
    with pytest.raises(ValidationError):
        _area(action="x" * 301)


def test_improvement_area_fields_are_stripped():
    area = _area(topic="  Signs  ", diagnosis="  Oops  ", action="  Practice  ")
    assert (area.topic, area.diagnosis, area.action) == ("Signs", "Oops", "Practice")


# --- GeneratedFeedback: topic_breakdown ------------------------------------------


def test_topic_breakdown_allows_empty():
    _feedback(topic_breakdown=[])


def test_topic_breakdown_caps_at_six():
    with pytest.raises(ValidationError):
        _feedback(topic_breakdown=[_topic(topic=f"T{i}") for i in range(7)])


def test_topic_breakdown_accepts_exactly_six():
    _feedback(topic_breakdown=[_topic(topic=f"T{i}") for i in range(6)])


def test_topic_breakdown_topic_length_bounds():
    _topic(topic="x")
    _topic(topic="x" * 100)
    with pytest.raises(ValidationError):
        _topic(topic="   ")
    with pytest.raises(ValidationError):
        _topic(topic="x" * 101)


@pytest.mark.parametrize(("correct", "total"), [(0, 0), (0, 5), (5, 5), (3, 5)])
def test_topic_breakdown_accepts_correct_within_bounds(correct, total):
    _topic(correct=correct, total=total)


@pytest.mark.parametrize(("correct", "total"), [(-1, 5), (6, 5), (1, -1)])
def test_topic_breakdown_rejects_correct_out_of_bounds(correct, total):
    with pytest.raises(ValidationError):
        _topic(correct=correct, total=total)
