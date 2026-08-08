"""Decide, per page, whether the model needs to *look* at it.

Text pages cost input tokens. Vision pages cost far more, so the whole point is
to route as few pages there as possible. On a typical answer-key paper that is
the handful of pages carrying circuit diagrams and graphs.
"""

from app.pdf.models import PageKind

#: Below this many characters of extractable text, a page is assumed to be
#: scanned or graphics-only and has to be read visually.
TEXT_DENSITY_THRESHOLD = 40


def classify_page(clean_text: str, *, figure_count: int) -> PageKind:
    """Vision when there is too little text to work with, or when the page
    carries a figure whose meaning is not in the text at all."""
    if len(clean_text.strip()) < TEXT_DENSITY_THRESHOLD:
        return PageKind.vision
    if figure_count > 0:
        return PageKind.vision
    return PageKind.text
