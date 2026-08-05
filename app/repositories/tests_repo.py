"""Test + question persistence.

Test meta lives in the teacher's partition (ownership by key — see
keys.py); questions live in the test's own partition. Both facts are baked
into the functions below rather than left for callers to get right.
"""

from app.models.company import Company
from app.models.question import Question
from app.models.test import Test
from app.repositories import keys, store
from app.repositories.companies_repo import COMPANY_ENTITY

TEST_ENTITY = "TEST"
QUESTION_ENTITY = "QUESTION"


def create_test(test: Test) -> None:
    store.put_new(keys.teacher_pk(test.teacher_sub), keys.test_sk(test.test_id), TEST_ENTITY, test)


def create_test_and_spend_credit(test: Test, company: Company, expected_company_version: int) -> int:
    """Create the test and debit one credit from its company in a single
    transaction -- both writes land or neither does, so a crash between them
    can never create a free test or spend a credit with nothing to show for
    it. Mirrors submissions_repo.create_submission_and_complete_session."""
    return store.transact_put_new_and_update(
        new_pk=keys.teacher_pk(test.teacher_sub),
        new_sk=keys.test_sk(test.test_id),
        new_entity_type=TEST_ENTITY,
        new_model=test,
        update_pk=keys.company_pk(company.company_id),
        update_sk=keys.PROFILE_SK,
        update_entity_type=COMPANY_ENTITY,
        update_model=company,
        update_expected_version=expected_company_version,
    )


def get_test(teacher_sub: str, test_id: str) -> store.Stored[Test] | None:
    """Look up by the *caller's* sub — a test owned by someone else simply misses."""
    return store.get(keys.teacher_pk(teacher_sub), keys.test_sk(test_id), Test)


def list_tests(teacher_sub: str) -> list[Test]:
    """Newest first: ULIDs are time-sortable, so a descending SK query is enough."""
    stored = store.query_prefix(
        keys.teacher_pk(teacher_sub), keys.TEST_SK_PREFIX, Test, descending=True
    )
    return [s.model for s in stored]


def update_test(teacher_sub: str, test: Test, expected_version: int) -> int:
    return store.put_versioned(
        keys.teacher_pk(teacher_sub), keys.test_sk(test.test_id), TEST_ENTITY, test, expected_version
    )


def get_questions(test_id: str) -> list[Question]:
    stored = store.query_prefix(keys.test_pk(test_id), keys.QUESTION_SK_PREFIX, Question)
    return [s.model for s in stored]


def replace_questions(test_id: str, questions: list[Question]) -> None:
    """Full replace: write the new set, then delete any leftover keys the new
    (shorter) set no longer covers."""
    existing_count = len(get_questions(test_id))
    items = [
        store.encode_item(keys.test_pk(test_id), keys.question_sk(q.order), QUESTION_ENTITY, q)
        for q in questions
    ]
    delete_keys = [
        {"PK": keys.test_pk(test_id), "SK": keys.question_sk(order)}
        for order in range(len(questions) + 1, existing_count + 1)
    ]
    store.batch_write(items, delete_keys)


def delete_test(teacher_sub: str, test_id: str) -> None:
    """Delete the test meta and every one of its questions in one batch."""
    delete_keys = [
        {"PK": keys.test_pk(test_id), "SK": keys.question_sk(q.order)} for q in get_questions(test_id)
    ]
    delete_keys.append({"PK": keys.teacher_pk(teacher_sub), "SK": keys.test_sk(test_id)})
    store.batch_write([], delete_keys)
