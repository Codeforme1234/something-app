from pydantic import BaseModel


class Question(BaseModel):
    question_id: str
    order: int
    stem: str
    options: list[str]
    correct_index: int
    # Object-store key, not a URL: the public origin is a runtime setting, so
    # moving storage never touches a stored blob. Optional so questions stored
    # before images existed still deserialize -- same reason as Test.company_id.
    image_key: str | None = None
    # Plain text, rendered as an <img alt> attribute -- NOT rich text, so it
    # must not go through sanitize_rich_text (that returns HTML and raises on blank).
    image_alt: str | None = None
