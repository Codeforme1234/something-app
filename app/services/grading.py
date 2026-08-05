"""Pure server-side grading.

This is the only place a student's chosen index is compared against
`Question.correct_index`. Keeping it a pure function (no repo/service
imports) makes it trivial to unit test every edge case without DynamoDB.
"""

from app.models.question import Question


def grade(
    questions: list[Question], answers: dict[str, int]
) -> tuple[dict[str, bool], int, int]:
    """Grade `answers` (question_id -> chosen index) against `questions`.

    Unanswered questions count wrong. Answer-key entries that don't match
    any question id in this test are ignored (a client can't invent extra
    credit by answering a bogus id). Returns (per_question, correct_count,
    score) where score is round(100 * correct / total).
    """
    per_question: dict[str, bool] = {}
    correct_count = 0
    for question in questions:
        is_correct = answers.get(question.question_id) == question.correct_index
        per_question[question.question_id] = is_correct
        if is_correct:
            correct_count += 1

    total = len(questions)
    score = round(100 * correct_count / total) if total else 0
    return per_question, correct_count, score
