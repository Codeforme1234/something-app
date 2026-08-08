"""Source documents a test is generated from: store the file, read its text.

The browser cannot do this itself. A PDF needs a parser and a photo of a
worksheet needs vision, so both have to be read server-side -- unlike the
original .txt/.md flow, which read the file with `file.text()` in the page.

The file is kept (not discarded) so the teacher can see and re-open what a test
was generated from. It goes through the ObjectStore Protocol, so it lands in a
local folder in dev and in S3 in prod with no code change.

What "read its text" means depends on the type:

    .txt / .md   decode as UTF-8. Free.
    .pdf         pdfplumber text extraction, plus the same boilerplate stripping
                 the extraction pipeline uses. Free -- no model call. Falls back
                 to vision only for pages with too little extractable text.
    image        vision transcription. Costs a model call; there is no other way
                 to read a photograph.
"""

import logging

from pydantic import BaseModel

from app.core.exceptions import BadRequestError, NotFoundError
from app.llm import get_question_extractor
from app.pdf import classify, document
from app.services.storage import get_object_store
from app.services.storage import keys as storage_keys
from app.services.storage import signatures

logger = logging.getLogger(__name__)

#: Mirrors GenerateQuestionsRequest.knowledge_base's cap, so what we hand back
#: always fits in the field it is destined for.
MAX_TEXT_CHARS = 20_000

_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


class KnowledgeBaseUpload(BaseModel):
    """What the generate page needs after an upload: a link to the stored file,
    and the text to send with the generation request."""

    file_key: str
    file_url: str
    file_name: str
    content_type: str
    text: str
    char_count: int
    #: True when the document was longer than MAX_TEXT_CHARS and got cut.
    truncated: bool
    #: True when reading the file required a model call (images, and PDFs with
    #: unextractable pages). Surfaced so the UI can warn before the upload.
    used_vision: bool


def ingest(
    teacher_sub: str, file_name: str, content_type: str | None, data: bytes, *, max_pdf_pages: int
) -> KnowledgeBaseUpload:
    """Validate, store, and read one source document."""
    if content_type not in storage_keys.KB_CONTENT_TYPE_EXTENSIONS:
        raise BadRequestError("unsupported file type; use a PDF, an image, or a .txt/.md file")
    if not data:
        raise BadRequestError("that file is empty")

    _verify_content(data, content_type)

    text, used_vision = _read_text(data, content_type, max_pdf_pages=max_pdf_pages)
    text = text.strip()
    if not text:
        raise BadRequestError("no readable text could be found in that file")

    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS]

    # Stored only after the read succeeds: a file we cannot read is not worth
    # keeping, and this way a rejected upload leaves nothing behind.
    key = storage_keys.new_knowledge_base_key(teacher_sub, content_type)
    store = get_object_store()
    store.put_bytes(key, data, content_type)

    return KnowledgeBaseUpload(
        file_key=key,
        file_url=store.public_url(key),
        file_name=file_name,
        content_type=content_type,
        text=text,
        char_count=len(text),
        truncated=truncated,
        used_vision=used_vision,
    )


def _verify_content(data: bytes, content_type: str) -> None:
    """The declared type is a client claim; these bytes are the truth."""
    if content_type in signatures.UNVERIFIABLE_TYPES:
        # No signature to check, but a binary file renamed .txt would reach the
        # model as mojibake, so at least require it to be text.
        if not signatures.is_decodable_text(data):
            raise BadRequestError("that file is not readable as text")
        return
    if not signatures.matches_declared_type(data, content_type):
        raise BadRequestError("file content does not match its type")


def _read_text(data: bytes, content_type: str, *, max_pdf_pages: int) -> tuple[str, bool]:
    if content_type in signatures.UNVERIFIABLE_TYPES:
        return data.decode("utf-8"), False
    if content_type == "application/pdf":
        return _read_pdf(data, max_pdf_pages=max_pdf_pages)
    if content_type in _IMAGE_TYPES:
        # A photograph has no text layer at all -- vision is the only option.
        return get_question_extractor().transcribe_page(data, 1), True
    raise BadRequestError("unsupported file type")


def _read_pdf(data: bytes, *, max_pdf_pages: int) -> tuple[str, bool]:
    """Extracted text, falling back to vision only for the pages that need it.

    Most question papers have a full text layer, so the common case costs
    nothing. A scanned page has no text to extract, and silently returning a
    blank page would produce questions about nothing.
    """
    doc = document.open_document(data, max_pages=max_pdf_pages)

    sparse = [
        page.number
        for page in doc.pages
        if len((page.clean_text or page.raw_text).strip()) < classify.TEXT_DENSITY_THRESHOLD
    ]
    if not sparse:
        return doc.text(), False

    logger.info("transcribing %d unextractable page(s) of a knowledge-base PDF", len(sparse))
    extractor = get_question_extractor()
    parts: list[str] = []
    used_vision = False
    for page in doc.pages:
        body = page.clean_text or page.raw_text
        if page.number not in sparse:
            parts.append(body)
            continue
        try:
            png = document.render_page_png(data, page.number)
            parts.append(extractor.transcribe_page(png, page.number))
            used_vision = True
        except Exception as exc:
            # One unreadable page must not lose the rest of the document.
            logger.warning("page %d transcription failed: %s", page.number, exc)
            parts.append(body)
    return "\n".join(parts), used_vision


def read_stored(teacher_sub: str, key: str) -> tuple[bytes, str]:
    """Fetch a stored source document for its owner. Returns (bytes, content_type).

    Ownership is a pure string compare against the key's own namespace -- no
    lookup, and a cross-tenant key fails before the store is touched.
    """
    if not storage_keys.kb_belongs_to_teacher(key, teacher_sub):
        # 404 rather than 403: someone else's key must be indistinguishable from
        # one that never existed, the same way tests_repo.get_test makes a
        # non-owned test simply miss (CLAUDE.md rule 3).
        raise NotFoundError("file not found")
    return get_object_store().get_bytes(key), storage_keys.kb_content_type_for_key(key)
