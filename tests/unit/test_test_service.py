"""Unit tests for the draft-only mutation rules in test_service. The
repository layer is monkeypatched so these run without DynamoDB — the
create/get/list/delete round trip against the real store is covered by
tests/integration/test_tests_api.py instead."""

import pytest

from app.core.clock import now
from app.core.exceptions import ConflictError, InsufficientCreditsError, NotFoundError
from app.llm.schemas import GeneratedMCQ
from app.models.company import Company
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


def _company(credit_balance: int) -> Company:
    return Company(company_id="COMP1", name="Alice's company", credit_balance=credit_balance, created_at=now())


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
    calls = []
    monkeypatch.setattr(tests_repo, "delete_test", lambda sub, tid: calls.append((sub, tid)))

    test_service.delete_test("dev-alice", "01TESTID")
    assert calls == [("dev-alice", "01TESTID")]


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
        self.calls: list[tuple[str, int, Difficulty, str | None]] = []

    def generate(self, topic, count, difficulty, knowledge_base=None):
        self.calls.append((topic, count, difficulty, knowledge_base))
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

    assert stub.calls == [("Photosynthesis", 1, Difficulty.medium, None)]
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
