"""Unit tests for results_service: the pure effective_status branches (with
exact boundary checks) and the analytics aggregation math. Both run without
DynamoDB -- the full round trip (teacher A/B ownership, review joins, live
aggregates) is covered by tests/integration/test_results_api.py instead."""

from datetime import timedelta

from app.core.clock import now
from app.core.config import get_settings
from app.models.question import Question
from app.models.session import SessionStatus, StudentSession
from app.models.submission import Submission
from app.models.test import Difficulty, Test, TestStatus
from app.services.results_service import compute_analytics, effective_status


def _test(**overrides) -> Test:
    defaults = dict(
        test_id="01TESTID",
        teacher_sub="dev-alice",
        title="Sample",
        difficulty=Difficulty.easy,
        duration_seconds=600,
        status=TestStatus.published,
        deadline=None,
        question_count=2,
        created_at=now(),
    )
    defaults.update(overrides)
    return Test(**defaults)


def _session(**overrides) -> StudentSession:
    defaults = dict(
        session_id="01SESSION",
        test_id="01TESTID",
        student_name="Ada Lovelace",
        student_email="ada@example.com",
        status=SessionStatus.invited,
        link_token="tok",
        invited_at=now(),
    )
    defaults.update(overrides)
    return StudentSession(**defaults)


# --- effective_status: invited branch ----------------------------------------


def test_invited_stays_invited_when_test_has_no_deadline():
    session = _session(status=SessionStatus.invited)
    test = _test(deadline=None)
    assert effective_status(session, test, now()) == "invited"


def test_invited_stays_invited_before_deadline():
    at = now()
    test = _test(deadline=at + timedelta(days=1))
    session = _session(status=SessionStatus.invited)
    assert effective_status(session, test, at) == "invited"


def test_invited_at_exactly_deadline_is_not_link_expired():
    deadline = now()
    test = _test(deadline=deadline)
    session = _session(status=SessionStatus.invited)
    assert effective_status(session, test, deadline) == "invited"


def test_invited_past_deadline_is_link_expired():
    deadline = now()
    test = _test(deadline=deadline)
    session = _session(status=SessionStatus.invited)
    at = deadline + timedelta(seconds=1)
    assert effective_status(session, test, at) == "link_expired"


# --- effective_status: started branch ----------------------------------------


def test_started_stays_started_before_grace_expires():
    ends_at = now()
    session = _session(
        status=SessionStatus.started, started_at=ends_at - timedelta(minutes=10), ends_at=ends_at
    )
    test = _test()
    assert effective_status(session, test, ends_at) == "started"


def test_started_at_exactly_ends_at_plus_grace_is_not_expired():
    grace = get_settings().submit_grace_seconds
    ends_at = now()
    boundary = ends_at + timedelta(seconds=grace)
    session = _session(
        status=SessionStatus.started, started_at=ends_at - timedelta(minutes=10), ends_at=ends_at
    )
    test = _test()
    assert effective_status(session, test, boundary) == "started"


def test_started_just_past_grace_is_expired():
    grace = get_settings().submit_grace_seconds
    ends_at = now()
    past = ends_at + timedelta(seconds=grace + 1)
    session = _session(
        status=SessionStatus.started, started_at=ends_at - timedelta(minutes=10), ends_at=ends_at
    )
    test = _test()
    assert effective_status(session, test, past) == "expired"


def test_started_without_ends_at_stays_started():
    # Defensive only -- start_attempt always sets ends_at together with
    # status=started, so this should never happen in practice, but the
    # function must not crash on it.
    session = _session(status=SessionStatus.started, ends_at=None)
    test = _test()
    assert effective_status(session, test, now()) == "started"


# --- effective_status: completed branch --------------------------------------


def test_completed_stays_completed_regardless_of_time():
    session = _session(status=SessionStatus.completed, score=80)
    test = _test(deadline=now() - timedelta(days=100))
    assert effective_status(session, test, now() + timedelta(days=100)) == "completed"


# --- compute_analytics --------------------------------------------------------


def _question(question_id: str, order: int) -> Question:
    return Question(
        question_id=question_id, order=order, stem=f"Q{order}?", options=["a", "b", "c", "d"], correct_index=1
    )


def _submission(session_id: str, per_question: dict[str, bool], score: int) -> Submission:
    return Submission(
        session_id=session_id,
        test_id="01TESTID",
        submitted_at=now(),
        answers={qid: (1 if ok else 0) for qid, ok in per_question.items()},
        per_question=per_question,
        score=score,
        correct_count=sum(1 for ok in per_question.values() if ok),
        total_questions=len(per_question),
    )


def test_empty_roster_has_zeroed_stats_and_no_scores():
    result = compute_analytics([], [], [])
    assert result.student_count == 0
    assert result.completed_count == 0
    assert result.completion_rate == 0
    assert result.average_score is None
    assert result.highest_score is None
    assert result.lowest_score is None
    assert result.question_stats == []


def test_no_completions_yields_zero_rates_but_nonzero_roster():
    sessions = [
        _session(session_id="s1", status=SessionStatus.invited),
        _session(session_id="s2", status=SessionStatus.started),
    ]
    questions = [_question("q1", 1)]

    result = compute_analytics(sessions, [], questions)

    assert result.student_count == 2
    assert result.completed_count == 0
    assert result.completion_rate == 0
    assert result.average_score is None
    assert result.highest_score is None
    assert result.lowest_score is None
    assert result.question_stats[0].attempt_count == 0
    assert result.question_stats[0].correct_rate == 0


def test_partial_completion_computes_averages_and_question_rates():
    sessions = [
        _session(session_id="s1", status=SessionStatus.completed, score=100),
        _session(session_id="s2", status=SessionStatus.completed, score=50),
        _session(session_id="s3", status=SessionStatus.invited),
    ]
    questions = [_question("q1", 1), _question("q2", 2)]
    submissions = [
        _submission("s1", {"q1": True, "q2": True}, 100),
        _submission("s2", {"q1": True, "q2": False}, 50),
    ]

    result = compute_analytics(sessions, submissions, questions)

    assert result.student_count == 3
    assert result.completed_count == 2
    assert result.completion_rate == 67  # 2/3 -> 66.67 rounds to 67
    assert result.average_score == 75  # (100 + 50) / 2
    assert result.highest_score == 100
    assert result.lowest_score == 50

    stats_by_id = {qs.question_id: qs for qs in result.question_stats}
    assert stats_by_id["q1"].correct_count == 2
    assert stats_by_id["q1"].attempt_count == 2
    assert stats_by_id["q1"].correct_rate == 100
    assert stats_by_id["q2"].correct_count == 1
    assert stats_by_id["q2"].attempt_count == 2
    assert stats_by_id["q2"].correct_rate == 50


def test_hardest_first_ordering():
    sessions = [_session(session_id="s1", status=SessionStatus.completed, score=67)]
    questions = [_question("easy", 1), _question("hard", 2), _question("medium", 3)]
    submissions = [_submission("s1", {"easy": True, "hard": False, "medium": True}, 67)]

    result = compute_analytics(sessions, submissions, questions)

    # hard (0%) first, then the tie between easy/medium (both 100%) broken
    # by question order (easy=order 1, medium=order 3).
    assert [qs.question_id for qs in result.question_stats] == ["hard", "easy", "medium"]


def test_hardest_first_ordering_ties_break_by_question_order():
    sessions = [_session(session_id="s1", status=SessionStatus.completed, score=0)]
    questions = [_question("q3", 3), _question("q1", 1), _question("q2", 2)]
    # All wrong -- every question ties at correct_rate 0.
    submissions = [_submission("s1", {"q3": False, "q1": False, "q2": False}, 0)]

    result = compute_analytics(sessions, submissions, questions)

    assert [qs.order for qs in result.question_stats] == [1, 2, 3]


def test_rounding_of_completion_rate():
    sessions = [
        _session(session_id="s1", status=SessionStatus.completed, score=100),
        _session(session_id="s2", status=SessionStatus.invited),
        _session(session_id="s3", status=SessionStatus.invited),
    ]
    questions = [_question("q1", 1)]
    submissions = [_submission("s1", {"q1": True}, 100)]

    result = compute_analytics(sessions, submissions, questions)

    assert result.completion_rate == 33  # 1/3 -> 33.33 rounds to 33
