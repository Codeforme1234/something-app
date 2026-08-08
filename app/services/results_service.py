"""Teacher-facing results: derived session status, per-student review, and
test analytics -- all computed on the fly at read time from stored
sessions, submissions, and questions. No cron jobs, no background writes,
no stored aggregates.
"""

from datetime import datetime, timedelta

from app.core.clock import now
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models.question import Question
from app.models.session import SessionStatus, StudentSession
from app.models.submission import Submission
from app.models.test import Test
from app.repositories import feedback_repo, sessions_repo, store, submissions_repo, tests_repo
from app.schemas.results import FeedbackView, QuestionReview, QuestionStat, StudentDetail, TestAnalytics
from app.schemas.students import SessionRow
from app.services import feedback_job
from app.services.storage import question_image_url


def _get_owned_test(teacher_sub: str, test_id: str) -> store.Stored[Test]:
    stored = tests_repo.get_test(teacher_sub, test_id)
    if stored is None:
        raise NotFoundError("test not found")
    return stored


def effective_status(session: StudentSession, test: Test, at: datetime) -> str:
    """What the teacher should see right now, derived from the stored status
    plus server time. Never written back to the session -- purely a read-time
    projection.

    - `started` but time (including the submit grace window) ran out with
      nothing submitted -> "expired" (score stays absent).
    - `invited` but the test's deadline has passed -> "link_expired" (the
      student never even started).
    - otherwise, the stored status unchanged.
    """
    if session.status == SessionStatus.started and session.ends_at is not None:
        grace = timedelta(seconds=get_settings().submit_grace_seconds)
        if at > session.ends_at + grace:
            return "expired"
    if session.status == SessionStatus.invited and test.deadline is not None:
        if at > test.deadline:
            return "link_expired"
    return session.status.value


def get_student_detail(teacher_sub: str, test_id: str, session_id: str) -> StudentDetail:
    stored_test = _get_owned_test(teacher_sub, test_id)
    test = stored_test.model

    # Sessions live at PK=test_pk(test_id), so a session_id belonging to a
    # different test simply misses here -- the same "ownership by key"
    # pattern as tests_repo.get_test, just one level down.
    session_stored = sessions_repo.get_session(test_id, session_id)
    if session_stored is None:
        raise NotFoundError("session not found")
    session = session_stored.model

    row = SessionRow.from_model(session, effective_status(session, test, now()))

    review: list[QuestionReview] | None = None
    feedback: FeedbackView | None = None
    if session.status == SessionStatus.completed:
        submission_stored = submissions_repo.get_submission(test_id, session_id)
        assert submission_stored is not None, "a completed session always has a submission"
        submission = submission_stored.model
        questions = tests_repo.get_questions(test_id)
        review = [_question_review(q, submission) for q in questions]

        stored_feedback = feedback_repo.get_feedback(test_id, session_id)
        model = feedback_job.presented(stored_feedback.model if stored_feedback else None, session)
        feedback = FeedbackView.from_model(model) if model else None

    return StudentDetail(session=row, review=review, feedback=feedback)


def _question_review(question: Question, submission: Submission) -> QuestionReview:
    return QuestionReview(
        question_id=question.question_id,
        order=question.order,
        stem=question.stem,
        options=question.options,
        correct_index=question.correct_index,
        chosen_index=submission.answers.get(question.question_id),
        is_correct=submission.per_question.get(question.question_id, False),
        image_url=question_image_url(question.image_key),
        image_alt=question.image_alt,
    )


def get_analytics(teacher_sub: str, test_id: str) -> TestAnalytics:
    _get_owned_test(teacher_sub, test_id)

    sessions = sessions_repo.list_sessions(test_id)
    questions = tests_repo.get_questions(test_id)

    submissions: list[Submission] = []
    for session in sessions:
        if session.status != SessionStatus.completed:
            continue
        stored_submission = submissions_repo.get_submission(test_id, session.session_id)
        if stored_submission is not None:
            submissions.append(stored_submission.model)

    return compute_analytics(sessions, submissions, questions)


def compute_analytics(
    sessions: list[StudentSession], submissions: list[Submission], questions: list[Question]
) -> TestAnalytics:
    """Pure aggregation over already-fetched rows -- split out from
    get_analytics so the math is unit-testable without DynamoDB."""
    student_count = len(sessions)
    completed_count = sum(1 for s in sessions if s.status == SessionStatus.completed)
    completion_rate = round(100 * completed_count / student_count) if student_count else 0

    scores = [s.score for s in sessions if s.status == SessionStatus.completed and s.score is not None]
    average_score = round(sum(scores) / len(scores)) if scores else None
    highest_score = max(scores) if scores else None
    lowest_score = min(scores) if scores else None

    question_stats = [_question_stat(q, submissions) for q in questions]
    # Hardest first: lowest correct_rate first; ties broken by question order
    # so the result is deterministic.
    question_stats.sort(key=lambda qs: (qs.correct_rate, qs.order))

    return TestAnalytics(
        student_count=student_count,
        completed_count=completed_count,
        completion_rate=completion_rate,
        average_score=average_score,
        highest_score=highest_score,
        lowest_score=lowest_score,
        question_stats=question_stats,
    )


def _question_stat(question: Question, submissions: list[Submission]) -> QuestionStat:
    attempt_count = 0
    correct_count = 0
    for submission in submissions:
        if question.question_id not in submission.per_question:
            continue
        attempt_count += 1
        if submission.per_question[question.question_id]:
            correct_count += 1

    correct_rate = round(100 * correct_count / attempt_count) if attempt_count else 0
    return QuestionStat(
        question_id=question.question_id,
        order=question.order,
        stem=question.stem,
        correct_count=correct_count,
        attempt_count=attempt_count,
        correct_rate=correct_rate,
    )
