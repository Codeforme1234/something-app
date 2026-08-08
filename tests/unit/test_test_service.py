"""Unit tests for the draft-only mutation rules in test_service. The
repository layer is monkeypatched so these run without DynamoDB — the
create/get/list/delete round trip against the real store is covered by
tests/integration/test_tests_api.py instead."""

from datetime import timedelta

import pytest

from app.core.clock import now
from app.core.config import get_settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    InsufficientAiCreditsError,
    InsufficientCreditsError,
    NotFoundError,
)
from app.llm.schemas import GeneratedMCQ
from app.models.company import Company
from app.models.question import Question
from app.models.teacher import Teacher
from app.models.test import Difficulty, Test, TestStatus
from app.repositories import companies_repo, store, teachers_repo, tests_repo
from app.schemas.tests import CreateTestRequest, GenerateQuestionsRequest, PutQuestionsRequest, QuestionInput, UpdateTestRequest
from app.services import test_service


def _test(test_status: TestStatus) -> Test:
    return Test(
        test_id="01TESTID",
        teacher_sub="dev-alice",
        title="Sample",
        difficulty=Difficulty.easy,
        duration_seconds=600,
        status=test_status,
        created_at=now(),
    )


def _teacher(company_id: str | None) -> Teacher:
    return Teacher(sub="dev-alice", email="a@x.com", name="Alice", company_id=company_id, created_at=now())


def _company(credit_balance: int, ai_credit_balance: int | None = 20) -> Company:
    """AI credits default to a comfortable balance so tests about *test* credits
    are not accidentally testing the AI pool. Pass 0 or None explicitly to
    exercise the AI-credit guard."""
    return Company(
        company_id="COMP1",
        name="Alice's company",
        credit_balance=credit_balance,
        ai_credit_balance=ai_credit_balance,
        created_at=now(),
    )


def test_create_test_spends_one_credit(monkeypatch):
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: _teacher("COMP1"))
    monkeypatch.setattr(companies_repo, "get_company", lambda cid: store.Stored(_company(5), 1))
    spent = {}
    monkeypatch.setattr(
        tests_repo,
        "create_test_and_spend_credit",
        lambda test, company, version: spent.update(test=test, company=company, version=version) or version + 1,
    )

    result = test_service.create_test(
        "dev-alice", CreateTestRequest(title="New", difficulty=Difficulty.easy, duration_seconds=600)
    )

    assert result.title == "New"
    assert spent["company"].credit_balance == 4  # debited by exactly one
    assert spent["test"].company_id == "COMP1"


def test_create_test_with_zero_credits_raises_402(monkeypatch):
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: _teacher("COMP1"))
    monkeypatch.setattr(companies_repo, "get_company", lambda cid: store.Stored(_company(0), 1))

    with pytest.raises(InsufficientCreditsError):
        test_service.create_test(
            "dev-alice", CreateTestRequest(title="New", difficulty=Difficulty.easy, duration_seconds=600)
        )


def test_create_test_without_a_company_raises_conflict(monkeypatch):
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: _teacher(None))

    with pytest.raises(ConflictError):
        test_service.create_test(
            "dev-alice", CreateTestRequest(title="New", difficulty=Difficulty.easy, duration_seconds=600)
        )


def test_update_draft_test_succeeds(monkeypatch):
    stored = store.Stored(_test(TestStatus.draft), 1)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: stored)
    monkeypatch.setattr(tests_repo, "update_test", lambda sub, test, version: version + 1)

    result = test_service.update_test("dev-alice", "01TESTID", UpdateTestRequest(title="New title"))
    assert result.title == "New title"


def test_update_published_test_raises_conflict(monkeypatch):
    stored = store.Stored(_test(TestStatus.published), 1)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: stored)

    with pytest.raises(ConflictError):
        test_service.update_test("dev-alice", "01TESTID", UpdateTestRequest(title="New title"))


def test_replace_questions_on_published_test_raises_conflict(monkeypatch):
    stored = store.Stored(_test(TestStatus.published), 1)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: stored)

    payload = PutQuestionsRequest(
        questions=[QuestionInput(stem="Q1", options=["a", "b", "c", "d"], correct_index=0)]
    )
    with pytest.raises(ConflictError):
        test_service.replace_questions("dev-alice", "01TESTID", payload)


def test_delete_published_test_raises_conflict(monkeypatch):
    stored = store.Stored(_test(TestStatus.published), 1)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: stored)

    with pytest.raises(ConflictError):
        test_service.delete_test("dev-alice", "01TESTID")


def test_delete_draft_test_delegates_to_repo(monkeypatch):
    stored = store.Stored(_test(TestStatus.draft), 1)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: stored)
    # delete_test now reads the questions first to collect image keys for the
    # storage sweep. Stub it, or this unit test starts needing DynamoDB.
    monkeypatch.setattr(tests_repo, "get_questions", lambda tid: [])
    calls = []
    monkeypatch.setattr(tests_repo, "delete_test", lambda sub, tid: calls.append((sub, tid)))

    test_service.delete_test("dev-alice", "01TESTID")
    assert calls == [("dev-alice", "01TESTID")]


def test_delete_test_sweeps_image_keys_after_the_rows_are_gone(monkeypatch):
    """Order matters: the DynamoDB delete must land before the storage sweep, and
    a storage failure must not fail the teacher's request."""
    stored = store.Stored(_test(TestStatus.draft), 1)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: stored)
    monkeypatch.setattr(
        tests_repo,
        "get_questions",
        lambda tid: [
            Question(
                question_id="q1",
                order=1,
                stem="s",
                options=["a", "b", "c", "d"],
                correct_index=0,
                image_key="tests/01TESTID/q/IMG1.png",
            ),
            Question(question_id="q2", order=2, stem="s", options=["a", "b", "c", "d"], correct_index=0),
        ],
    )

    events: list[str] = []
    monkeypatch.setattr(tests_repo, "delete_test", lambda sub, tid: events.append("rows-deleted"))

    class _Store:
        def delete_many(self, keys):
            events.append(f"swept:{','.join(keys)}")

    monkeypatch.setattr(test_service, "get_object_store", lambda: _Store())

    test_service.delete_test("dev-alice", "01TESTID")

    # Only the question that had an image, and only after the rows were removed.
    assert events == ["rows-deleted", "swept:tests/01TESTID/q/IMG1.png"]


def test_delete_test_survives_a_storage_sweep_failure(monkeypatch):
    stored = store.Stored(_test(TestStatus.draft), 1)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: stored)
    monkeypatch.setattr(
        tests_repo,
        "get_questions",
        lambda tid: [
            Question(
                question_id="q1",
                order=1,
                stem="s",
                options=["a", "b", "c", "d"],
                correct_index=0,
                image_key="tests/01TESTID/q/IMG1.png",
            )
        ],
    )
    deleted = []
    monkeypatch.setattr(tests_repo, "delete_test", lambda sub, tid: deleted.append(tid))

    class _BrokenStore:
        def delete_many(self, keys):
            raise OSError("disk on fire")

    monkeypatch.setattr(test_service, "get_object_store", lambda: _BrokenStore())

    test_service.delete_test("dev-alice", "01TESTID")  # must not raise

    assert deleted == ["01TESTID"]


def test_get_missing_test_raises_not_found(monkeypatch):
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: None)

    with pytest.raises(NotFoundError):
        test_service.get_test_detail("dev-alice", "nope")


def test_update_missing_test_raises_not_found(monkeypatch):
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: None)

    with pytest.raises(NotFoundError):
        test_service.update_test("dev-alice", "nope", UpdateTestRequest(title="X"))


class _StubGenerator:
    """Fake MCQGenerator that records the call it received instead of
    talking to any LLM -- generate_questions only needs to delegate to
    whatever app.llm.get_mcq_generator returns."""

    def __init__(self, questions=None):
        self.questions = questions or [
            GeneratedMCQ(stem="Q1?", options=["A", "B", "C", "D"], correct_index=0)
        ]
        self.calls: list[tuple[str, int, Difficulty, str | None, str | None]] = []

    def generate(self, topic, count, difficulty, knowledge_base=None, guidelines=None):
        self.calls.append((topic, count, difficulty, knowledge_base, guidelines))
        return self.questions


def _generate_payload(**overrides) -> GenerateQuestionsRequest:
    defaults = {"topic": "Photosynthesis", "count": 1, "difficulty": Difficulty.medium}
    defaults.update(overrides)
    return GenerateQuestionsRequest(**defaults)


def test_generate_questions_delegates_to_configured_generator(monkeypatch):
    stored = store.Stored(_test(TestStatus.draft), 1)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: stored)
    stub = _StubGenerator()
    monkeypatch.setattr(test_service, "get_mcq_generator", lambda: stub)

    result = test_service.generate_questions(
        "dev-alice", "01TESTID", _generate_payload(topic="Photosynthesis", count=1)
    )

    assert stub.calls == [("Photosynthesis", 1, Difficulty.medium, None, None)]
    assert [q.stem for q in result.questions] == ["Q1?"]


def test_generate_questions_on_published_test_raises_conflict(monkeypatch):
    stored = store.Stored(_test(TestStatus.published), 1)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: stored)
    monkeypatch.setattr(test_service, "get_mcq_generator", lambda: _StubGenerator())

    with pytest.raises(ConflictError):
        test_service.generate_questions("dev-alice", "01TESTID", _generate_payload())


def test_generate_questions_on_missing_test_raises_not_found(monkeypatch):
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: None)
    monkeypatch.setattr(test_service, "get_mcq_generator", lambda: _StubGenerator())

    with pytest.raises(NotFoundError):
        test_service.generate_questions("dev-alice", "nope", _generate_payload())


def test_generate_questions_does_not_write_anything(monkeypatch):
    stored = store.Stored(_test(TestStatus.draft), 1)
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: stored)
    monkeypatch.setattr(test_service, "get_mcq_generator", lambda: _StubGenerator())

    def _fail(*args, **kwargs):
        raise AssertionError("generate_questions must not write to the repository")

    monkeypatch.setattr(tests_repo, "replace_questions", _fail)
    monkeypatch.setattr(tests_repo, "update_test", _fail)

    test_service.generate_questions("dev-alice", "01TESTID", _generate_payload())


# --- guidelines reach the generator as plain text ----------------------------


def _stub_owned_draft(monkeypatch):
    monkeypatch.setattr(
        tests_repo, "get_test", lambda sub, tid: store.Stored(_test(TestStatus.draft), 1)
    )


def test_guidelines_reach_the_generator_flattened_to_plain_text(monkeypatch):
    """A model must never be handed an HTML fragment, and a naive tag strip would
    turn a bulleted list into one run-on instruction."""
    _stub_owned_draft(monkeypatch)
    stub = _StubGenerator()
    monkeypatch.setattr(test_service, "get_mcq_generator", lambda: stub)

    payload = _generate_payload(
        guidelines="<p>Rules:</p><ul><li>No trick questions</li><li>Use SI units</li></ul>"
    )
    test_service.generate_questions("dev-alice", "01TESTID", payload)

    guidelines = stub.calls[0][4]
    assert "<" not in guidelines  # no markup survives into the prompt
    assert "- No trick questions" in guidelines  # list structure does
    assert "- Use SI units" in guidelines


def test_absent_guidelines_are_passed_as_none(monkeypatch):
    _stub_owned_draft(monkeypatch)
    stub = _StubGenerator()
    monkeypatch.setattr(test_service, "get_mcq_generator", lambda: stub)

    test_service.generate_questions("dev-alice", "01TESTID", _generate_payload())

    assert stub.calls[0][4] is None


def test_an_empty_editor_counts_as_no_guidelines(monkeypatch):
    """Tiptap posts "<p></p>" for an untouched editor."""
    _stub_owned_draft(monkeypatch)
    stub = _StubGenerator()
    monkeypatch.setattr(test_service, "get_mcq_generator", lambda: stub)

    test_service.generate_questions(
        "dev-alice", "01TESTID", _generate_payload(guidelines="<p></p>")
    )

    assert stub.calls[0][4] is None


def test_generate_defaults_count_and_difficulty_when_the_page_omits_them(monkeypatch):
    """The generate page no longer asks for either."""
    _stub_owned_draft(monkeypatch)
    stub = _StubGenerator()
    monkeypatch.setattr(test_service, "get_mcq_generator", lambda: stub)

    payload = GenerateQuestionsRequest(topic="Optics")
    test_service.generate_questions("dev-alice", "01TESTID", payload)

    _topic, count, difficulty, _kb, _g = stub.calls[0]
    assert count == 10
    assert difficulty is Difficulty.medium


# --- a generating test, as the API presents it --------------------------------
#
# Background generation runs in the process that served the request, so a
# restart mid-run strands the row in `generating`. Nothing reaps it; staleness
# is derived on read instead.


def _generating(started_at) -> Test:
    return Test(
        test_id="01TESTID",
        teacher_sub="dev-alice",
        title="Sample",
        difficulty=Difficulty.easy,
        duration_seconds=600,
        status=TestStatus.generating,
        created_at=now(),
        generation_started_at=started_at,
    )


def test_a_recent_generating_test_is_still_generating(monkeypatch):
    monkeypatch.setattr(
        tests_repo, "list_tests", lambda sub: [_generating(now() - timedelta(seconds=30))]
    )

    assert test_service.list_tests("dev-alice")[0].status is TestStatus.generating


def test_a_run_older_than_the_extraction_budget_reads_as_failed(monkeypatch):
    """Long enough that the process which owned it cannot still be working."""
    dead = now() - timedelta(seconds=get_settings().openai_extraction_timeout_seconds) - timedelta(hours=1)
    monkeypatch.setattr(tests_repo, "list_tests", lambda sub: [_generating(dead)])

    summary = test_service.list_tests("dev-alice")[0]

    assert summary.status is TestStatus.generation_failed
    assert "no credits were charged" in summary.generation_error


def test_staleness_is_not_written_back(monkeypatch):
    """Derived on read only. A write here would race the run it just declared
    dead, in the case where that run is merely slow."""
    dead = now() - timedelta(days=1)
    monkeypatch.setattr(tests_repo, "list_tests", lambda sub: [_generating(dead)])
    monkeypatch.setattr(
        tests_repo, "update_test", lambda *a: pytest.fail("must not write on a read")
    )

    test_service.list_tests("dev-alice")


def test_the_editor_refuses_a_test_that_is_still_generating(monkeypatch):
    """_require_draft already covers this, but it is the guarantee that stops a
    teacher's save racing the run that is about to overwrite their questions."""
    monkeypatch.setattr(
        tests_repo, "get_test", lambda sub, tid: store.Stored(_generating(now()), 1)
    )

    with pytest.raises(ConflictError):
        test_service.replace_questions(
            "dev-alice",
            "01TESTID",
            PutQuestionsRequest(
                questions=[
                    QuestionInput(
                        stem="<p>Q</p>", options=["A", "B", "C", "D"], correct_index=0
                    )
                ]
            ),
        )


def test_a_generating_test_cannot_be_deleted(monkeypatch):
    """A run is in flight and would write questions back under a deleted test."""
    monkeypatch.setattr(
        tests_repo, "get_test", lambda sub, tid: store.Stored(_generating(now()), 1)
    )

    with pytest.raises(ConflictError):
        test_service.delete_test("dev-alice", "01TESTID")


def test_a_failed_test_can_be_deleted(monkeypatch):
    """Otherwise _require_draft would leave the teacher permanently unable to
    clear a card for a test that never produced anything."""
    failed = _generating(None).model_copy(
        update={"status": TestStatus.generation_failed, "generation_error": "boom"}
    )
    monkeypatch.setattr(tests_repo, "get_test", lambda sub, tid: store.Stored(failed, 1))
    monkeypatch.setattr(tests_repo, "get_questions", lambda tid: [])
    deleted = {}
    monkeypatch.setattr(
        tests_repo, "delete_test", lambda sub, tid: deleted.update(test_id=tid)
    )

    test_service.delete_test("dev-alice", "01TESTID")

    assert deleted["test_id"] == "01TESTID"


def test_a_dead_generating_test_can_also_be_deleted(monkeypatch):
    """Because _presented turns it into generation_failed first -- without that
    a stranded run would be undeletable for ever."""
    monkeypatch.setattr(
        tests_repo, "get_test", lambda sub, tid: store.Stored(_generating(now() - timedelta(days=1)), 1)
    )
    monkeypatch.setattr(tests_repo, "get_questions", lambda tid: [])
    deleted = {}
    monkeypatch.setattr(
        tests_repo, "delete_test", lambda sub, tid: deleted.update(test_id=tid)
    )

    test_service.delete_test("dev-alice", "01TESTID")

    assert deleted["test_id"] == "01TESTID"
