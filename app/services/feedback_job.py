"""Post-submission LLM feedback, generated in a background task right after a
student submits (see app.services.attempt_service.submit_attempt and
app.routers.take.py). Same start/run split as app.services.generation_job, for
the same reason: `start` does the one thing that must happen synchronously
with the submit -- write a `generating` placeholder so the teacher's
student-detail page has something to show immediately -- and `run` does the
slow model call afterwards.

Unlike generation_job, nothing here is billed, and nothing here can fail the
student's submission: `start` runs after the submission has already been
committed, so both it and `run` swallow every exception rather than raising.
"""

import logging
from datetime import timedelta

from app.core.clock import now
from app.core.config import get_settings
from app.core.exceptions import ConflictError, UpstreamError
from app.core.rich_text import rich_text_to_plain
from app.llm import get_feedback_generator
from app.llm.feedback_schemas import FeedbackInput, FeedbackQuestionResult, GeneratedFeedback
from app.models.feedback import FeedbackContent, FeedbackStatus, ImprovementArea, StudentFeedback, TopicMastery
from app.models.session import SessionStatus, StudentSession
from app.repositories import feedback_repo, sessions_repo, submissions_repo, tests_repo

logger = logging.getLogger(__name__)

#: Mirrors generation_job.MAX_ATTEMPTS -- same reasoning: a timeout or an
#: unparseable response is usually transient and worth exactly one retry.
MAX_ATTEMPTS = 2

#: Grace on top of the worst-case generation time before a `generating`
#: feedback row is presumed dead. Same idea as
#: test_service.STALE_GENERATION_MARGIN.
FEEDBACK_STALE_MARGIN = timedelta(minutes=5)


def stale_budget() -> timedelta:
    """Worst-case wall-clock time `run` should ever take: up to MAX_ATTEMPTS
    (2) attempts, each up to a model call plus one repair-retry call (so 2x
    openai_timeout_seconds per attempt), plus a safety margin for scheduling
    jitter."""
    return timedelta(seconds=get_settings().openai_timeout_seconds * 4) + FEEDBACK_STALE_MARGIN


def start(test_id: str, session_id: str) -> None:
    """Write the `generating` placeholder synchronously, inside the submit
    request. Never raises: feedback bookkeeping must not fail a student's
    submission, which by the time this runs has already been committed."""
    try:
        feedback_repo.create_feedback(
            StudentFeedback(
                session_id=session_id,
                test_id=test_id,
                status=FeedbackStatus.generating,
                generation_started_at=now(),
            )
        )
    except Exception:  # noqa: BLE001 -- must never fail the caller's submit
        logger.exception(
            "could not create feedback placeholder test=%s session=%s", test_id, session_id
        )


def run(teacher_sub: str, test_id: str, session_id: str) -> None:
    """The slow half. Never raises: it is a background task, so there is
    nobody left to return an error to -- every failure is recorded on the
    feedback row instead."""
    try:
        _run(teacher_sub, test_id, session_id)
    except Exception:  # noqa: BLE001 -- a background task swallows everything
        logger.exception("feedback run crashed unexpectedly test=%s session=%s", test_id, session_id)


def _run(teacher_sub: str, test_id: str, session_id: str) -> None:
    test_stored = tests_repo.get_test(teacher_sub, test_id)
    if test_stored is None:
        # The teacher deleted the test while the run was in flight.
        logger.warning("feedback run: test %s was deleted before it could run", test_id)
        _fail(test_id, session_id, "test was deleted")
        return
    test = test_stored.model

    submission_stored = submissions_repo.get_submission(test_id, session_id)
    if submission_stored is None:
        # Should be unreachable -- `start` is only ever called right after a
        # submission is written -- but there is nobody to fail loudly to, so
        # just log and stop rather than writing a misleading result.
        logger.warning("feedback run: no submission for test=%s session=%s", test_id, session_id)
        return
    submission = submission_stored.model

    session_stored = sessions_repo.get_session(test_id, session_id)
    if session_stored is None:
        # Same reasoning as the missing-submission case above: should be
        # unreachable, but there is nobody to fail loudly to.
        logger.warning("feedback run: no session for test=%s session=%s", test_id, session_id)
        return
    session = session_stored.model

    elapsed_seconds = None
    if session.started_at is not None and session.completed_at is not None:
        elapsed_seconds = int((session.completed_at - session.started_at).total_seconds())

    questions = sorted(tests_repo.get_questions(test_id), key=lambda q: q.order)
    rows = [
        FeedbackQuestionResult(
            order=q.order,
            stem=rich_text_to_plain(q.stem),
            options=q.options,
            chosen_index=submission.answers.get(q.question_id),
            correct_index=q.correct_index,
        )
        for q in questions
    ]

    feedback_input = FeedbackInput(
        test_title=test.title,
        difficulty=test.difficulty.value,
        score=submission.score,
        correct_count=submission.correct_count,
        total_questions=submission.total_questions,
        duration_seconds=test.duration_seconds,
        elapsed_seconds=elapsed_seconds,
        results=rows,
    )

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            generated = get_feedback_generator().generate(feedback_input)
            _finish(test_id, session_id, generated)
            logger.info(
                "feedback finished test=%s session=%s attempt=%d", test_id, session_id, attempt
            )
            return
        except Exception as exc:  # noqa: BLE001 -- a background task swallows everything
            last_error = exc
            logger.warning(
                "feedback attempt %d/%d failed test=%s session=%s: %s",
                attempt,
                MAX_ATTEMPTS,
                test_id,
                session_id,
                exc,
            )

    message = str(last_error) if isinstance(last_error, UpstreamError) else "feedback generation failed"
    _fail(test_id, session_id, message)


def _finish(test_id: str, session_id: str, generated: GeneratedFeedback) -> None:
    """Land the successful result.

    Re-read rather than trust any version captured earlier in `run`: a
    concurrent regenerate (app.services.feedback_service.regenerate) could
    have reset the row in the meantime, so the version to write against has
    to be whatever is stored right now.
    """
    content = FeedbackContent(
        summary=generated.summary,
        strengths=generated.strengths,
        # areas_to_improve/focus_topics are left at their [] defaults -- v2's
        # GeneratedFeedback no longer produces them (see
        # app.llm.feedback_schemas); they only ever hold data on rows written
        # under v1.
        improvement_areas=[
            ImprovementArea(topic=a.topic, diagnosis=a.diagnosis, action=a.action)
            for a in generated.improvement_areas
        ],
        study_plan=generated.study_plan,
        topic_breakdown=[
            TopicMastery(topic=t.topic, correct=t.correct, total=t.total)
            for t in generated.topic_breakdown
        ],
    )
    stored = feedback_repo.get_feedback(test_id, session_id)
    if stored is None:
        # The placeholder write in `start` itself must have failed -- there is
        # no row to update, so create the terminal state directly rather than
        # losing the result.
        _create_terminal(test_id, session_id, status=FeedbackStatus.ready, content=content, error=None)
        return

    updated = stored.model.model_copy(
        update={
            "status": FeedbackStatus.ready,
            "content": content,
            "generated_at": now(),
            "generation_started_at": None,
            "error": None,
        }
    )
    try:
        feedback_repo.update_feedback(updated, stored.version)
    except ConflictError:
        logger.warning("feedback ready-write lost a race test=%s session=%s", test_id, session_id)


def _fail(test_id: str, session_id: str, message: str) -> None:
    error = message[:500]
    stored = feedback_repo.get_feedback(test_id, session_id)
    if stored is None:
        _create_terminal(test_id, session_id, status=FeedbackStatus.failed, content=None, error=error)
        return

    updated = stored.model.model_copy(
        update={
            "status": FeedbackStatus.failed,
            "error": error,
            "generation_started_at": None,
        }
    )
    try:
        feedback_repo.update_feedback(updated, stored.version)
    except ConflictError:
        logger.warning("feedback fail-write lost a race test=%s session=%s", test_id, session_id)


def _create_terminal(
    test_id: str,
    session_id: str,
    *,
    status: FeedbackStatus,
    content: FeedbackContent | None,
    error: str | None,
) -> None:
    """The row was never written (the placeholder create in `start` failed) --
    write the terminal state directly so the result is not silently lost."""
    try:
        feedback_repo.create_feedback(
            StudentFeedback(
                session_id=session_id,
                test_id=test_id,
                status=status,
                content=content,
                generated_at=now() if content is not None else None,
                error=error,
            )
        )
    except Exception:  # noqa: BLE001 -- run() must never raise
        logger.exception(
            "could not create feedback row (was missing) test=%s session=%s", test_id, session_id
        )


def presented(feedback: StudentFeedback | None, session: StudentSession) -> StudentFeedback | None:
    """A feedback row as the API should describe it right now -- never
    written back, the same read-time-projection idea as
    test_service._presented and results_service.effective_status.

    - No feedback is shown at all until the session is completed.
    - A completed session with no stored row (the placeholder write in
      `start` failed) reads as a synthetic `failed` row rather than None, so
      the teacher sees an explanation instead of a blank.
    - A `generating` row whose run started longer ago than stale_budget()
      allows is presumed dead (a process restart mid-run, with no scheduler
      to reap it) and presented as `failed`.
    """
    if session.status != SessionStatus.completed:
        return None
    if feedback is None:
        return StudentFeedback(
            session_id=session.session_id,
            test_id=session.test_id,
            status=FeedbackStatus.failed,
            error="feedback was not generated",
        )
    if (
        feedback.status == FeedbackStatus.generating
        and feedback.generation_started_at is not None
        and now() - feedback.generation_started_at > stale_budget()
    ):
        return feedback.model_copy(
            update={
                "status": FeedbackStatus.failed,
                "error": "feedback generation stopped unexpectedly",
                "generation_started_at": None,
            }
        )
    return feedback
