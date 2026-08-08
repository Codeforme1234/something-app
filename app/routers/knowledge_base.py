"""Upload and re-read the source document a test is generated from.

Both routes are teacher-only, unlike app/routers/images.py: a question image has
to be readable by an anonymous student taking the test, whereas a teacher's
source document has no reason to leave their own account.
"""

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import Response

from app.auth.dependencies import get_current_teacher
from app.auth.protocol import TeacherClaims
from app.core.config import get_settings
from app.core.exceptions import BadRequestError
from app.services import knowledge_base
from app.services.knowledge_base import KnowledgeBaseUpload

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


# async def, unlike most routes in this app, because UploadFile.read is a
# coroutine -- same reason as the question-image upload in app/routers/tests.py.
@router.post("", response_model=KnowledgeBaseUpload, status_code=status.HTTP_201_CREATED)
async def upload_knowledge_base(
    file: UploadFile = File(...),
    claims: TeacherClaims = Depends(get_current_teacher),
) -> KnowledgeBaseUpload:
    settings = get_settings()
    max_bytes = settings.max_pdf_bytes
    # One byte past the limit, so an oversized upload is detectable. The
    # body-size middleware only inspects Content-Length, which a client can
    # understate, so this is the real ceiling.
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise BadRequestError(f"file must be at most {max_bytes // 1_000_000} MB")

    return knowledge_base.ingest(
        claims.sub,
        file.filename or "upload",
        file.content_type,
        data,
        max_pdf_pages=settings.max_pdf_pages,
    )


@router.get("/{key:path}")
def get_knowledge_base_file(
    key: str, claims: TeacherClaims = Depends(get_current_teacher)
) -> Response:
    data, content_type = knowledge_base.read_stored(claims.sub, key)
    return Response(
        content=data,
        media_type=content_type,
        headers={
            # The type comes from the key we minted, never from the request.
            "X-Content-Type-Options": "nosniff",
            # inline so a PDF opens in the browser's viewer rather than
            # downloading, which is what "re-open what I generated from" wants.
            "Content-Disposition": "inline",
        },
    )
