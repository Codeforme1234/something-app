"""Deterministic question extractor for dev and tests. No network, no API key.

It ignores the document text entirely, so the whole PDF flow runs locally with
any valid PDF. The real app/pdf/ parser still runs -- only the model calls are
faked -- so a dev box exercises the parsing, cleaning, and figure cropping for
real, which is where the interesting bugs live.

Every question it returns satisfies ExtractedQuestion's strict validators, so
the fake can never be the reason a run fails validation.
"""

from collections.abc import Sequence

from app.llm.extraction_schemas import ExtractedQuestion

#: How many questions to invent when the caller has no count to go on.
_FALLBACK_COUNT = 6
#: Which question claims a figure, so the crop-and-attach branch runs locally.
_FIGURE_EVERY = 5
#: Distinct from FakeMCQGenerator's " (from your uploaded material)", which
#: tests/integration/test_ai_generation_api.py asserts on and must not change.
_TELL = " (extracted from your PDF)"


class FakeQuestionExtractor:
    def transcribe_page(self, page_png: bytes, page_number: int) -> str:
        # Includes the byte length so a test can prove the real renderer ran
        # rather than a placeholder being passed through.
        return (
            f"Q{page_number}. Transcribed page {page_number} "
            f"from a {len(page_png)}-byte render.{_TELL}\n"
            "(1) first (2) second (3) third (4) fourth"
        )

    def extract(
        self,
        document_text: str,
        *,
        expected_count: int,
        instruction: str | None = None,
        answer_key: str | None = None,
        only_numbers: Sequence[int] | None = None,
    ) -> list[ExtractedQuestion]:
        if only_numbers:
            numbers = list(only_numbers)
        else:
            count = expected_count if expected_count > 0 else _FALLBACK_COUNT
            numbers = list(range(1, count + 1))

        # Echoed so an integration test can assert the teacher's instruction
        # actually reached the extractor.
        note = f" [{instruction.strip()[:60]}]" if instruction else ""

        return [
            ExtractedQuestion(
                number=number,
                stem=f"Question {number} extracted from the paper{_TELL}{note}",
                options=[
                    f"Q{number} option A",
                    f"Q{number} option B",
                    f"Q{number} option C",
                    f"Q{number} option D",
                ],
                # Vary it so nothing accidentally depends on a fixed answer.
                correct_index=number % 4,
                source_page=max(1, (number - 1) // 4 + 1),
                has_figure=number % _FIGURE_EVERY == 0,
            )
            for number in numbers
        ]
