"""Unit tests for the feedback prompt templates -- prompt text lives only in
app/llm/prompts/feedback_generation.py (CLAUDE.md rule), so this is where its
content is worth pinning down with tests.

v2: the model is deliberately handed full depth (options, the student's
choice, and the correct answer), so the old "never reveal the correct
answer" assertion is gone -- replaced by the ban on a bare per-question
answer key.
"""

from app.llm.feedback_schemas import FeedbackInput, FeedbackQuestionResult
from app.llm.prompts.feedback_generation import render_feedback_prompt, render_repair_prompt


def _result(
    order: int,
    *,
    correct_index: int = 0,
    chosen_index: int | None = 0,
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
    defaults = dict(
        test_title="Algebra Basics",
        difficulty="medium",
        score=75,
        correct_count=3,
        total_questions=4,
        duration_seconds=900,
        elapsed_seconds=None,
        results=results,
    )
    defaults.update(overrides)
    return FeedbackInput(**defaults)


def test_prompt_includes_title_difficulty_score_and_counts():
    _, user_prompt = render_feedback_prompt(_input([_result(1)]))
    assert "Algebra Basics" in user_prompt
    assert "medium" in user_prompt
    assert "75%" in user_prompt
    assert "3/4" in user_prompt


def test_time_line_present_when_elapsed_is_given():
    _, user_prompt = render_feedback_prompt(_input([_result(1)], duration_seconds=900, elapsed_seconds=480))
    assert "Time used: 8 of 15 minutes." in user_prompt


def test_time_line_absent_when_elapsed_is_none():
    _, user_prompt = render_feedback_prompt(_input([_result(1)], elapsed_seconds=None))
    assert "Time used:" not in user_prompt


def test_results_are_fenced_by_the_injected_nonce():
    _, user_prompt = render_feedback_prompt(_input([_result(1)]), nonce="FIXEDNONCE")
    assert "<<<RESULTS FIXEDNONCE>>>" in user_prompt
    assert "<<<END RESULTS FIXEDNONCE>>>" in user_prompt


def test_a_fresh_nonce_is_generated_when_none_is_given():
    _, first = render_feedback_prompt(_input([_result(1)]))
    _, second = render_feedback_prompt(_input([_result(1)]))
    assert first != second


def test_per_question_block_shows_options_choice_and_correct_answer():
    result = _result(
        2,
        stem="Which phase has chromosomes aligned at the equator?",
        options=["Prophase", "Telophase", "Metaphase", "Anaphase"],
        chosen_index=0,
        correct_index=2,
    )
    _, user_prompt = render_feedback_prompt(_input([result]))

    assert "Q2 [wrong] Which phase has chromosomes aligned at the equator?" in user_prompt
    assert "Options: A) Prophase  B) Telophase  C) Metaphase  D) Anaphase" in user_prompt
    assert "Student chose: A) Prophase   Correct: C) Metaphase" in user_prompt


def test_unanswered_question_shows_no_answer_and_the_correct_option():
    result = _result(3, options=["A", "B", "C", "D"], chosen_index=None, correct_index=1)
    _, user_prompt = render_feedback_prompt(_input([result]))

    assert "Q3 [unanswered]" in user_prompt
    assert "Student chose: (no answer)   Correct: B) B" in user_prompt


def test_correct_marker_when_choice_matches():
    result = _result(1, chosen_index=2, correct_index=2)
    _, user_prompt = render_feedback_prompt(_input([result]))
    assert "Q1 [correct]" in user_prompt


def test_system_prompt_bans_a_bare_answer_key_and_shaming():
    system_prompt, _ = render_feedback_prompt(_input([_result(1)]))
    lowered = system_prompt.lower()
    assert "bare answer key" in lowered
    assert "never shame the student" in lowered


def test_system_prompt_bans_generic_filler():
    system_prompt, _ = render_feedback_prompt(_input([_result(1)]))
    lowered = system_prompt.lower()
    assert "study more" in lowered
    assert "generic filler" in lowered


def test_system_prompt_states_exam_technique_and_edge_case_rules():
    system_prompt, _ = render_feedback_prompt(_input([_result(1)]))
    lowered = system_prompt.lower()
    assert "exam-technique" in lowered
    assert "perfect score" in lowered
    assert "topic_breakdown" in lowered


def test_repair_prompt_appends_the_errors():
    _, user_prompt = render_feedback_prompt(_input([_result(1)]))

    repaired = render_repair_prompt(user_prompt, "study_plan had 9 items, max is 6")

    assert repaired.startswith(user_prompt)
    assert "study_plan had 9 items, max is 6" in repaired
