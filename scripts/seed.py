"""Idempotent dev seed: a demo teacher, one published 3-question test with a
~7-day deadline, two invited students, and one completed attempt so
results/analytics have something to show.

Goes through the same service-layer functions the API routes use
(test_service, student_service, attempt_service) rather than writing repo
items directly, so every invariant those services enforce (draft-only
mutation, deadline-in-the-future, "students never see answers", grading,
etc.) holds for the seeded data exactly as it would for real usage.
teachers_repo.upsert_teacher is the one direct repo call, because there is
no teacher_service -- app/routers/me.py calls it the same way on every
login, and it has no invariants beyond "idempotent upsert". sessions_repo is
used read-only, to hand back each student's link token (see
app/repositories/sessions_repo.py -- link_token deliberately never appears
on any teacher-facing response model, so a service function can't fetch it
back out for us).

Idempotent by re-seed, not by refusal: rerunning finds the demo test by its
fixed title and skips whichever steps already happened (question upload,
publish + invites, the graded attempt) instead of duplicating data or
erroring. This is deliberately not achieved by clearing and recreating
everything, so re-running never disturbs a teacher who has started poking
at the seeded test in the UI.

Guarded to APP_ENV=dev because this seeds the well-known "dev-teacher"
identity (the sub FakeVerifier/FakeAuthProvider produce for the default
"Dev sign in" -- see app/auth/fake.py and web's src/lib/auth/fake.ts), which
must never be a real identity outside local/dev use.
"""

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, ".")

from app.core.clock import now  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.models.session import SessionStatus  # noqa: E402
from app.models.test import Difficulty, TestStatus  # noqa: E402
from app.repositories import sessions_repo, teachers_repo  # noqa: E402
from app.schemas.students import AddStudentsRequest, PublishRequest, StudentInput  # noqa: E402
from app.schemas.take import SubmitRequest  # noqa: E402
from app.schemas.tests import CreateTestRequest, PutQuestionsRequest, QuestionInput  # noqa: E402
from app.services import attempt_service, student_service, test_service  # noqa: E402

TEACHER_SUB = "dev-teacher"
TEACHER_EMAIL = "teacher@local.test"
TEACHER_NAME = "Dev Teacher"

# Fixed title so a rerun can find and reuse this exact test instead of
# creating a new one every time.
TEST_TITLE = "Demo: World Capitals"
QUESTIONS = [
    ("What is the capital of France?", ["Paris", "Lyon", "Marseille", "Nice"], 0),
    ("What is the capital of Japan?", ["Osaka", "Kyoto", "Tokyo", "Nagoya"], 2),
    ("What is the capital of Australia?", ["Sydney", "Melbourne", "Canberra", "Perth"], 2),
]
STUDENTS = [
    ("Alice Demo", "alice.demo@example.com"),
    ("Bob Demo", "bob.demo@example.com"),
]
# Only this student's attempt gets completed, so the analytics/results view
# has one finished and one still-invited session to look at.
GRADED_STUDENT_EMAIL = STUDENTS[0][1]


def _questions_payload() -> PutQuestionsRequest:
    return PutQuestionsRequest(
        questions=[
            QuestionInput(stem=stem, options=options, correct_index=correct)
            for stem, options, correct in QUESTIONS
        ]
    )


def _find_seed_test():
    for summary in test_service.list_tests(TEACHER_SUB):
        if summary.title == TEST_TITLE:
            return summary
    return None


def _ensure_test_with_questions() -> str:
    existing = _find_seed_test()
    if existing is not None:
        if existing.status == TestStatus.draft and existing.question_count < len(QUESTIONS):
            # A previous run created the test but got interrupted before the
            # questions were saved -- draft tests allow replace_questions, so
            # finish that step now instead of leaving a half-seeded test.
            test_service.replace_questions(TEACHER_SUB, existing.test_id, _questions_payload())
            print(f"- test {existing.test_id} existed without questions; added them now")
        else:
            print(f"- reusing existing test {existing.test_id} ({existing.status.value})")
        return existing.test_id

    summary = test_service.create_test(
        TEACHER_SUB,
        CreateTestRequest(title=TEST_TITLE, difficulty=Difficulty.medium, duration_seconds=600),
    )
    test_service.replace_questions(TEACHER_SUB, summary.test_id, _questions_payload())
    print(f"- created test {summary.test_id} with {len(QUESTIONS)} questions")
    return summary.test_id


def _ensure_students(test_id: str) -> None:
    response = student_service.add_students(
        TEACHER_SUB,
        test_id,
        AddStudentsRequest(students=[StudentInput(name=n, email=e) for n, e in STUDENTS]),
    )
    if response.added:
        print(f"- added {len(response.added)} student(s)")
    if response.skipped_emails:
        print(f"- {len(response.skipped_emails)} student(s) already invited, left as-is")


def _ensure_published(test_id: str) -> None:
    detail = test_service.get_test_detail(TEACHER_SUB, test_id)
    if detail.status == TestStatus.published:
        print("- test already published")
        return

    deadline = now() + timedelta(days=7)
    student_service.publish_test(TEACHER_SUB, test_id, PublishRequest(deadline=deadline))
    print(f"- published test, deadline {deadline.isoformat(timespec='minutes')} (invitations sent)")


def _ensure_one_completed_attempt(test_id: str) -> None:
    sessions = sessions_repo.list_sessions(test_id)
    session = next((s for s in sessions if s.student_email == GRADED_STUDENT_EMAIL), None)
    assert session is not None, f"{GRADED_STUDENT_EMAIL} should have been added by _ensure_students"

    if session.status == SessionStatus.completed:
        print(f"- {GRADED_STUDENT_EMAIL} attempt already completed (score {session.score})")
        return

    detail = test_service.get_test_detail(TEACHER_SUB, test_id)
    answers = {
        # First two correct, the rest wrong -- gives the analytics view a
        # mix instead of an all-or-nothing score.
        q.question_id: q.correct_index if i < 2 else (q.correct_index + 1) % len(q.options)
        for i, q in enumerate(detail.questions)
    }

    attempt_service.start_attempt(session.link_token)
    attempt_service.submit_attempt(session.link_token, SubmitRequest(answers=answers))
    print(f"- completed an attempt for {GRADED_STUDENT_EMAIL}")


def _print_summary(test_id: str) -> None:
    settings = get_settings()
    sessions_by_email = {s.student_email: s for s in sessions_repo.list_sessions(test_id)}

    print()
    print("=" * 64)
    print("QuizDeck dev seed ready")
    print("=" * 64)
    print(f"Dashboard:   {settings.frontend_origin}/dashboard")
    print('             sign in with "Dev sign in" (defaults to dev-teacher)')
    print(f"Outbox dir:  {Path(settings.outbox_dir).resolve()}")
    print()
    print("Students:")
    for name, email in STUDENTS:
        session = sessions_by_email[email]
        status = session.status.value
        if session.status == SessionStatus.completed:
            status += f", score {session.score}"
        print(f"  {name} <{email}> [{status}]")
        print(f"    {settings.frontend_origin}/t/{session.link_token}")
    print("=" * 64)


def main() -> None:
    settings = get_settings()
    if settings.app_env != "dev":
        print(f"refusing to seed: APP_ENV={settings.app_env!r} (must be 'dev')", file=sys.stderr)
        raise SystemExit(1)

    teachers_repo.upsert_teacher(TEACHER_SUB, TEACHER_EMAIL, TEACHER_NAME)
    print(f"- teacher ready: {TEACHER_SUB} <{TEACHER_EMAIL}>")

    test_id = _ensure_test_with_questions()
    _ensure_students(test_id)
    _ensure_published(test_id)
    _ensure_one_completed_attempt(test_id)
    _print_summary(test_id)


if __name__ == "__main__":
    main()
