"""Submission persistence.

A submission is written exactly once, atomically together with the session
it completes (status -> completed, score fields filled in) via
`store.transact_put_new_and_update`: both writes land or neither does, so a
crash between them can never leave a "completed" session with no submission
record or a submission with no completed session.
"""

from app.models.session import StudentSession
from app.models.submission import Submission
from app.repositories import keys, store
from app.repositories.sessions_repo import SESSION_ENTITY

SUBMISSION_ENTITY = "SUBMISSION"


def get_submission(test_id: str, session_id: str) -> store.Stored[Submission] | None:
    return store.get(keys.test_pk(test_id), keys.submission_sk(session_id), Submission)


def create_submission_and_complete_session(
    submission: Submission, completed_session: StudentSession, expected_session_version: int
) -> int:
    return store.transact_put_new_and_update(
        new_pk=keys.test_pk(submission.test_id),
        new_sk=keys.submission_sk(submission.session_id),
        new_entity_type=SUBMISSION_ENTITY,
        new_model=submission,
        update_pk=keys.test_pk(completed_session.test_id),
        update_sk=keys.session_sk(completed_session.session_id),
        update_entity_type=SESSION_ENTITY,
        update_model=completed_session,
        update_expected_version=expected_session_version,
    )
