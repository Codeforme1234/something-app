"""Unit tests for app/services/feedback_job.py.

Mirrors tests/unit/test_generation_job.py's approach: `start` must write a
placeholder synchronously and never raise, `run` must never raise either, and
a run that fails twice must record the failure on the row. Repositories and
the LLM are monkeypatched, so nothing here touches DynamoDB or OpenAI.
"""

from datetime import timedelta

from app.core.clock import now
from app.core.exceptions import ConflictError, UpstreamError
from app.llm.feedback_schemas import GeneratedFeedback, GeneratedImprovementArea, GeneratedTopicMastery
from app.models.feedback import FeedbackStatus, StudentFeedback
from app.models.question import Question
from app.models.session import SessionStatus, StudentSession
from app.models.submission import Submission
from app.models.test import Difficulty, Test, TestStatus
from app.repositories import feedback_repo, sessions_repo, store, submissions_repo, tests_repo
from app.services import feedback_job

TEACHER_SUB = "dev-alice"
TEST_ID = "01TESTID"
SESSION_ID = "01SESSIONID"


def _feedback(**overrides) -> StudentFeedback:
    defaults = dict(session_id=SESSION_ID, test_id=TEST_ID, status=FeedbackStatus.generating)
    defaults.update(overrides)
    return StudentFeedback(**defaults)


def _generated(**overrides) -> GeneratedFeedback:
    defaults = dict(
        summary="You did fine.",
        strengths=["Good recall"],
        improvement_areas=[
            GeneratedImprovementArea(
                topic="Signs", diagnosis="Chose the positive option instead.", action="Practice signed arithmetic."
            )
        ],
        study_plan=["Revisit signs."],
        topic_breakdown=[GeneratedTopicMastery(topic="Signs", correct=1, total=2)],
    )
    defaults.update(overrides)
    return GeneratedFeedback(**defaults)


class _StubGenerator:
    def __init__(self, feedback: GeneratedFeedback | None = None):
        self.feedback = feedback or _generated()
        self.calls: list = []

    def generate(self, input):
        self.calls.append(input)
        return self.feedback


# --- start ---------------------------------------------------------------------


def test_start_creates_a_generating_placeholder_with_generation_started_at(monkeypatch):
    created: list[StudentFeedback] = []
    monkeypatch.setattr(feedback_repo, "create_feedback", lambda fb: created.append(fb))

    feedback_job.start(TEST_ID, SESSION_ID)

    assert len(created) == 1
    assert created[0].status == FeedbackStatus.generating
    assert created[0].test_id == TEST_ID
    assert created[0].session_id == SESSION_ID
    assert created[0].generation_started_at is not None


def test_start_never_raises_when_the_repo_raises(monkeypatch):
    monkeypatch.setattr(
        feedback_repo, "create_feedback", lambda fb: (_ for _ in ()).throw(RuntimeError("dynamo is down"))
    )

    feedback_job.start(TEST_ID, SESSION_ID)  # must not raise


def test_start_swallows_conflict_error(monkeypatch):
    """A placeholder that already exists (a retried request?) must not turn
    into a 500 for the student's submit."""
    monkeypatch.setattr(
        feedback_repo, "create_feedback", lambda fb: (_ for _ in ()).throw(ConflictError("item already exists"))
    )

    feedback_job.start(TEST_ID, SESSION_ID)  # must not raise


# --- run: setup helpers ---------------------------------------------------------


def _test_model(**overrides) -> Test:
    defaults = dict(
        test_id=TEST_ID,
        teacher_sub=TEACHER_SUB,
        title="Algebra Basics",
        difficulty=Difficulty.medium,
        duration_seconds=900,
        status=TestStatus.published,
        created_at=now(),
    )
    defaults.update(overrides)
    return Test(**defaults)


def _submission(**overrides) -> Submission:
    defaults = dict(
        session_id=SESSION_ID,
        test_id=TEST_ID,
        submitted_at=now(),
        answers={"q1": 1},
        per_question={"q1": True, "q2": False},
        score=50,
        correct_count=1,
        total_questions=2,
    )
    defaults.update(overrides)
    return Submission(**defaults)


def _session(**overrides) -> StudentSession:
    defaults = dict(
        session_id=SESSION_ID,
        test_id=TEST_ID,
        student_name="Ada Lovelace",
        student_email="ada@example.com",
        status=SessionStatus.completed,
        link_token="tok",
        invited_at=now(),
        started_at=now() - timedelta(minutes=10),
        completed_at=now(),
    )
    defaults.update(overrides)
    return StudentSession(**defaults)


def _questions(stems: tuple[str, ...] = ("Q1?", "Q2?")) -> list[Question]:
    return [
        Question(question_id=f"q{i}", order=i, stem=s, options=["a", "b", "c", "d"], correct_index=1)
        for i, s in enumerate(stems, start=1)
    ]


_UNSET = object()


def _patch_run(
    monkeypatch,
    *,
    generator=None,
    test=None,
    submission=None,
    session=None,
    questions=None,
    feedback_row=_UNSET,
) -> dict:
    """Enough repo stubs for `run` to complete against a submitted attempt.

    `feedback_row=None` (as opposed to leaving it at the _UNSET default)
    simulates a row that was never written -- i.e. the placeholder create in
    `start` itself failed.
    """
    initial = _feedback(generation_started_at=now()) if feedback_row is _UNSET else feedback_row
    written: dict = {"feedback": initial}
    monkeypatch.setattr(feedback_job, "get_feedback_generator", lambda: generator or _StubGenerator())
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: store.Stored(test or _test_model(), 1))
    monkeypatch.setattr(
        submissions_repo, "get_submission", lambda tid, sid: store.Stored(submission or _submission(), 1)
    )
    monkeypatch.setattr(sessions_repo, "get_session", lambda tid, sid: store.Stored(session or _session(), 1))
    monkeypatch.setattr(tests_repo, "get_questions", lambda tid: questions if questions is not None else _questions())
    monkeypatch.setattr(
        feedback_repo,
        "get_feedback",
        lambda tid, sid: store.Stored(written["feedback"], 1) if written["feedback"] is not None else None,
    )

    def _update(fb, version):
        written["feedback"] = fb
        written["version"] = version
        return version + 1

    def _create(fb):
        written["feedback"] = fb
        written["created"] = True

    monkeypatch.setattr(feedback_repo, "update_feedback", _update)
    monkeypatch.setattr(feedback_repo, "create_feedback", _create)
    return written


# --- run: success, retry, failure -----------------------------------------------


def test_run_success_marks_ready_with_content_and_clears_generation_started_at(monkeypatch):
    generated = _generated(summary="Great job overall.")
    written = _patch_run(monkeypatch, generator=_StubGenerator(generated))

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)

    fb = written["feedback"]
    assert fb.status == FeedbackStatus.ready
    assert fb.content is not None
    assert fb.content.summary == "Great job overall."
    assert fb.content.improvement_areas[0].topic == "Signs"
    assert fb.content.improvement_areas[0].diagnosis == "Chose the positive option instead."
    assert fb.content.study_plan == ["Revisit signs."]
    assert fb.content.topic_breakdown[0].correct == 1
    assert fb.content.topic_breakdown[0].total == 2
    # v1-compat fields stay at their defaults -- v2 never produces them.
    assert fb.content.areas_to_improve == []
    assert fb.content.focus_topics == []
    assert fb.generated_at is not None
    assert fb.generation_started_at is None
    assert fb.error is None


def test_run_passes_the_graded_score_difficulty_and_counts_to_the_generator(monkeypatch):
    stub = _StubGenerator()
    submission = _submission(score=75, correct_count=3, total_questions=4)
    _patch_run(monkeypatch, generator=stub, submission=submission)

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)

    feedback_input = stub.calls[0]
    assert feedback_input.test_title == "Algebra Basics"
    assert feedback_input.difficulty == "medium"
    assert (feedback_input.score, feedback_input.correct_count, feedback_input.total_questions) == (75, 3, 4)
    assert feedback_input.duration_seconds == 900


def test_run_computes_elapsed_seconds_from_the_session(monkeypatch):
    stub = _StubGenerator()
    started_at = now() - timedelta(minutes=12)
    completed_at = started_at + timedelta(minutes=8)
    session = _session(started_at=started_at, completed_at=completed_at)
    _patch_run(monkeypatch, generator=stub, session=session)

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)

    assert stub.calls[0].elapsed_seconds == 8 * 60


def test_run_elapsed_seconds_is_none_when_session_is_missing_timestamps(monkeypatch):
    stub = _StubGenerator()
    session = _session(started_at=None, completed_at=None)
    _patch_run(monkeypatch, generator=stub, session=session)

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)

    assert stub.calls[0].elapsed_seconds is None


def test_run_builds_results_from_questions_sorted_by_order(monkeypatch):
    stub = _StubGenerator()
    questions = [
        Question(question_id="q2", order=2, stem="Second?", options=["a", "b", "c", "d"], correct_index=0),
        Question(question_id="q1", order=1, stem="First?", options=["a", "b", "c", "d"], correct_index=0),
    ]
    submission = _submission(answers={"q1": 0}, per_question={"q1": True, "q2": False})
    _patch_run(monkeypatch, generator=stub, questions=questions, submission=submission)

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)

    results = stub.calls[0].results
    assert [r.order for r in results] == [1, 2]
    q1, q2 = results
    assert q1.options == ["a", "b", "c", "d"]
    assert q1.chosen_index == 0
    assert q1.correct_index == 0
    assert q2.chosen_index is None  # not in submission.answers
    assert q2.correct_index == 0


def test_run_converts_html_stems_to_plain_text(monkeypatch):
    """A model must never be handed markup to describe in prose -- stems are
    passed through app.core.rich_text.rich_text_to_plain first."""
    stub = _StubGenerator()
    questions = [
        Question(
            question_id="q1",
            order=1,
            stem="<p>What is <strong>2 + 2</strong>?</p>",
            options=["a", "b", "c", "d"],
            correct_index=0,
        )
    ]
    submission = _submission(answers={"q1": 0}, per_question={"q1": True})
    _patch_run(monkeypatch, generator=stub, questions=questions, submission=submission)

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)

    results = stub.calls[0].results
    assert results[0].stem == "What is 2 + 2?"
    assert "<" not in results[0].stem


def test_a_transient_failure_is_retried_once(monkeypatch):
    attempts = {"n": 0}

    class _FlakyGenerator(_StubGenerator):
        def generate(self, *args, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise UpstreamError("the model is having a moment")
            return super().generate(*args, **kwargs)

    written = _patch_run(monkeypatch, generator=_FlakyGenerator())

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)

    assert attempts["n"] == 2
    assert written["feedback"].status == FeedbackStatus.ready


def test_two_failures_mark_the_row_failed_with_the_error(monkeypatch):
    class _AlwaysFails(_StubGenerator):
        def generate(self, *args, **kwargs):
            raise UpstreamError("model unavailable")

    written = _patch_run(monkeypatch, generator=_AlwaysFails())

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)

    fb = written["feedback"]
    assert fb.status == FeedbackStatus.failed
    assert "model unavailable" in fb.error
    assert fb.generation_started_at is None


def test_run_never_raises_even_if_the_generator_explodes_unexpectedly(monkeypatch):
    class _Exploding(_StubGenerator):
        def generate(self, *args, **kwargs):
            raise RuntimeError("nobody anticipated this")

    _patch_run(monkeypatch, generator=_Exploding())

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)  # must not raise


def test_run_never_raises_even_if_repos_explode(monkeypatch):
    monkeypatch.setattr(
        tests_repo, "get_test", lambda sub, tid: (_ for _ in ()).throw(RuntimeError("dynamo is down"))
    )

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)  # must not raise


def test_run_with_missing_feedback_item_still_records_terminal_state_via_create(monkeypatch):
    """If the placeholder write in `start` itself failed, there is no row to
    update -- the result must not be lost."""
    written = _patch_run(monkeypatch, feedback_row=None)

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)

    assert written.get("created") is True
    assert written["feedback"].status == FeedbackStatus.ready


def test_run_with_missing_test_flips_the_item_to_failed(monkeypatch):
    written = _patch_run(monkeypatch)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: None)

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)

    fb = written["feedback"]
    assert fb.status == FeedbackStatus.failed
    assert fb.error == "test was deleted"


def test_run_with_missing_submission_logs_and_returns_without_writing(monkeypatch):
    written = _patch_run(monkeypatch)
    monkeypatch.setattr(submissions_repo, "get_submission", lambda tid, sid: None)

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)

    # Unchanged from the placeholder set up by _patch_run -- nothing was written.
    assert written["feedback"].status == FeedbackStatus.generating


def test_run_with_missing_session_logs_and_returns_without_writing(monkeypatch):
    written = _patch_run(monkeypatch)
    monkeypatch.setattr(sessions_repo, "get_session", lambda tid, sid: None)

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)

    # Unchanged from the placeholder set up by _patch_run -- nothing was written.
    assert written["feedback"].status == FeedbackStatus.generating


def test_a_write_conflict_on_finish_is_swallowed(monkeypatch):
    """A concurrent regenerate could have reset the row mid-run; the write
    losing that race must not crash the background task."""
    _patch_run(monkeypatch)

    def _raise_conflict(fb, version):
        raise ConflictError("item was modified concurrently")

    monkeypatch.setattr(feedback_repo, "update_feedback", _raise_conflict)

    feedback_job.run(TEACHER_SUB, TEST_ID, SESSION_ID)  # must not raise


# --- presented() -----------------------------------------------------------------


def test_presented_is_none_when_session_is_not_completed():
    session = _session(status=SessionStatus.started)
    feedback = _feedback(status=FeedbackStatus.ready)

    assert feedback_job.presented(feedback, session) is None


def test_presented_is_synthetic_failed_when_the_row_is_missing():
    session = _session()

    result = feedback_job.presented(None, session)

    assert result is not None
    assert result.status == FeedbackStatus.failed
    assert result.session_id == SESSION_ID
    assert result.test_id == TEST_ID
    assert result.error


def test_presented_generating_past_stale_budget_is_failed():
    stale_start = now() - feedback_job.stale_budget() - timedelta(seconds=1)
    feedback = _feedback(status=FeedbackStatus.generating, generation_started_at=stale_start)

    result = feedback_job.presented(feedback, _session())

    assert result.status == FeedbackStatus.failed
    assert result.error == "feedback generation stopped unexpectedly"
    assert result.generation_started_at is None


def test_presented_generating_within_stale_budget_is_unchanged():
    fresh_start = now() - timedelta(seconds=1)
    feedback = _feedback(status=FeedbackStatus.generating, generation_started_at=fresh_start)

    result = feedback_job.presented(feedback, _session())

    assert result.status == FeedbackStatus.generating
    assert result.generation_started_at == fresh_start


def test_presented_ready_is_unchanged():
    feedback = _feedback(status=FeedbackStatus.ready, generated_at=now())

    result = feedback_job.presented(feedback, _session())

    assert result is feedback
