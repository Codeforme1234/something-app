"""Business rules for manual test authoring.

Mutation is draft-only (a published test is frozen — publishing itself is a
later phase); `question_count` is denormalized onto the test meta whenever
questions are replaced so the dashboard list needs no extra query.
"""

import logging
from datetime import timedelta

from app.core.clock import now
from app.core.config import get_settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    InsufficientAiCreditsError,
    InsufficientCreditsError,
    NotFoundError,
)
from app.core.ids import new_ulid
from app.core.rich_text import rich_text_to_plain
from app.llm import get_mcq_generator
from app.models.company import Company
from app.models.question import Question
from app.models.test import Test, TestStatus
from app.repositories import companies_repo, store, teachers_repo, tests_repo
from app.schemas.tests import (
    CreateTestRequest,
    GenerateQuestionsRequest,
    GenerateQuestionsResponse,
    GeneratedQuestion,
    PutQuestionsRequest,
    QuestionImageUploadResponse,
    TestDetail,
    TestSummary,
    UpdateTestRequest,
)
from app.services import ai_credits
from app.services.storage import get_object_store, question_image_url
from app.services.storage import keys as storage_keys
from app.services.storage.signatures import matches_declared_type

logger = logging.getLogger(__name__)


def _get_owned_test(teacher_sub: str, test_id: str) -> store.Stored[Test]:
    stored = tests_repo.get_test(teacher_sub, test_id)
    if stored is None:
        raise NotFoundError("test not found")
    return stored


def _require_draft(test: Test) -> None:
    if test.status != TestStatus.draft:
        raise ConflictError("test is no longer a draft")


#: Grace on top of the extraction timeout before a `generating` test is presumed
#: dead. Generous on purpose: showing "failed" for a run that is merely slow is
#: worse than showing "generating" a few minutes too long.
STALE_GENERATION_MARGIN = timedelta(minutes=5)


def _presented(test: Test) -> Test:
    """A test as the API should describe it right now.

    Background generation runs in the process that served the request, so a
    restart mid-run leaves the row in `generating` with nobody left to finish
    it. There is no scheduler to reap those, so staleness is derived on read --
    the same approach results_service.effective_status takes for a session whose
    deadline has quietly passed. Nothing is written back: the next successful
    run, or a delete, is what actually clears the row.
    """
    if test.status != TestStatus.generating or test.generation_started_at is None:
        return test
    budget = (
        timedelta(seconds=get_settings().openai_extraction_timeout_seconds)
        + STALE_GENERATION_MARGIN
    )
    if now() - test.generation_started_at <= budget:
        return test
    return test.model_copy(
        update={
            "status": TestStatus.generation_failed,
            "generation_error": "generation stopped unexpectedly; no credits were charged",
        }
    )


def get_teacher_and_company(teacher_sub: str) -> tuple[str, store.Stored[Company]]:
    """Resolve the caller's company, checked fresh -- shared by every path
    that's about to spend a credit, so a re-check right before committing
    always sees the current balance rather than a stale one."""
    teacher = teachers_repo.get_teacher(teacher_sub)
    if teacher is None or not teacher.company_id:
        # Unreachable in practice: GET /me provisions the company on every
        # login, before a teacher can ever reach a credit-spending action.
        raise ConflictError("admin has no company assigned yet")

    company_stored = companies_repo.get_company(teacher.company_id)
    if company_stored is None:
        raise NotFoundError("company not found")
    return teacher.company_id, company_stored


def require_credits(company: Company, ai_cost: int) -> None:
    """Both pools, checked together so a run that cannot pay never starts.

    The test-creation credit is reported first because it is the one every test
    needs; a teacher with neither should fix that one first.
    """
    if company.credit_balance < 1:
        raise InsufficientCreditsError("not enough credits to generate a test")
    left = ai_credits.available(company)
    if left < ai_cost:
        raise InsufficientAiCreditsError(
            f"this run needs {ai_cost} AI credit{'' if ai_cost == 1 else 's'}, "
            f"and {left} {'is' if left == 1 else 'are'} left"
        )


def prompt_guidelines(payload: GenerateQuestionsRequest) -> str | None:
    """The teacher's guidelines as plain text for the prompt.

    Stored and validated as a sanitized Tiptap fragment, but a model should never
    be handed HTML -- and a naive tag strip would flatten a bulleted list into one
    run-on instruction, so rich_text_to_plain preserves the structure.
    """
    if not payload.guidelines:
        return None
    return rich_text_to_plain(payload.guidelines) or None


def create_test(teacher_sub: str, payload: CreateTestRequest) -> TestSummary:
    """Creating a test spends one credit from the admin's company, atomically
    with the test itself (app.repositories.tests_repo.create_test_and_spend_credit)
    -- a crash or a losing race on the company's version can never produce a
    free test or a spent credit with no test to show for it."""
    company_id, company_stored = get_teacher_and_company(teacher_sub)
    if company_stored.model.credit_balance < 1:
        raise InsufficientCreditsError("not enough credits to create a test")

    test = Test(
        test_id=new_ulid(),
        teacher_sub=teacher_sub,
        company_id=company_id,
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
    return [TestSummary.from_model(_presented(t)) for t in tests_repo.list_tests(teacher_sub)]


def get_test_detail(teacher_sub: str, test_id: str) -> TestDetail:
    stored = _get_owned_test(teacher_sub, test_id)
    questions = tests_repo.get_questions(test_id)
    return TestDetail.from_models(_presented(stored.model), questions, question_image_url)


def update_test(teacher_sub: str, test_id: str, payload: UpdateTestRequest) -> TestSummary:
    stored = _get_owned_test(teacher_sub, test_id)
    _require_draft(stored.model)
    updated = stored.model.model_copy(update=payload.model_dump(exclude_unset=True))
    tests_repo.update_test(teacher_sub, updated, stored.version)
    return TestSummary.from_model(updated)


def delete_test(teacher_sub: str, test_id: str) -> None:
    stored = _get_owned_test(teacher_sub, test_id)
    # A failed run is deletable as well as a draft: _require_draft alone would
    # leave a teacher permanently unable to clear a card for a test that never
    # produced anything. A `generating` one is still refused -- a run is in
    # flight and would write questions back under a deleted test -- but
    # _presented() turns a dead one into generation_failed, so nothing is stuck.
    presented = _presented(stored.model)
    if presented.status not in (TestStatus.draft, TestStatus.generation_failed):
        raise ConflictError("test is no longer a draft")

    # Read the keys before the rows are gone, but sweep storage only after the
    # DynamoDB delete succeeds -- and never let a storage failure fail the
    # teacher's request. An orphaned blob is a disk-usage problem; a delete that
    # 500s after the rows are already gone is a broken UI.
    image_keys = [q.image_key for q in tests_repo.get_questions(test_id) if q.image_key]
    tests_repo.delete_test(teacher_sub, test_id)
    if image_keys:
        try:
            get_object_store().delete_many(image_keys)
        except Exception:
            logger.warning("failed to delete %d image(s) for test %s", len(image_keys), test_id)


def replace_questions(teacher_sub: str, test_id: str, payload: PutQuestionsRequest) -> TestDetail:
    stored = _get_owned_test(teacher_sub, test_id)
    _require_draft(stored.model)

    # An image_key is a path fragment the client hands back, and it ends up in a
    # URL we render. Pin every one to this test before storing it: that rules out
    # traversal, key injection, and pointing a question at another test's image.
    for q in payload.questions:
        if q.image_key is not None and not storage_keys.belongs_to_test(q.image_key, test_id):
            raise BadRequestError("image does not belong to this test")

    questions = [
        Question(
            question_id=new_ulid(),
            order=order,
            stem=q.stem,
            options=q.options,
            correct_index=q.correct_index,
            image_key=q.image_key,
            image_alt=q.image_alt,
        )
        for order, q in enumerate(payload.questions, start=1)
    ]
    tests_repo.replace_questions(test_id, questions)

    updated_test = stored.model.model_copy(update={"question_count": len(questions)})
    tests_repo.update_test(teacher_sub, updated_test, stored.version)

    return TestDetail.from_models(updated_test, questions, question_image_url)


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
        payload.topic,
        payload.effective_count,
        payload.difficulty,
        payload.knowledge_base,
        prompt_guidelines(payload),
    )

    return GenerateQuestionsResponse(
        questions=[
            GeneratedQuestion(stem=q.stem, options=q.options, correct_index=q.correct_index)
            for q in generated
        ]
    )


def upload_question_image(
    teacher_sub: str, test_id: str, content_type: str | None, data: bytes
) -> QuestionImageUploadResponse:
    """Store one image and hand back the key + URL for the editor to attach.

    The image is not associated with a question here: the key is minted against
    the *test*, and the editor sends it back inside a QuestionInput on the next
    PUT /questions. That indirection is deliberate -- replace_questions re-mints
    every question_id on every save, so a key derived from question_id would be
    orphaned the first time a teacher hit Save.
    """
    stored = _get_owned_test(teacher_sub, test_id)
    _require_draft(stored.model)
    return store_question_image(test_id, content_type, data)


def store_question_image(
    test_id: str, content_type: str | None, data: bytes
) -> QuestionImageUploadResponse:
    """Validate and store one image, with no ownership or status check.

    Split out of upload_question_image so app/services/generation_job.py can
    store a figure it cropped from a PDF while the test is still `generating` --
    a state _require_draft rightly rejects for anything a teacher does by hand.
    Callers reached from HTTP must go through upload_question_image; this one
    trusts its test_id, so never pass it one that came off a request.
    """
    if content_type not in storage_keys.CONTENT_TYPE_EXTENSIONS:
        raise BadRequestError("unsupported image type; use PNG, JPEG or WebP")
    if not data:
        raise BadRequestError("image file is empty")
    # The declared type is just a claim by the client. This is the check that
    # makes the same-origin serve route safe (app/routers/images.py): without
    # it, HTML labelled image/png would be stored and later served from the
    # origin the bearer token lives on.
    if not matches_declared_type(data, content_type):
        raise BadRequestError("file content does not match its image type")

    key = storage_keys.new_question_image_key(test_id, content_type)
    store_ = get_object_store()
    store_.put_bytes(key, data, content_type)
    # public_url rather than question_image_url: the key is known non-empty here,
    # so the None-handling wrapper would only obscure the return type.
    return QuestionImageUploadResponse(image_key=key, image_url=store_.public_url(key))
