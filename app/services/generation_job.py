"""The "Generate with AI" workflow, split across the HTTP boundary.

Extraction of a real paper takes minutes -- a 75-question PDF measured 211s --
so it cannot happen inside the request. `start` does everything that must be
synchronous (validate, price, debit, create the row) and returns a test in
`generating`; `run` does the slow part afterwards and flips the status.

Two things this file is careful about, both learned from the physics-mock bug
that prompted it:

  - A PDF is EXTRACTED, not generated from. The count comes from the paper's own
    numbering, never from `payload.count`. Baking in a default is exactly how a
    14-question paper came back with 10 unrelated questions.
  - Figures are cropped and stored, so a question that refers to a diagram
    actually has one.
"""

import logging
from datetime import timedelta

from app.core.clock import now
from app.core.config import get_settings
from app.core.exceptions import BadRequestError, UpstreamError
from app.core.ids import new_ulid
from app.core.rich_text import sanitize_rich_text
from app.llm import get_mcq_generator, get_question_extractor
from app.llm.extraction_schemas import ExtractedQuestion
from app.models.question import Question
from app.models.test import Test, TestStatus
from app.repositories import companies_repo, tests_repo
from app.schemas.tests import GenerateQuestionsRequest, TestSummary
from app.services import ai_credits, generation_pipeline, knowledge_base, test_service
from app.services.storage import keys as storage_keys

logger = logging.getLogger(__name__)

#: Content type of the only source document we can extract questions FROM.
#: Everything else the knowledge base accepts (images, txt, md) has no question
#: structure to parse, so it falls back to generation -- see _resolve_plan.
PDF_CONTENT_TYPE = "application/pdf"

#: How many times the whole pipeline is attempted. The second attempt exists
#: because the common failures here -- a timeout, a 5xx, a model returning
#: something unparseable twice -- are transient. It is deliberately NOT applied
#: to BadRequestError: a corrupt PDF is corrupt on the retry too, and retrying
#: only spends the teacher's credit twice to reach the same answer.
MAX_ATTEMPTS = 2


def start(teacher_sub: str, payload: GenerateQuestionsRequest) -> TestSummary:
    """Reserve the test and the credits, synchronously.

    Both pools are debited here, before any model call, because the teacher
    asked for the run: the card and the balance change together the moment they
    click. If the run then fails twice, `run` refunds -- see _fail.

    Everything expensive is deliberately left to `run`, so this stays fast
    enough to be a normal request.
    """
    # The key prices the run and, from here on, is also read for its bytes.
    # Pinning it to the caller's own namespace is what stops one teacher
    # extracting from another's uploaded paper.
    if payload.knowledge_base_key and not storage_keys.kb_belongs_to_teacher(
        payload.knowledge_base_key, teacher_sub
    ):
        raise BadRequestError("that source file does not belong to you")

    ai_cost = ai_credits.cost(ai_credits.mode_for(payload))
    company_id, company_stored = test_service.get_teacher_and_company(teacher_sub)
    test_service.require_credits(company_stored.model, ai_cost)

    test = Test(
        test_id=new_ulid(),
        teacher_sub=teacher_sub,
        company_id=company_id,
        title=payload.topic[:200],
        difficulty=payload.difficulty,
        duration_seconds=900,
        status=TestStatus.generating,
        # Unknown until the run finishes. A PDF's real count comes from the
        # paper; a prompt run's from _resolve_count.
        question_count=0,
        created_at=now(),
        generation_started_at=now(),
    )
    debited = ai_credits.debited(company_stored.model, ai_credits=ai_cost)
    # One transaction: the row and the debit land together or neither does, so
    # a crash here can never leave a paid-for test that does not exist, or a
    # test nobody paid for.
    tests_repo.create_test_and_spend_credit(test, debited, company_stored.version)
    logger.info(
        "generation started test=%s mode=%s ai_cost=%d", test.test_id, ai_credits.mode_for(payload), ai_cost
    )
    return TestSummary.from_model(test)


def run(teacher_sub: str, test_id: str, payload: GenerateQuestionsRequest) -> None:
    """The slow half. Never raises: it is a background task, so there is nobody
    left to return an error to -- every failure is recorded on the test."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            questions, figures = _produce(teacher_sub, payload)
            _finish(teacher_sub, test_id, questions, figures)
            logger.info(
                "generation finished test=%s questions=%d figures=%d attempt=%d",
                test_id,
                len(questions),
                len(figures),
                attempt,
            )
            return
        except BadRequestError as exc:
            # Permanent by construction: an unreadable PDF reads the same way
            # next time. Fail immediately rather than bill a second attempt.
            logger.warning("generation rejected test=%s: %s", test_id, exc)
            _fail(teacher_sub, test_id, payload, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 -- a background task swallows everything
            last_error = exc
            logger.warning(
                "generation attempt %d/%d failed test=%s: %s", attempt, MAX_ATTEMPTS, test_id, exc
            )

    message = str(last_error) if isinstance(last_error, UpstreamError) else "generation failed"
    _fail(teacher_sub, test_id, payload, message)


def _produce(
    teacher_sub: str, payload: GenerateQuestionsRequest
) -> tuple[list[ExtractedQuestion] | list[Question], list[generation_pipeline.FigureAttachment]]:
    """Extract from a PDF, or generate. Returns questions already in Question
    form plus any figures to store."""
    source = _load_pdf(teacher_sub, payload)
    if source is not None:
        outcome = generation_pipeline.run_extraction(
            pdf_bytes=source,
            extractor=get_question_extractor(),
            instruction=test_service.prompt_guidelines(payload),
            max_pages=get_settings().max_pdf_pages,
            progress=lambda m: logger.info("extraction: %s", m),
        )
        logger.info(
            "extracted %d of %d question(s) the paper numbers, %d figure(s)",
            len(outcome.questions),
            outcome.expected_count,
            len(outcome.figures),
        )
        return _questions_from_extraction(outcome.questions), outcome.figures

    generated = get_mcq_generator().generate(
        payload.topic,
        payload.effective_count,
        payload.difficulty,
        payload.knowledge_base,
        test_service.prompt_guidelines(payload),
    )
    questions = [
        Question(
            question_id=new_ulid(),
            order=order,
            stem=q.stem,
            options=q.options,
            correct_index=q.correct_index,
        )
        for order, q in enumerate(generated, start=1)
    ]
    return questions, []


def _load_pdf(teacher_sub: str, payload: GenerateQuestionsRequest) -> bytes | None:
    """The uploaded PDF's bytes, or None when this is not a PDF run.

    read_stored re-checks the key against the caller, so a key that slipped past
    `start` still cannot read someone else's document.
    """
    key = payload.knowledge_base_key
    if not key or storage_keys.kb_content_type_for_key(key) != PDF_CONTENT_TYPE:
        return None
    data, _content_type = knowledge_base.read_stored(teacher_sub, key)
    return data


def _questions_from_extraction(extracted: list[ExtractedQuestion]) -> list[Question]:
    """Wrap each extracted stem as the HTML fragment the editor and take page
    expect (CLAUDE.md rule 9).

    Deliberately no manual escaping, matching scripts/seed_from_pdf._as_rich_text:
    ExtractedQuestion's own validator has already run sanitize_rich_text, so a
    stray `<` in extracted maths is already `&lt;`. Escaping again here would
    render it to the student as the literal text "&lt;".

    `order` is the paper's own question number, not an enumerate() counter, so a
    figure keyed by question number still lines up when extraction drops a
    question it could not read.
    """
    return [
        Question(
            question_id=new_ulid(),
            order=q.number,
            stem=sanitize_rich_text(f"<p>{q.stem}</p>", "stem", max_visible_chars=1000),
            options=q.options,
            correct_index=q.correct_index,
            # Filled in by _finish once the figure has been stored and has a key.
            image_key=None,
        )
        for q in extracted
    ]


def _finish(
    teacher_sub: str,
    test_id: str,
    questions: list[Question],
    figures: list[generation_pipeline.FigureAttachment],
) -> None:
    """Store figures, persist questions, flip the test to draft."""
    if figures:
        # Extraction numbers figures by the paper's question number, which is
        # 1-based and matches the `order` assigned above.
        by_order = {f.question_number: f for f in figures}
        for question in questions:
            figure = by_order.get(question.order)
            if figure is None:
                continue
            uploaded = test_service.store_question_image(test_id, "image/png", figure.png)
            question.image_key = uploaded.image_key
            question.image_alt = f"Figure for question {question.order}"

    tests_repo.replace_questions(test_id, questions)

    stored = tests_repo.get_test(teacher_sub, test_id)
    if stored is None:
        # The teacher deleted the test while the run was in flight. The
        # questions just written are orphaned rows under a test that no longer
        # exists; leaving them is harmless and cheaper than a second sweep.
        logger.warning("generated test %s vanished before it could be finished", test_id)
        return
    finished = stored.model.model_copy(
        update={
            "status": TestStatus.draft,
            "question_count": len(questions),
            "generation_started_at": None,
        }
    )
    tests_repo.update_test(teacher_sub, finished, stored.version)


def _fail(
    teacher_sub: str, test_id: str, payload: GenerateQuestionsRequest, message: str
) -> None:
    """Record the failure and give the credits back.

    Refunding is why this is careful about versions: the company update is
    guarded by the version read here, so two concurrent refunds cannot both
    land and hand back twice what was taken.
    """
    ai_cost = ai_credits.cost(ai_credits.mode_for(payload))
    try:
        _, company_stored = test_service.get_teacher_and_company(teacher_sub)
        refunded = ai_credits.refunded(company_stored.model, ai_credits=ai_cost)
        companies_repo.update_company(refunded, company_stored.version)
        logger.info("refunded test_credits=1 ai_credits=%d after failed test=%s", ai_cost, test_id)
    except Exception:
        # A refund that fails must not also lose the failure message -- the
        # teacher needs to see *something* on the card either way.
        logger.exception("could not refund credits for failed test %s", test_id)

    stored = tests_repo.get_test(teacher_sub, test_id)
    if stored is None:
        return
    tests_repo.update_test(
        teacher_sub,
        stored.model.model_copy(
            update={
                "status": TestStatus.generation_failed,
                "generation_error": message[:500],
                "generation_started_at": None,
            }
        ),
        stored.version,
    )


