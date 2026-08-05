"""Unit tests for the pure grading function -- see app/services/grading.py."""

from app.models.question import Question
from app.services.grading import grade


def _questions(n: int) -> list[Question]:
    return [
        Question(question_id=f"q{i}", order=i, stem=f"Q{i}?", options=["a", "b", "c", "d"], correct_index=1)
        for i in range(1, n + 1)
    ]


def test_all_correct_scores_100():
    questions = _questions(4)
    answers = {q.question_id: 1 for q in questions}

    per_question, correct_count, score = grade(questions, answers)

    assert correct_count == 4
    assert score == 100
    assert all(per_question.values())


def test_all_wrong_scores_0():
    questions = _questions(4)
    answers = {q.question_id: 0 for q in questions}

    per_question, correct_count, score = grade(questions, answers)

    assert correct_count == 0
    assert score == 0
    assert not any(per_question.values())


def test_partial_credit():
    questions = _questions(4)
    answers = {"q1": 1, "q2": 1, "q3": 0, "q4": 0}

    per_question, correct_count, score = grade(questions, answers)

    assert correct_count == 2
    assert score == 50
    assert per_question == {"q1": True, "q2": True, "q3": False, "q4": False}


def test_unanswered_question_counts_wrong():
    questions = _questions(2)
    answers = {"q1": 1}  # q2 never answered

    per_question, correct_count, score = grade(questions, answers)

    assert correct_count == 1
    assert per_question["q2"] is False
    assert score == 50


def test_unknown_question_id_in_answers_is_ignored():
    questions = _questions(1)
    answers = {"q1": 1, "does-not-exist": 1}

    per_question, correct_count, score = grade(questions, answers)

    assert correct_count == 1
    assert score == 100
    assert "does-not-exist" not in per_question


def test_score_rounds_to_nearest_int():
    # 1/3 correct -> 33.33...% rounds to 33
    questions = _questions(3)
    answers = {"q1": 1}

    _, correct_count, score = grade(questions, answers)

    assert correct_count == 1
    assert score == 33


def test_score_rounding_two_of_three():
    # 2/3 correct -> 66.66...% rounds to 67
    questions = _questions(3)
    answers = {"q1": 1, "q2": 1}

    _, correct_count, score = grade(questions, answers)

    assert correct_count == 2
    assert score == 67


def test_no_questions_scores_zero_without_dividing_by_zero():
    per_question, correct_count, score = grade([], {})

    assert per_question == {}
    assert correct_count == 0
    assert score == 0
