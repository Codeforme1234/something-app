"""Feedback persistence.

A StudentFeedback row is created as a `generating` placeholder synchronously
at submit time (app.services.feedback_job.start) and then updated in place --
to `ready` with content, or to `failed` -- by the background job
(app.services.feedback_job.run). Lives at TEST#<test_id> / FEEDBACK#<session_id>,
the same partition as the submission it is about.
"""

from app.models.feedback import StudentFeedback
from app.repositories import keys, store

FEEDBACK_ENTITY = "FEEDBACK"


def create_feedback(feedback: StudentFeedback) -> None:
    store.put_new(
        keys.test_pk(feedback.test_id), keys.feedback_sk(feedback.session_id), FEEDBACK_ENTITY, feedback
    )


def get_feedback(test_id: str, session_id: str) -> store.Stored[StudentFeedback] | None:
    return store.get(keys.test_pk(test_id), keys.feedback_sk(session_id), StudentFeedback)


def update_feedback(feedback: StudentFeedback, expected_version: int) -> int:
    return store.put_versioned(
        keys.test_pk(feedback.test_id),
        keys.feedback_sk(feedback.session_id),
        FEEDBACK_ENTITY,
        feedback,
        expected_version,
    )
