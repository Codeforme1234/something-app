"""FakeFeedbackGenerator must always produce output that passes the same
validation layer real generated output goes through, for every mix of
correct/wrong/unanswered results a real attempt can produce -- including a
perfect score, a total miss, and an all-unanswered attempt, where the product
rule says some lists come back empty by design, not as a defect."""

from app.llm.fake_feedback import FakeFeedbackGenerator
from app.llm.feedback_schemas import FeedbackInput, FeedbackQuestionResult, GeneratedFeedback


def _result(
    order: int,
    *,
    chosen_index: int | None,
    correct_index: int = 0,
    stem: str = "Q?",
    options: list[str] | None = None,
) -> FeedbackQuestionResult:
    return FeedbackQuestionResult(
        order=order,
        stem=stem,
        options=options or ["A", "B", "C", "D"],
        chosen_index=chosen_index,
        correct_index=correct_index,
    )


def _input(results: list[FeedbackQuestionResult], **overrides) -> FeedbackInput:
    total = len(results)
    correct_count = sum(1 for r in results if r.chosen_index == r.correct_index)
    defaults = dict(
        test_title="Algebra",
        difficulty="medium",
        score=round(100 * correct_count / total) if total else 0,
        correct_count=correct_count,
        total_questions=total,
        duration_seconds=900,
        elapsed_seconds=None,
        results=results,
    )
    defaults.update(overrides)
    return FeedbackInput(**defaults)


def test_deterministic_same_input_twice_gives_the_same_output():
    results = [_result(1, chosen_index=0, correct_index=0), _result(2, chosen_index=1, correct_index=0)]
    feedback_input = _input(results)

    first = FakeFeedbackGenerator().generate(feedback_input)
    second = FakeFeedbackGenerator().generate(feedback_input)

    assert first == second


def test_all_correct_passes_validation_with_no_improvement_areas():
    results = [_result(i, chosen_index=0, correct_index=0, stem=f"Question {i} about topic") for i in range(1, 4)]

    generated = FakeFeedbackGenerator().generate(_input(results))

    GeneratedFeedback.model_validate(generated.model_dump())
    assert generated.improvement_areas == []
    assert len(generated.strengths) > 0
    # No unanswered/rushed signal -- no exam-technique study_plan item either.
    assert generated.study_plan == []


def test_all_wrong_passes_validation_with_no_strengths():
    results = [
        _result(i, chosen_index=1, correct_index=0, stem=f"Question {i} about topic") for i in range(1, 4)
    ]

    generated = FakeFeedbackGenerator().generate(_input(results))

    GeneratedFeedback.model_validate(generated.model_dump())
    assert generated.strengths == []
    assert len(generated.improvement_areas) > 0
    assert len(generated.study_plan) > 0


def test_all_unanswered_passes_validation_and_adds_an_exam_technique_item():
    results = [
        _result(i, chosen_index=None, correct_index=0, stem=f"Question {i} about topic") for i in range(1, 4)
    ]

    generated = FakeFeedbackGenerator().generate(_input(results))

    GeneratedFeedback.model_validate(generated.model_dump())
    assert generated.strengths == []
    assert len(generated.improvement_areas) > 0
    assert any("unanswered" in area.diagnosis.lower() for area in generated.improvement_areas)
    # An unanswered question always warrants the exam-technique line.
    assert any("pace" in item.lower() or "attempt every" in item.lower() for item in generated.study_plan)


def test_mixed_results_pass_validation():
    results = [
        _result(1, chosen_index=0, correct_index=0, stem="Correct question about mitosis"),
        _result(2, chosen_index=1, correct_index=2, stem="Wrong question about meiosis"),
        _result(3, chosen_index=None, correct_index=0, stem="Skipped question about genetics"),
    ]

    generated = FakeFeedbackGenerator().generate(_input(results))

    GeneratedFeedback.model_validate(generated.model_dump())
    assert len(generated.strengths) > 0
    assert len(generated.improvement_areas) > 0
    assert len(generated.topic_breakdown) > 0


def test_no_questions_at_all_still_passes_validation():
    generated = FakeFeedbackGenerator().generate(_input([]))

    GeneratedFeedback.model_validate(generated.model_dump())
    assert generated.strengths == []
    assert generated.improvement_areas == []
    assert generated.study_plan == []
    assert generated.topic_breakdown == []


def test_summary_contains_the_score_and_title():
    feedback_input = _input(
        [_result(1, chosen_index=0, correct_index=0)], test_title="Algebra Basics", score=75, correct_count=3,
        total_questions=4,
    )
    generated = FakeFeedbackGenerator().generate(feedback_input)
    assert "75" in generated.summary
    assert "Algebra Basics" in generated.summary


def test_diagnosis_names_the_chosen_and_correct_option_text():
    result = _result(
        1,
        chosen_index=0,
        correct_index=2,
        stem="Which phase has chromosomes aligned at the equator?",
        options=["Prophase", "Telophase", "Metaphase", "Anaphase"],
    )
    generated = FakeFeedbackGenerator().generate(_input([result]))

    assert len(generated.improvement_areas) == 1
    diagnosis = generated.improvement_areas[0].diagnosis
    assert "Prophase" in diagnosis
    assert "Metaphase" in diagnosis


def test_topic_breakdown_counts_match_the_per_question_data():
    # Same first three words -> same mechanical "topic" -> one grouped row.
    results = [
        _result(1, chosen_index=0, correct_index=0, stem="Cell organelles function overview"),
        _result(2, chosen_index=1, correct_index=0, stem="Cell organelles function basics"),
    ]
    generated = FakeFeedbackGenerator().generate(_input(results))

    assert len(generated.topic_breakdown) == 1
    row = generated.topic_breakdown[0]
    assert row.total == 2
    assert row.correct == 1


def test_a_very_long_stem_produces_a_topic_within_the_length_cap():
    long_stem = "x" * 1000
    results = [_result(1, chosen_index=0, correct_index=0, stem=long_stem)]

    generated = FakeFeedbackGenerator().generate(_input(results))

    GeneratedFeedback.model_validate(generated.model_dump())
    assert len(generated.strengths[0]) <= 100
