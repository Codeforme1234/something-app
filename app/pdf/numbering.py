"""Count the questions a paper contains, before spending a token on it.

This is what makes "store all the questions irrespective" enforceable: the
expected count comes from the paper's own numbering, so a short extraction is
detectable and repairable rather than silently accepted.
"""

import re

#: `Q12.` / `Q12)` / `Question 12:` / bare `12.` at the start of a line.
#: Anchored to line start so an inline "(2)" option marker or a mid-sentence
#: number can never match.
_NUMBER_RE = re.compile(r"(?m)^\s*(?:Q(?:uestion)?\s*)?(\d{1,3})\s*[.)\]:]")

#: A prefixed form (`Q12.`) is unambiguous. Bare `12.` is not -- numbered option
#: lists use the same shape -- so a document that has prefixed markers at all is
#: read using only those.
_PREFIXED_RE = re.compile(r"(?m)^\s*Q(?:uestion)?\s*(\d{1,3})\s*[.)\]:]")


def find_question_numbers(document_text: str) -> list[int]:
    """Question numbers in the order they appear.

    Prefers the unambiguous `Q<n>` form when the document uses it anywhere, and
    only falls back to bare `<n>.` for papers that never prefix. Mixing the two
    is what produces phantom questions out of numbered option lists.
    """
    prefixed = [int(m) for m in _PREFIXED_RE.findall(document_text)]
    if prefixed:
        return prefixed
    return [int(m) for m in _NUMBER_RE.findall(document_text)]


def expected_count(numbers: list[int]) -> int:
    """How many questions the paper actually has.

    Distinct numbers rather than raw hits, because a number reprinted in a
    continuation header would otherwise inflate the count. Returns 0 when the
    numbering is unreadable, which callers treat as "unknown" and skip the
    count check rather than aborting -- refusing to extract would be the one
    outcome the product explicitly rules out.
    """
    return len(set(numbers))


def missing_ranges(expected: list[int], extracted: list[int]) -> list[tuple[int, int]]:
    """Contiguous runs of question numbers that were expected but not returned.

    Ranges rather than individual numbers so a repair pass can ask for
    "questions 41-45" in one call instead of five.
    """
    missing = sorted(set(expected) - set(extracted))
    if not missing:
        return []

    ranges: list[tuple[int, int]] = []
    start = previous = missing[0]
    for number in missing[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append((start, previous))
        start = previous = number
    ranges.append((start, previous))
    return ranges
