from pydantic import BaseModel


class Question(BaseModel):
    question_id: str
    order: int
    stem: str
    options: list[str]
    correct_index: int
