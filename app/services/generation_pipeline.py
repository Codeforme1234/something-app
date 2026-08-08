"""PDF -> questions. Bytes in, extracted questions plus figure PNGs out.

Deliberately touches no object storage, no DynamoDB, and no credits, so the whole
pipeline is testable with a stub extractor and an in-memory PDF. The caller is
responsible for storing the figures and persisting the questions.

Implements docs/PLAN.md steps 1-3. Step 4 (generating *new* questions from the
extracted ones) is dropped by product decision: the paper's own questions, in
QuizDeck's format, are the deliverable.

Two things here beat what PLAN.md described, both learned from a real paper:

  - The correct answer is read from the paper's printed answer key
    (app/pdf/answers.py) and OVERRIDES whatever the model returned. PLAN.md's own
    README flagged model-guessed answers as the weak point; on a paper that
    prints its key, guessing is simply unnecessary.
  - Boilerplate stripping works across pages rather than within one, because real
    headers and footers appear once per page.
"""

import logging
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from app.core.exceptions import BadRequestError
from app.llm.extraction_schemas import ExtractedQuestion
from app.llm.prompts.question_extraction import format_answer_key
from app.llm.protocol import QuestionExtractor
from app.pdf import answers, document, numbering
from app.pdf.models import Figure, PdfDocument

logger = logging.getLogger(__name__)

#: Must stay equal to PutQuestionsRequest's cap: a test with more questions than
#: the editor can save is a test the teacher can never edit again.
MAX_QUESTIONS_PER_TEST = 100

ProgressFn = Callable[[str], None]


class FigureAttachment(BaseModel):
    """A cropped figure and the question it belongs to."""

    question_number: int
    png: bytes


class ExtractionOutcome(BaseModel):
    questions: list[ExtractedQuestion]
    #: How many the paper's numbering said there were. Compared against
    #: len(questions) this is what tells a teacher "73 of 75 came through".
    expected_count: int
    figures: list[FigureAttachment]
    page_count: int


def _noop(_message: str) -> None:
    pass


def run_extraction(
    *,
    pdf_bytes: bytes,
    extractor: QuestionExtractor,
    instruction: str | None = None,
    max_pages: int,
    progress: ProgressFn = _noop,
) -> ExtractionOutcome:
    progress("Reading your PDF")
    doc = document.open_document(pdf_bytes, max_pages=max_pages)

    blocks = answers.split_question_blocks(doc.text())
    option_answers = answers.answer_index_map(blocks)
    numeric_answers = answers.numeric_answer_map(blocks)

    expected_numbers = numbering.find_question_numbers(doc.text())
    expected = numbering.expected_count(expected_numbers)
    if expected:
        progress(f"Found {expected} questions")
    if expected > MAX_QUESTIONS_PER_TEST:
        # Truncating would violate "store all the questions irrespective", and a
        # test over the cap could never be saved from the editor -- so refuse
        # rather than half-deliver.
        raise BadRequestError(
            f"that PDF has {expected} questions; the limit is {MAX_QUESTIONS_PER_TEST}"
        )

    document_text = _with_vision_transcriptions(doc, pdf_bytes, extractor, progress)

    answer_key = format_answer_key(option_answers, numeric_answers) or None

    progress("Extracting questions")
    questions = extractor.extract(
        document_text,
        expected_count=expected,
        instruction=instruction,
        answer_key=answer_key,
    )

    questions = _repair_missing(
        questions,
        extractor=extractor,
        document_text=document_text,
        expected_numbers=expected_numbers,
        expected=expected,
        instruction=instruction,
        answer_key=answer_key,
        progress=progress,
    )

    # The paper's own key wins over the model on every question it covers.
    overridden = 0
    for question in questions:
        printed = option_answers.get(question.number)
        if printed is not None and question.correct_index != printed:
            question.correct_index = printed
            overridden += 1
    if overridden:
        logger.info("answer key corrected %d model-chosen answers", overridden)

    questions.sort(key=lambda q: q.number)
    figures = _attach_figures(doc, pdf_bytes, questions, progress)

    return ExtractionOutcome(
        questions=questions,
        expected_count=expected,
        figures=figures,
        page_count=doc.page_count,
    )


def _with_vision_transcriptions(
    doc: PdfDocument, pdf_bytes: bytes, extractor: QuestionExtractor, progress: ProgressFn
) -> str:
    """Replace each figure-bearing page's text with a transcription that describes
    what the figure shows, so the extraction call can read a question whose
    meaning is partly pictorial.

    Only pages classified as vision pay this cost -- on a typical paper that is
    well under a third of them.
    """
    vision_pages = set(doc.vision_pages())
    if not vision_pages:
        return doc.text()

    progress(f"Reading {len(vision_pages)} diagram page(s)")
    parts: list[str] = []
    for page in doc.pages:
        if page.number not in vision_pages:
            parts.append(page.clean_text or page.raw_text)
            continue
        try:
            png = document.render_page_png(pdf_bytes, page.number)
            transcription = extractor.transcribe_page(png, page.number)
        except Exception as exc:
            # A failed transcription degrades that page to its extracted text
            # rather than failing the whole run -- the text is usually most of
            # the question, just without the figure described.
            logger.warning("page %d transcription failed, using text: %s", page.number, exc)
            parts.append(page.clean_text or page.raw_text)
            continue
        # Keep both: the transcription describes the figure, the extracted text
        # carries the answer-key line and any glyphs the model misread.
        parts.append(f"{page.clean_text or page.raw_text}\n{transcription}")
    return "\n".join(parts)


def _repair_missing(
    questions: list[ExtractedQuestion],
    *,
    extractor: QuestionExtractor,
    document_text: str,
    expected_numbers: list[int],
    expected: int,
    instruction: str | None,
    answer_key: str | None,
    progress: ProgressFn,
) -> list[ExtractedQuestion]:
    """One targeted re-run for whatever the first pass missed.

    Exactly one: a second repair on a paper the model keeps failing on is a
    latency and cost sink, and the product accepts a short set (reported to the
    teacher) over a failed run.
    """
    if not expected:
        return questions

    gaps = numbering.missing_ranges(expected_numbers, [q.number for q in questions])
    if not gaps:
        return questions

    wanted = [n for start, end in gaps for n in range(start, end + 1)]
    first, last = wanted[0], wanted[-1]
    progress(f"Recovering questions {first}-{last}")
    logger.info("re-running extraction for %d missing question(s)", len(wanted))

    try:
        recovered = extractor.extract(
            document_text,
            expected_count=expected,
            instruction=instruction,
            answer_key=answer_key,
            only_numbers=wanted,
        )
    except Exception as exc:
        # Keep what we have: a partial paper the teacher can finish by hand beats
        # nothing at all.
        logger.warning("repair pass failed, keeping %d questions: %s", len(questions), exc)
        return questions

    have = {q.number for q in questions}
    return questions + [q for q in recovered if q.number not in have]


def _attach_figures(
    doc: PdfDocument,
    pdf_bytes: bytes,
    questions: list[ExtractedQuestion],
    progress: ProgressFn,
) -> list[FigureAttachment]:
    """Associate each cropped figure with the question it illustrates.

    Questions run down a page, so a figure belongs to the nearest question header
    above it -- compared in real page coordinates, since both come from
    pdfplumber. Only questions the model flagged `has_figure` are eligible, which
    keeps a decorative logo off a text-only question.

    A question that spans a page break has its figure on a page with no header
    above it; those figures fall to `carried`, the last header seen on an earlier
    page, so a diagram at the top of a continuation page still lands correctly.

    Only the first figure per question is kept -- Question holds one image, and on
    a question whose OPTIONS are pictures the first (topmost) figure is the stem
    diagram, which is the one worth showing.
    """
    eligible = {q.number for q in questions if q.has_figure}
    if not eligible:
        return []

    attachments: list[FigureAttachment] = []
    taken: set[int] = set()
    carried: int | None = None

    for page in doc.pages:
        headers = document.question_header_tops(pdf_bytes, page.number)

        if page.figure_count:
            for figure in document.extract_figures(pdf_bytes, page.number):
                owner = _owner_for(figure.top, headers, carried)
                if owner is None or owner not in eligible or owner in taken:
                    continue
                taken.add(owner)
                attachments.append(FigureAttachment(question_number=owner, png=figure.png))

        if headers:
            carried = headers[-1][0]

    if attachments:
        progress(f"Attaching {len(attachments)} diagram(s)")
    return attachments


def _owner_for(
    figure_top: float, headers: Sequence[tuple[int, float]], carried: int | None
) -> int | None:
    """The question whose header is the last one at or above `figure_top`.

    `carried` is the last header from a previous page, used when a figure sits
    above the first header on its own page (a question continuing across a break).
    """
    owner = carried
    for number, top in headers:
        if top <= figure_top:
            owner = number
        else:
            break
    return owner
