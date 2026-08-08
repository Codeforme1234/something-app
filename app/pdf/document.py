"""Turn PDF bytes into text and PNGs.

The only impure module in app/pdf/, and even here the I/O is entirely
in-memory: no filesystem, no network. Everything that makes a *decision* lives
in a pure sibling module.

Two libraries, both permissively licensed:
  - pdfplumber (MIT) for text and for where images sit on a page.
  - pypdfium2 (BSD-3/Apache) for rasterizing.

PyMuPDF is deliberately NOT used despite docs/PLAN.md naming it: it is AGPL-3.0
or paid, and AGPL section 13's network-interaction clause is triggered by exactly
what a SaaS backend does.

Figures are always re-rendered by us rather than passed through from the PDF's
own image stream. An embedded stream can be any format, including one a browser
would treat as active content, and these bytes get served back from our origin.
"""

import io
import logging
import re

import pdfplumber
import pypdfium2
from PIL import Image

from app.core.exceptions import BadRequestError
from app.pdf import classify, cleanup
from app.pdf.models import Figure, PdfDocument, PdfPage

logger = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF-"
#: Enough to read a circuit diagram without paying for a huge PNG.
DEFAULT_RENDER_DPI = 150
#: Figures get a little more, since they are the whole point of the crop.
FIGURE_RENDER_DPI = 200
#: PDF user space is 72 points per inch.
_POINTS_PER_INCH = 72
#: Ignore decorative hairlines and spacer images that are not real figures.
MIN_FIGURE_POINTS = 24
#: A standalone `Q37.` token, as pdfplumber tokenizes a question header.
_HEADER_WORD_RE = re.compile(r"^Q(\d{1,3})[.)\]:]?$")


def _require_pdf(data: bytes) -> None:
    """The uploaded Content-Type is a client claim; these bytes are the truth."""
    if not data.startswith(_PDF_MAGIC):
        raise BadRequestError("that file could not be read as a PDF")


def open_document(data: bytes, *, max_pages: int) -> PdfDocument:
    """Extract text per page, clean it, and classify each page.

    Raises BadRequestError for bytes that are not a readable PDF, and for a
    document longer than `max_pages` -- both are the teacher's problem to fix,
    not an internal error.
    """
    _require_pdf(data)

    try:
        with pdfplumber.open(io.BytesIO(data)) as doc:
            page_count = len(doc.pages)
            if page_count == 0:
                raise BadRequestError("that PDF has no pages")
            if page_count > max_pages:
                raise BadRequestError(
                    f"that PDF is {page_count} pages; the limit is {max_pages}"
                )
            raw = [
                (page.extract_text() or "", _significant_image_count(page))
                for page in doc.pages
            ]
    except BadRequestError:
        raise
    except Exception as exc:
        # Encrypted, truncated, or malformed past pdfplumber's tolerance.
        logger.warning("failed to open PDF: %s", exc)
        raise BadRequestError("that PDF could not be read; it may be corrupt or password-protected") from exc

    page_texts = [text for text, _ in raw]
    boilerplate = cleanup.find_boilerplate_lines(page_texts)

    pages: list[PdfPage] = []
    for index, (text, figure_count) in enumerate(raw, start=1):
        clean = cleanup.clean_page_text(text, boilerplate)
        pages.append(
            PdfPage(
                number=index,
                raw_text=text,
                clean_text=clean,
                kind=classify.classify_page(clean, figure_count=figure_count),
                figure_count=figure_count,
            )
        )

    return PdfDocument(page_count=page_count, pages=pages)


def _significant_image_count(page) -> int:
    """Embedded images big enough to be a real diagram."""
    return sum(
        1
        for image in page.images
        if (image["x1"] - image["x0"]) >= MIN_FIGURE_POINTS
        and (image["bottom"] - image["top"]) >= MIN_FIGURE_POINTS
    )


def question_header_tops(data: bytes, page_number: int) -> list[tuple[int, float]]:
    """(question number, y position in points) for each `Q<n>.` header on a page.

    Real coordinates, from the same space as Figure.top, so a figure can be
    attributed to the question above it by direct comparison. Deriving the
    header position from a line index instead would mix two coordinate systems
    and mis-attribute figures on any page with uneven line heights.
    """
    with pdfplumber.open(io.BytesIO(data)) as doc:
        if not (1 <= page_number <= len(doc.pages)):
            return []
        words = doc.pages[page_number - 1].extract_words()

    headers: list[tuple[int, float]] = []
    for word in words:
        match = _HEADER_WORD_RE.match(word["text"])
        if match:
            headers.append((int(match.group(1)), float(word["top"])))
    headers.sort(key=lambda pair: pair[1])
    return headers


def render_page_png(data: bytes, page_number: int, *, dpi: int = DEFAULT_RENDER_DPI) -> bytes:
    """Rasterize one 1-based page to PNG, for the vision pass."""
    _require_pdf(data)
    image = _render(data, page_number, dpi)
    return _to_png(image)


def extract_figures(data: bytes, page_number: int, *, dpi: int = FIGURE_RENDER_DPI) -> list[Figure]:
    """Crop every significant embedded image on a page to its own PNG.

    Returns them top-to-bottom, which is the order the caller needs to associate
    each with the question above it.
    """
    _require_pdf(data)

    with pdfplumber.open(io.BytesIO(data)) as doc:
        if not (1 <= page_number <= len(doc.pages)):
            return []
        page = doc.pages[page_number - 1]
        boxes = [
            (image["x0"], image["top"], image["x1"], image["bottom"])
            for image in page.images
            if (image["x1"] - image["x0"]) >= MIN_FIGURE_POINTS
            and (image["bottom"] - image["top"]) >= MIN_FIGURE_POINTS
        ]

    if not boxes:
        return []

    rendered = _render(data, page_number, dpi)
    scale = dpi / _POINTS_PER_INCH

    figures: list[Figure] = []
    for x0, top, x1, bottom in sorted(boxes, key=lambda b: b[1]):
        crop = rendered.crop(
            (
                max(0, int(x0 * scale)),
                max(0, int(top * scale)),
                min(rendered.width, int(x1 * scale)),
                min(rendered.height, int(bottom * scale)),
            )
        )
        if crop.width == 0 or crop.height == 0:
            continue
        figures.append(
            Figure(page_number=page_number, top=top, bottom=bottom, png=_to_png(crop))
        )
    return figures


def _render(data: bytes, page_number: int, dpi: int) -> Image.Image:
    pdf = pypdfium2.PdfDocument(data)
    try:
        if not (1 <= page_number <= len(pdf)):
            raise BadRequestError(f"page {page_number} is outside this PDF")
        page = pdf[page_number - 1]
        # pypdfium2's scale is relative to 72 dpi, which is PDF user space.
        return page.render(scale=dpi / _POINTS_PER_INCH).to_pil().convert("RGB")
    finally:
        pdf.close()


def _to_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
