"""Student session + token-lookup persistence.

Sessions live in the test's own partition (a test's owner already controls
who can list them, via tests_repo). Each session is written together with
its token lookup item in one batch so the two are never left out of sync.
"""

from app.models.session import StudentSession
from app.models.token import TokenLookup
from app.repositories import keys, store

SESSION_ENTITY = "SESSION"
TOKEN_ENTITY = "TOKEN_LOOKUP"


def create_sessions(sessions: list[StudentSession], teacher_sub: str) -> None:
    items = []
    for session in sessions:
        items.append(
            store.encode_item(
                keys.test_pk(session.test_id), keys.session_sk(session.session_id), SESSION_ENTITY, session
            )
        )
        lookup = TokenLookup(test_id=session.test_id, session_id=session.session_id, teacher_sub=teacher_sub)
        items.append(store.encode_item(keys.token_pk(session.link_token), keys.LOOKUP_SK, TOKEN_ENTITY, lookup))
    store.batch_write(items)


def list_sessions(test_id: str) -> list[StudentSession]:
    stored = store.query_prefix(keys.test_pk(test_id), keys.SESSION_SK_PREFIX, StudentSession)
    return [s.model for s in stored]


def get_session(test_id: str, session_id: str) -> store.Stored[StudentSession] | None:
    return store.get(keys.test_pk(test_id), keys.session_sk(session_id), StudentSession)


def get_token_lookup(token: str) -> store.Stored[TokenLookup] | None:
    return store.get(keys.token_pk(token), keys.LOOKUP_SK, TokenLookup)
