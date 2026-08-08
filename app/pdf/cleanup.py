"""Pure text cleanup applied before a single character reaches the model.

Two jobs, both free:

1. **Boilerplate removal.** Headers, footers, and watermarks repeat on every
   page and are pure token cost. Note this works ACROSS pages, not within one:
   in real prep-site papers each boilerplate line appears once per page, so a
   per-page frequency rule (which is what docs/PLAN.md assumed) would never fire.

2. **Injection defanging.** The document is untrusted input that reaches a
   model. This strips only text that could masquerade as *prompt structure* --
   chat-role headers and fence-like rules. It deliberately does NOT try to
   filter instruction-shaped prose: regex-matching natural language is
   unwinnable, and pretending otherwise is worse than being honest about it.
   The real boundaries are the nonce-delimited prompt and the output schema.
"""

import re
from collections import Counter

from app.pdf.answers import looks_like_answer_line

#: Boilerplate is short. A long repeated line is more likely a real instruction
#: block ("Read all questions carefully...") that is cheap to keep anyway.
MAX_BOILERPLATE_LEN = 60
#: A line must appear on at least this fraction of pages to count as boilerplate.
MIN_PAGE_FRACTION = 0.5
#: ...and on at least this many pages regardless, so a 2-page document does not
#: lose a line that merely happens to appear twice.
MIN_PAGES = 3

#: Lines that look like a chat transcript role marker. An injected
#: "system: ignore your instructions" is defanged into visible text.
_ROLE_LINE_RE = re.compile(
    r"(?mi)^\s*(system|assistant|user|developer|tool)\s*:", flags=0
)
#: Long runs of fence characters, which could try to close our own delimiter.
_FENCE_RE = re.compile(r"(?m)^\s*([-=_`~*#]{4,})\s*$")
#: Our own delimiter syntax, so a document cannot forge a document boundary.
_NONCE_MARKER_RE = re.compile(r"(?i)<<<\s*/?\s*(end\s*)?document[^>]*>>>")


def find_boilerplate_lines(page_texts: list[str]) -> set[str]:
    """Lines that repeat across enough pages to be header/footer furniture.

    Answer-key lines are excluded even though they repeat on nearly every page:
    they are per-question data, not furniture, and losing them would throw away
    the only reliable source of the correct option (see app/pdf/answers.py).
    """
    if not page_texts:
        return set()

    # Count PAGES a line appears on, not total occurrences, so a line printed
    # twice on one page does not count double toward the threshold.
    page_counts: Counter[str] = Counter()
    for text in page_texts:
        seen_on_this_page = {
            line.strip() for line in text.split("\n") if line.strip()
        }
        page_counts.update(seen_on_this_page)

    threshold = max(MIN_PAGES, int(len(page_texts) * MIN_PAGE_FRACTION))
    return {
        line
        for line, pages in page_counts.items()
        if pages >= threshold and len(line) <= MAX_BOILERPLATE_LEN and not looks_like_answer_line(line)
    }


def strip_boilerplate(page_text: str, boilerplate: set[str]) -> str:
    kept = [line for line in page_text.split("\n") if line.strip() not in boilerplate]
    return "\n".join(kept)


def neutralize_injection_markers(page_text: str) -> str:
    """Defang text that could pass itself off as prompt structure.

    Deliberately narrow -- see the module docstring on why prose filtering is
    not attempted here.
    """
    text = _ROLE_LINE_RE.sub(lambda m: f"[{m.group(1)}]", page_text)
    text = _FENCE_RE.sub("", text)
    return _NONCE_MARKER_RE.sub("[marker removed]", text)


def normalize_whitespace(page_text: str) -> str:
    """Collapse the blank-line runs that boilerplate removal leaves behind."""
    lines = [line.rstrip() for line in page_text.split("\n")]
    out: list[str] = []
    for line in lines:
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out).strip()


def clean_page_text(page_text: str, boilerplate: set[str]) -> str:
    """The single entry point, in the order the steps have to run."""
    text = strip_boilerplate(page_text, boilerplate)
    text = neutralize_injection_markers(text)
    return normalize_whitespace(text)
