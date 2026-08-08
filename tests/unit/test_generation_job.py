"""Unit tests for app/services/generation_job.py.

The job is split across the HTTP boundary, and the split is the point: `start`
must debit and create synchronously, `run` must never raise, and a run that
fails twice must give the credits back. Repositories and the LLM are
monkeypatched, so nothing here touches DynamoDB, S3 or OpenAI.
"""

import pytest

from app.core.clock import now
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    InsufficientAiCreditsError,
    InsufficientCreditsError,
    UpstreamError,
)
from app.llm.extraction_schemas import ExtractedQuestion
from app.llm.schemas import GeneratedMCQ
from app.models.company import Company
from app.models.teacher import Teacher
from app.models.test import Difficulty, Test, TestStatus
from app.repositories import companies_repo, store, teachers_repo, tests_repo
from app.schemas.tests import GenerateQuestionsRequest
from app.services import ai_credits, generation_job, generation_pipeline, test_service
from app.services.storage import keys as storage_keys


def _teacher(company_id: str | None) -> Teacher:
    return Teacher(
        sub="dev-alice", email="a@x.com", name="Alice", company_id=company_id, created_at=now()
    )


def _company(credit_balance: int, ai_credit_balance: int | None = 20) -> Company:
    return Company(
        company_id="COMP1",
        name="Alice's company",
        credit_balance=credit_balance,
        ai_credit_balance=ai_credit_balance,
        created_at=now(),
    )


def _payload(**overrides) -> GenerateQuestionsRequest:
    defaults = {"topic": "Photosynthesis", "difficulty": Difficulty.medium}
    defaults.update(overrides)
    return GenerateQuestionsRequest(**defaults)


class _StubGenerator:
    def __init__(self, questions=None):
        self.questions = questions or [
            GeneratedMCQ(stem="Q1?", options=["A", "B", "C", "D"], correct_index=0)
        ]
        self.calls: list[tuple] = []

    def generate(self, topic, count, difficulty, knowledge_base=None, guidelines=None):
        self.calls.append((topic, count, difficulty, knowledge_base, guidelines))
        return self.questions


def _patch_start(monkeypatch, company: Company) -> dict:
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: _teacher("COMP1"))
    monkeypatch.setattr(companies_repo, "get_company", lambda cid: store.Stored(company, 1))
    spent: dict = {}
    monkeypatch.setattr(
        tests_repo,
        "create_test_and_spend_credit",
        lambda test, comp, version: spent.update(test=test, company=comp, version=version)
        or version + 1,
    )
    return spent


# --- start: what must happen inside the request -------------------------------


def test_start_returns_a_generating_test_with_no_questions_yet(monkeypatch):
    _patch_start(monkeypatch, _company(5))

    summary = generation_job.start("dev-alice", _payload(topic="Optics"))

    assert summary.status is TestStatus.generating
    assert summary.question_count == 0
    assert summary.title == "Optics"


def test_start_never_calls_the_model(monkeypatch):
    """The whole point of the split: the request returns before any spend of
    wall-clock time on OpenAI."""
    _patch_start(monkeypatch, _company(5))

    def _boom():
        raise AssertionError("start must not reach the generator")

    monkeypatch.setattr(generation_job, "get_mcq_generator", _boom)

    generation_job.start("dev-alice", _payload())


def test_start_debits_both_pools_in_a_single_company_write(monkeypatch):
    """The atomicity guarantee: one item, one write, so a crash can never spend
    one pool without the other."""
    spent = _patch_start(monkeypatch, _company(5, ai_credit_balance=9))

    generation_job.start("dev-alice", _payload())

    assert (spent["company"].credit_balance, spent["company"].ai_credit_balance) == (4, 8)


def test_a_document_run_costs_more_ai_credits(monkeypatch):
    spent = _patch_start(monkeypatch, _company(5, ai_credit_balance=9))

    generation_job.start("dev-alice", _payload(knowledge_base="Some source text"))

    assert spent["company"].credit_balance == 4  # still exactly one
    assert spent["company"].ai_credit_balance == 7  # 9 - 2 (document mode)


def test_a_key_alone_also_counts_as_document_mode(monkeypatch):
    """Mode is derived from the request, never declared, so a caller cannot claim
    the cheaper price while attaching a document."""
    spent = _patch_start(monkeypatch, _company(5, ai_credit_balance=9))
    own_key = storage_keys.new_knowledge_base_key("dev-alice", "application/pdf")

    generation_job.start("dev-alice", _payload(knowledge_base_key=own_key))

    assert spent["company"].ai_credit_balance == 7


def test_start_records_when_the_run_began(monkeypatch):
    """Without this there is no way to tell a slow run from a dead one --
    see test_service._presented."""
    spent = _patch_start(monkeypatch, _company(5))

    generation_job.start("dev-alice", _payload())

    assert spent["test"].generation_started_at is not None


@pytest.mark.parametrize(
    ("company", "expected"),
    [
        (_company(0), InsufficientCreditsError),
        (_company(5, ai_credit_balance=0), InsufficientAiCreditsError),
        (_company(5, ai_credit_balance=None), InsufficientAiCreditsError),
    ],
)
def test_start_refuses_without_credits(monkeypatch, company, expected):
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: _teacher("COMP1"))
    monkeypatch.setattr(companies_repo, "get_company", lambda cid: store.Stored(company, 1))
    monkeypatch.setattr(
        tests_repo,
        "create_test_and_spend_credit",
        lambda *a: pytest.fail("must not create a test without credits"),
    )

    with pytest.raises(expected):
        generation_job.start("dev-alice", _payload())


def test_start_without_a_company_raises_conflict(monkeypatch):
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: _teacher(None))

    with pytest.raises(ConflictError):
        generation_job.start("dev-alice", _payload())


def test_a_foreign_knowledge_base_key_is_rejected(monkeypatch):
    _patch_start(monkeypatch, _company(5))
    foreign = storage_keys.new_knowledge_base_key("dev-mallory", "application/pdf")

    with pytest.raises(BadRequestError):
        generation_job.start("dev-alice", _payload(knowledge_base_key=foreign))


def test_the_callers_own_knowledge_base_key_is_accepted(monkeypatch):
    _patch_start(monkeypatch, _company(5))
    own = storage_keys.new_knowledge_base_key("dev-alice", "application/pdf")

    generation_job.start("dev-alice", _payload(knowledge_base_key=own))


# --- count resolution ---------------------------------------------------------
#
# The bug that prompted all of this: a 14-question paper came back with 10
# unrelated questions, because `count` defaulted to 10 and the PDF was never
# extracted from at all.


def _patch_run(monkeypatch, generator=None) -> dict:
    """Enough repo stubs for `run` to complete against a generating test."""
    written: dict = {}
    test = Test(
        test_id="01TESTID",
        teacher_sub="dev-alice",
        title="T",
        difficulty=Difficulty.medium,
        duration_seconds=900,
        status=TestStatus.generating,
        created_at=now(),
        generation_started_at=now(),
    )
    monkeypatch.setattr(generation_job, "get_mcq_generator", lambda: generator or _StubGenerator())
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: store.Stored(test, 1))
    monkeypatch.setattr(
        tests_repo, "replace_questions", lambda tid, qs: written.update(questions=qs)
    )
    monkeypatch.setattr(
        tests_repo, "update_test", lambda sub, t, v: written.update(test=t) or v + 1
    )
    return written


def test_no_count_given_falls_back_to_ten(monkeypatch):
    stub = _StubGenerator()
    _patch_run(monkeypatch, generator=stub)

    generation_job.run("dev-alice", "01TESTID", _payload())

    assert stub.calls[0][1] == 10


def test_an_explicit_count_is_honoured(monkeypatch):
    stub = _StubGenerator()
    _patch_run(monkeypatch, generator=stub)

    generation_job.run("dev-alice", "01TESTID", _payload(count=7))

    assert stub.calls[0][1] == 7


def test_a_pdf_run_extracts_and_ignores_count(monkeypatch):
    """The count comes from the paper. `count=3` here is a trap: honouring it
    would be the original bug."""
    written = _patch_run(monkeypatch)
    key = storage_keys.new_knowledge_base_key("dev-alice", "application/pdf")
    monkeypatch.setattr(
        generation_job.knowledge_base, "read_stored", lambda sub, k: (b"%PDF-fake", "application/pdf")
    )

    def _fail_generator():
        raise AssertionError("a PDF must be extracted, never generated from")

    monkeypatch.setattr(generation_job, "get_mcq_generator", _fail_generator)
    monkeypatch.setattr(generation_job, "get_question_extractor", lambda: object())
    monkeypatch.setattr(
        generation_pipeline,
        "run_extraction",
        lambda **kw: generation_pipeline.ExtractionOutcome(
            questions=[
                ExtractedQuestion(
                    number=n,
                    stem=f"Paper Q{n}",
                    options=["A", "B", "C", "D"],
                    correct_index=0,
                    source_page=1,
                )
                for n in range(1, 15)
            ],
            expected_count=14,
            figures=[],
            page_count=3,
        ),
    )

    generation_job.run("dev-alice", "01TESTID", _payload(count=3, knowledge_base_key=key))

    assert len(written["questions"]) == 14
    assert written["test"].question_count == 14


def test_a_non_pdf_document_still_generates(monkeypatch):
    """Only PDFs have question structure to parse; an image or a .md falls back
    to generation rather than failing."""
    stub = _StubGenerator()
    _patch_run(monkeypatch, generator=stub)
    key = storage_keys.new_knowledge_base_key("dev-alice", "text/markdown")

    generation_job.run("dev-alice", "01TESTID", _payload(knowledge_base_key=key, count=4))

    assert stub.calls[0][1] == 4


# --- run: success, retry, refund ----------------------------------------------


def test_a_successful_run_turns_the_test_into_a_draft(monkeypatch):
    written = _patch_run(monkeypatch)

    generation_job.run("dev-alice", "01TESTID", _payload())

    assert written["test"].status is TestStatus.draft
    assert written["test"].question_count == 1
    assert written["test"].generation_started_at is None


def test_a_transient_failure_is_retried_once(monkeypatch):
    attempts = {"n": 0}

    class _FlakyGenerator(_StubGenerator):
        def generate(self, *args, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise UpstreamError("the model is having a moment")
            return super().generate(*args, **kwargs)

    written = _patch_run(monkeypatch, generator=_FlakyGenerator())

    generation_job.run("dev-alice", "01TESTID", _payload())

    assert attempts["n"] == 2
    assert written["test"].status is TestStatus.draft


def test_a_permanent_failure_is_not_retried(monkeypatch):
    """A corrupt PDF is corrupt on the retry too. Retrying only bills twice to
    reach the same answer."""
    attempts = {"n": 0}

    class _RejectingGenerator(_StubGenerator):
        def generate(self, *args, **kwargs):
            attempts["n"] += 1
            raise BadRequestError("no readable questions in that file")

    _patch_run(monkeypatch, generator=_RejectingGenerator())
    refunds = _patch_refund(monkeypatch)

    generation_job.run("dev-alice", "01TESTID", _payload())

    assert attempts["n"] == 1
    assert refunds["company"].credit_balance == 6  # 5 + 1 back


def _patch_refund(monkeypatch) -> dict:
    refunds: dict = {}
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: _teacher("COMP1"))
    monkeypatch.setattr(companies_repo, "get_company", lambda cid: store.Stored(_company(5, 9), 3))
    monkeypatch.setattr(
        companies_repo,
        "update_company",
        lambda company, version: refunds.update(company=company, version=version) or version + 1,
    )
    return refunds


def test_failing_twice_refunds_both_pools_and_records_the_error(monkeypatch):
    class _AlwaysFails(_StubGenerator):
        def generate(self, *args, **kwargs):
            raise UpstreamError("model unavailable")

    written = _patch_run(monkeypatch, generator=_AlwaysFails())
    refunds = _patch_refund(monkeypatch)

    generation_job.run("dev-alice", "01TESTID", _payload())

    # 5 + 1 test credit, 9 + 1 AI credit (prompt mode) -- the exact inverse of
    # what start() took.
    assert (refunds["company"].credit_balance, refunds["company"].ai_credit_balance) == (6, 10)
    assert written["test"].status is TestStatus.generation_failed
    assert "model unavailable" in written["test"].generation_error


def test_the_refund_is_version_guarded(monkeypatch):
    """A refund written without the version it was read at could double-refund
    against a concurrent write."""

    class _AlwaysFails(_StubGenerator):
        def generate(self, *args, **kwargs):
            raise UpstreamError("nope")

    _patch_run(monkeypatch, generator=_AlwaysFails())
    refunds = _patch_refund(monkeypatch)

    generation_job.run("dev-alice", "01TESTID", _payload())

    assert refunds["version"] == 3  # the version the company was read at


def test_run_never_raises(monkeypatch):
    """It is a background task: there is nobody left to catch anything."""

    class _Exploding(_StubGenerator):
        def generate(self, *args, **kwargs):
            raise RuntimeError("something nobody anticipated")

    _patch_run(monkeypatch, generator=_Exploding())
    _patch_refund(monkeypatch)

    generation_job.run("dev-alice", "01TESTID", _payload())  # must not raise


def test_a_refund_failure_still_records_the_error(monkeypatch):
    """A teacher must see *something* on the card even if the refund write lost
    a race."""

    class _AlwaysFails(_StubGenerator):
        def generate(self, *args, **kwargs):
            raise UpstreamError("model unavailable")

    written = _patch_run(monkeypatch, generator=_AlwaysFails())
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: _teacher("COMP1"))
    monkeypatch.setattr(companies_repo, "get_company", lambda cid: store.Stored(_company(5, 9), 3))
    monkeypatch.setattr(
        companies_repo,
        "update_company",
        lambda company, version: (_ for _ in ()).throw(RuntimeError("lost the race")),
    )

    generation_job.run("dev-alice", "01TESTID", _payload())

    assert written["test"].status is TestStatus.generation_failed


def test_a_test_deleted_mid_run_is_not_resurrected(monkeypatch):
    _patch_run(monkeypatch)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: None)
    monkeypatch.setattr(
        tests_repo, "update_test", lambda *a: pytest.fail("must not write a deleted test back")
    )

    generation_job.run("dev-alice", "01TESTID", _payload())


# --- figures ------------------------------------------------------------------


def test_extracted_figures_are_stored_and_attached_to_their_question(monkeypatch):
    written = _patch_run(monkeypatch)
    key = storage_keys.new_knowledge_base_key("dev-alice", "application/pdf")
    monkeypatch.setattr(
        generation_job.knowledge_base, "read_stored", lambda sub, k: (b"%PDF-fake", "application/pdf")
    )
    monkeypatch.setattr(generation_job, "get_question_extractor", lambda: object())
    monkeypatch.setattr(
        generation_pipeline,
        "run_extraction",
        lambda **kw: generation_pipeline.ExtractionOutcome(
            questions=[
                ExtractedQuestion(
                    number=1, stem="Q1", options=["A", "B", "C", "D"], correct_index=0, source_page=1
                ),
                ExtractedQuestion(
                    number=2, stem="Q2", options=["A", "B", "C", "D"], correct_index=1, source_page=1
                ),
            ],
            expected_count=2,
            figures=[generation_pipeline.FigureAttachment(question_number=2, png=b"\x89PNG-fake")],
            page_count=1,
        ),
    )
    stored_images: list = []

    def _store_image(test_id, content_type, data):
        stored_images.append((test_id, content_type, data))
        from app.schemas.tests import QuestionImageUploadResponse

        return QuestionImageUploadResponse(image_key="tests/T/q/IMG.png", image_url="http://x/IMG.png")

    monkeypatch.setattr(test_service, "store_question_image", _store_image)

    generation_job.run("dev-alice", "01TESTID", _payload(knowledge_base_key=key))

    assert len(stored_images) == 1  # only question 2 had a figure
    questions = written["questions"]
    assert questions[0].image_key is None
    assert questions[1].image_key == "tests/T/q/IMG.png"
    assert questions[1].image_alt == "Figure for question 2"


def test_an_extracted_stem_is_stored_as_sanitized_rich_text(monkeypatch):
    """Extracted stems are plain text off a PDF, but they are later rendered
    with dangerouslySetInnerHTML (CLAUDE.md rule 9)."""
    written = _patch_run(monkeypatch)
    key = storage_keys.new_knowledge_base_key("dev-alice", "application/pdf")
    monkeypatch.setattr(
        generation_job.knowledge_base, "read_stored", lambda sub, k: (b"%PDF-fake", "application/pdf")
    )
    monkeypatch.setattr(generation_job, "get_question_extractor", lambda: object())
    monkeypatch.setattr(
        generation_pipeline,
        "run_extraction",
        lambda **kw: generation_pipeline.ExtractionOutcome(
            questions=[
                ExtractedQuestion(
                    number=1,
                    stem="If a < b <script>alert(1)</script>, find x",
                    options=["A", "B", "C", "D"],
                    correct_index=0,
                    source_page=1,
                )
            ],
            expected_count=1,
            figures=[],
            page_count=1,
        ),
    )

    generation_job.run("dev-alice", "01TESTID", _payload(knowledge_base_key=key))

    stem = written["questions"][0].stem
    assert "<script>" not in stem
    assert "alert(1)" in stem  # escaped to text, not dropped
    assert stem.startswith("<p>")


# --- refund is the exact inverse of the debit ---------------------------------


def test_refunded_undoes_debited_exactly():
    company = _company(5, ai_credit_balance=9)

    round_tripped = ai_credits.refunded(
        ai_credits.debited(company, ai_credits=2), ai_credits=2
    )

    assert round_tripped.credit_balance == company.credit_balance
    assert round_tripped.ai_credit_balance == company.ai_credit_balance
