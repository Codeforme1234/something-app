import pytest
from pydantic import ValidationError

from app.schemas.support import SupportCategory, SupportRequest


def test_subject_and_message_must_be_nonblank_after_strip():
    with pytest.raises(ValidationError):
        SupportRequest(category=SupportCategory.bug, subject="   ", message="It broke")
    with pytest.raises(ValidationError):
        SupportRequest(category=SupportCategory.bug, subject="It broke", message="   ")


def test_subject_and_message_are_stripped():
    req = SupportRequest(category=SupportCategory.other, subject="  Hi  ", message="  Hello  ")
    assert req.subject == "Hi"
    assert req.message == "Hello"


def test_subject_max_length_enforced():
    with pytest.raises(ValidationError):
        SupportRequest(category=SupportCategory.other, subject="x" * 201, message="m")


def test_message_max_length_enforced():
    with pytest.raises(ValidationError):
        SupportRequest(category=SupportCategory.other, subject="s", message="x" * 5001)


def test_invalid_category_rejected():
    with pytest.raises(ValidationError):
        SupportRequest(category="not-a-category", subject="s", message="m")


@pytest.mark.parametrize("category", list(SupportCategory))
def test_every_category_accepted(category):
    SupportRequest(category=category, subject="s", message="m")
