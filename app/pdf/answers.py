"""Split a question paper into per-question blocks and read its answer key.

Prep-site "answer key" PDFs print the correct answer next to each question, and
reading it with a regex is strictly better than asking a model to solve the
question: free, deterministic, and correct. The model's own `correct_index` is
only a fallback for questions where no key line is present.

Two question shapes occur in a real JEE paper and they must not be conflated:

    Q3.  ... is equal to :
    (1) -4 (2) 4 (3) 8 (4) -8
    MathonGo Answer Key : (2)          <- OPTION INDEX (1-based)

    Q22. ... is equal to _______.
    MathonGo Answer Key : 91           <- NUMERIC VALUE, not an index

Conflating them is a live trap: a naive `[1-4]` search reads the leading `1` of
`192` as "option 1". So an answer is only read as an option index when the block
actually presents four options, and only when the printed number stands alone.
"""

import re

from pydantic import BaseModel

#: `Q12.` / `Q12)` / `Question 12:` at the start of a line.
_QUESTION_RE = re.compile(r"(?m)^\s*Q(?:uestion)?\s*(\d{1,3})\s*[.)\]:]")

#: The printed answer. `(?!\d)` and the leading boundary are what stop `192`
#: from reading as `1`. Vendor-agnostic on punctuation and prefix.
_ANSWER_RE = re.compile(
    r"Answer\s*(?:Key)?\s*[:\-]?\s*\(?\s*(\d{1,6})\s*\)?(?!\d)", re.IGNORECASE
)

#: Four option markers in order somewhere in the block.
_OPTIONS_RE = re.compile(r"\(1\).*?\(2\).*?\(3\).*?\(4\)", re.DOTALL)

OPTION_COUNT = 4


class QuestionBlock(BaseModel):
    """One question's slice of the document, plus what we could read from it."""

    number: int
    text: str
    #: True when the paper printed four options for this question. False marks a
    #: JEE "numerical value" question, which has no options at all.
    has_options: bool
    #: 0-based correct option, only ever set when has_options is True and the
    #: printed answer was a bare 1-4.
    correct_index: int | None = None
    #: The printed answer for a numerical-value question, verbatim.
    numeric_answer: str | None = None


def split_question_blocks(document_text: str) -> list[QuestionBlock]:
    """Slice the document at each question header, in document order.

    A block runs to the start of the next question, so it carries that
    question's stem, options, and answer line and nothing else.
    """
    matches = list(_QUESTION_RE.finditer(document_text))
    blocks: list[QuestionBlock] = []

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(document_text)
        body = document_text[start:end]

        has_options = bool(_OPTIONS_RE.search(body))
        correct_index: int | None = None
        numeric_answer: str | None = None

        answer_match = _ANSWER_RE.search(body)
        if answer_match:
            printed = answer_match.group(1)
            if has_options and printed.isdigit() and 1 <= int(printed) <= OPTION_COUNT:
                correct_index = int(printed) - 1
            else:
                # Either a numerical-value question, or an option-style question
                # whose printed answer is out of range -- in both cases this is
                # not an index and must not be treated as one.
                numeric_answer = printed

        blocks.append(
            QuestionBlock(
                number=int(match.group(1)),
                text=body,
                has_options=has_options,
                correct_index=correct_index,
                numeric_answer=numeric_answer,
            )
        )

    return blocks


def answer_index_map(blocks: list[QuestionBlock]) -> dict[int, int]:
    """Question number -> 0-based correct option, for the blocks that have one.

    Used to override whatever the model returned: the paper's own key wins.
    """
    return {b.number: b.correct_index for b in blocks if b.correct_index is not None}


def numeric_answer_map(blocks: list[QuestionBlock]) -> dict[int, str]:
    """Question number -> printed numeric answer, for numerical-value questions.

    These need the true value carried into the prompt so the model can build a
    four-option question around it instead of inventing an answer.
    """
    return {b.number: b.numeric_answer for b in blocks if b.numeric_answer is not None}


def find_question_headers(page_text: str) -> list[tuple[int, int]]:
    """(question number, 0-based line index) for every question header on a page.

    Used to work out which question a figure sits under: headers and figures
    interleave in reading order, so the last header above a figure owns it.
    """
    headers: list[tuple[int, int]] = []
    for index, line in enumerate(page_text.split("\n")):
        match = _QUESTION_RE.match(line)
        if match:
            headers.append((int(match.group(1)), index))
    return headers


def looks_like_answer_line(line: str) -> bool:
    """True for a line that is only an answer-key annotation.

    Protects these lines from boilerplate stripping: an answer line repeats on
    nearly every page and is short, so pure frequency analysis would otherwise
    delete the most valuable text in the document.
    """
    stripped = line.strip()
    if not stripped:
        return False
    match = _ANSWER_RE.search(stripped)
    if match is None:
        return False
    # Only an annotation if that is essentially the whole line -- a stem that
    # happens to contain the word "answer" must survive.
    return len(stripped) - len(match.group(0)) <= 30
