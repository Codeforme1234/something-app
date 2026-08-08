"""Carriers for the PDF layer. Deliberately plain models with no behaviour --
every decision lives in a pure function in a sibling module so it is unit
testable without a PDF, a network, or a model call.
"""

from enum import StrEnum

from pydantic import BaseModel


class PageKind(StrEnum):
    #: Text extraction produced enough to work with; no model call needed.
    text = "text"
    #: Sparse or absent text, or a figure worth transcribing -- needs the
    #: vision pass to describe what a reader would see.
    vision = "vision"


class Figure(BaseModel):
    """One raster image embedded in a page, with where it sits on that page.

    `top` is what associates a figure with a question: questions run down the
    page, so the nearest question number *above* a figure owns it.
    """

    page_number: int
    top: float
    bottom: float
    #: PNG bytes, always re-rendered by us rather than passed through from the
    #: PDF's own image stream -- an embedded stream can be any format, and we
    #: serve these back from our own origin.
    png: bytes


class PdfPage(BaseModel):
    #: 1-based, matching how a human refers to pages.
    number: int
    raw_text: str
    clean_text: str = ""
    kind: PageKind = PageKind.text
    figure_count: int = 0


class PdfDocument(BaseModel):
    page_count: int
    pages: list[PdfPage]

    def text(self) -> str:
        """The whole document as one string, cleaned, for the extraction call."""
        return "\n".join(page.clean_text or page.raw_text for page in self.pages)

    def vision_pages(self) -> list[int]:
        return [page.number for page in self.pages if page.kind is PageKind.vision]
