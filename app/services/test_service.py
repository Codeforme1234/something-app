"""Business rules for manual test authoring.

Mutation is draft-only (a published test is frozen — publishing itself is a
later phase); `question_count` is denormalized onto the test meta whenever
questions are replaced so the dashboard list needs no extra query.
"""

from app.core.clock import now
from app.core.exceptions import ConflictError, InsufficientCreditsError, NotFoundError
from app.core.ids import new_ulid
from app.llm import get_mcq_generator
from app.models.question import Question
from app.models.test import Test, TestStatus
from app.repositories import companies_repo, store, teachers_repo, tests_repo
from app.schemas.tests import (
    CreateTestRequest,
    GenerateQuestionsRequest,
    GenerateQuestionsResponse,
    GeneratedQuestion,
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
    """Creating a test spends one credit from the admin's company, atomically
    with the test itself (app.repositories.tests_repo.create_test_and_spend_credit)
    -- a crash or a losing race on the company's version can never produce a
    free test or a spent credit with no test to show for it."""
    teacher = teachers_repo.get_teacher(teacher_sub)
    if teacher is None or not teacher.company_id:
        # Unreachable in practice: GET /me provisions the company on every
        # login, before a teacher can ever reach the "create test" button.
        raise ConflictError("admin has no company assigned yet")

    company_stored = companies_repo.get_company(teacher.company_id)
    if company_stored is None:
        raise NotFoundError("company not found")
    if company_stored.model.credit_balance < 1:
        raise InsufficientCreditsError("not enough credits to create a test")

    test = Test(
        test_id=new_ulid(),
        teacher_sub=teacher_sub,
        company_id=teacher.company_id,
        title=payload.title,
        difficulty=payload.difficulty,
        duration_seconds=payload.duration_seconds,
        status=TestStatus.draft,
        created_at=now(),
    )
    debited_company = company_stored.model.model_copy(
        update={"credit_balance": company_stored.model.credit_balance - 1}
    )
    tests_repo.create_test_and_spend_credit(test, debited_company, company_stored.version)
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


def generate_questions(
    teacher_sub: str, test_id: str, payload: GenerateQuestionsRequest
) -> GenerateQuestionsResponse:
    """Draft-only, like every other mutation guard in this module -- but this
    one writes nothing at all. The generated questions are handed back for
    the teacher to review/edit in the question editor and save via the
    ordinary PUT /questions path."""
    stored = _get_owned_test(teacher_sub, test_id)
    _require_draft(stored.model)

    generated = get_mcq_generator().generate(
        payload.topic, payload.count, payload.difficulty, payload.knowledge_base
    )

    return GenerateQuestionsResponse(
        questions=[
            GeneratedQuestion(stem=q.stem, options=q.options, correct_index=q.correct_index)
            for q in generated
        ]
    )
