from pydantic import BaseModel


class TokenLookup(BaseModel):
    """Stored at TOKEN#<token> / LOOKUP.

    The single-GetItem replacement for what would otherwise be a GSI from
    token -> session: the student link contains the token, and the later
    attempt-flow phase resolves it with one `store.get`.
    """

    test_id: str
    session_id: str
    teacher_sub: str
