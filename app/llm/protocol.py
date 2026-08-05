from typing import Protocol

from app.llm.schemas import GeneratedMCQ
from app.models.test import Difficulty


class MCQGenerator(Protocol):
    def generate(
        self,
        topic: str,
        count: int,
        difficulty: Difficulty,
        knowledge_base: str | None = None,
    ) -> list[GeneratedMCQ]:
        """Return exactly `count` schema-valid GeneratedMCQ for the given topic
        and difficulty. When `knowledge_base` is given, questions should be
        drawn from that material specifically rather than general knowledge.
        Raise app.core.exceptions.UpstreamError if generation fails (including
        after the implementation's own internal retries)."""
        ...
