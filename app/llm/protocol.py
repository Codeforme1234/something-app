from collections.abc import Sequence
from typing import Protocol

from app.llm.extraction_schemas import ExtractedQuestion
from app.llm.feedback_schemas import FeedbackInput, GeneratedFeedback
from app.llm.schemas import GeneratedMCQ
from app.models.test import Difficulty


class MCQGenerator(Protocol):
    def generate(
        self,
        topic: str,
        count: int,
        difficulty: Difficulty,
        knowledge_base: str | None = None,
        guidelines: str | None = None,
    ) -> list[GeneratedMCQ]:
        """Return exactly `count` schema-valid GeneratedMCQ for the given topic
        and difficulty. When `knowledge_base` is given, questions should be
        drawn from that material specifically rather than general knowledge.
        `guidelines` is the teacher's own free-text instructions, already
        flattened to PLAIN TEXT by the caller -- implementations must not be
        handed an HTML fragment. Raise app.core.exceptions.UpstreamError if
        generation fails (including after the implementation's own internal
        retries)."""
        ...


class QuestionExtractor(Protocol):
    """Reads questions that ALREADY EXIST in a document, unlike MCQGenerator
    which invents them.

    A separate Protocol rather than widening MCQGenerator, because the two have
    incompatible contracts: `generate` promises exactly `count` questions, while
    extraction discovers an unknown number and must be allowed to come up short
    so the caller can repair it.
    """

    def transcribe_page(self, page_png: bytes, page_number: int) -> str:
        """Plain-text transcription of one rendered page: question text and
        options verbatim, with any diagram, graph, or circuit described in enough
        detail to answer without seeing it. Raise
        app.core.exceptions.UpstreamError on give-up."""
        ...

    def extract(
        self,
        document_text: str,
        *,
        expected_count: int,
        instruction: str | None = None,
        answer_key: str | None = None,
        only_numbers: Sequence[int] | None = None,
    ) -> list[ExtractedQuestion]:
        """Extract the questions present in `document_text`, transformed per
        `instruction` (which may reword or change values but must never invent or
        drop a question).

        `expected_count` is advisory context for the model, NOT a contract --
        callers must accept a short set and repair it by calling again with
        `only_numbers`. `answer_key` carries the paper's own printed answers,
        which are authoritative. Raise UpstreamError on give-up."""
        ...


class FeedbackGenerator(Protocol):
    def generate(self, input: FeedbackInput) -> GeneratedFeedback:
        """Second-person feedback for one completed attempt, at FULL DEPTH:
        `input` carries every question's options, the student's chosen
        option, and the correct option (app.llm.feedback_schemas.
        FeedbackQuestionResult) -- unlike v1, the correct answer is not
        withheld from the model.

        An implementation MAY explain the concept behind a question the
        student missed; a teacher reviews every result before it is emailed
        (app.services.feedback_service.email_feedback), and that review is
        the safeguard, not withholding the answer here. What it must NOT do
        is emit a bare per-question answer key, or shame the student for a
        low score. Raise app.core.exceptions.UpstreamError if generation
        fails (including after the implementation's own internal retries)."""
        ...
