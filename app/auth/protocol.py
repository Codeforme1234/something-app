from typing import Protocol

from pydantic import BaseModel


class TeacherClaims(BaseModel):
    sub: str
    email: str
    name: str


class TokenVerifier(Protocol):
    def verify(self, token: str) -> TeacherClaims:
        """Verify a bearer token and return teacher claims. Raise ValueError if invalid."""
        ...
