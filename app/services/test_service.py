"""Business rules for manual test authoring.

Mutation is draft-only (a published test is frozen — publishing itself is a
later phase); `question_count` is denormalized onto the test meta whenever
questions are replaced so the dashboard list needs no extra query.
"""

from app.core.clock import now
from app.core.exceptions import ConflictError, NotFoundError
from app.core.ids import new_ulid
from app.models.question import Question
from app.models.test import Test, TestStatus
from app.repositories import store, tests_repo
from app.schemas.tests import (
    CreateTestRequest,
    PutQuestionsRequest,
    TestDetail,
    TestSummary,
    UpdateTestRequest,
)


def _get_owned_test(teacher_sub: str, test_id: str) -> store.Stored[Test]:
    stored = tests_repo.get_test(teacher_sub, test_id)
    if stored is None:
        raise NotFoundError("test not found")
    return stored


def _require_draft(test: Test) -> None:
    if test.status != TestStatus.draft:
        raise ConflictError("test is no longer a draft")


def create_test(teacher_sub: str, payload: CreateTestRequest) -> TestSummary:
    test = Test(
        test_id=new_ulid(),
        teacher_sub=teacher_sub,
        title=payload.title,
        difficulty=payload.difficulty,
        duration_seconds=payload.duration_seconds,
        status=TestStatus.draft,
        created_at=now(),
    )
    tests_repo.create_test(test)
    return TestSummary.from_model(test)


def list_tests(teacher_sub: str) -> list[TestSummary]:
    return [TestSummary.from_model(t) for t in tests_repo.list_tests(teacher_sub)]


def get_test_detail(teacher_sub: str, test_id: str) -> TestDetail:
    stored = _get_owned_test(teacher_sub, test_id)
    questions = tests_repo.get_questions(test_id)
    return TestDetail.from_models(stored.model, questions)


def update_test(teacher_sub: str, test_id: str, payload: UpdateTestRequest) -> TestSummary:
    stored = _get_owned_test(teacher_sub, test_id)
    _require_draft(stored.model)
    updated = stored.model.model_copy(update=payload.model_dump(exclude_unset=True))
    tests_repo.update_test(teacher_sub, updated, stored.version)
    return TestSummary.from_model(updated)


def delete_test(teacher_sub: str, test_id: str) -> None:
    stored = _get_owned_test(teacher_sub, test_id)
    _require_draft(stored.model)
    tests_repo.delete_test(teacher_sub, test_id)


def replace_questions(teacher_sub: str, test_id: str, payload: PutQuestionsRequest) -> TestDetail:
    stored = _get_owned_test(teacher_sub, test_id)
    _require_draft(stored.model)

    questions = [
        Question(
            question_id=new_ulid(),
            order=order,
            stem=q.stem,
            options=q.options,
            correct_index=q.correct_index,
        )
        for order, q in enumerate(payload.questions, start=1)
    ]
    tests_repo.replace_questions(test_id, questions)

    updated_test = stored.model.model_copy(update={"question_count": len(questions)})
    tests_repo.update_test(teacher_sub, updated_test, stored.version)

    return TestDetail.from_models(updated_test, questions)
