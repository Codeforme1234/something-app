"""Request/response DTOs for the Help & Support form."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class SupportCategory(StrEnum):
    bug = "bug"
    feature = "feature"
    billing = "billing"
    other = "other"


def _stripped_nonblank(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be blank")
    return stripped


class SupportRequest(BaseModel):
    category: SupportCategory
    subject: Annotated[str, Field(min_length=1, max_length=200)]
    message: Annotated[str, Field(min_length=1, max_length=5000)]

    @field_validator("subject")
    @classmethod
    def _strip_subject(cls, v: str) -> str:
        return _stripped_nonblank(v, "subject")

    @field_validator("message")
    @classmethod
    def _strip_message(cls, v: str) -> str:
        return _stripped_nonblank(v, "message")


class SupportResponse(BaseModel):
    status: str = "sent"
