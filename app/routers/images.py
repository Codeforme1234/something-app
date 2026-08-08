"""Serves uploaded question images.

Anonymous on purpose: the student take page has no bearer token (see
app/routers/take.py and AGENTS.md rule 5 on the web side), so an `<img src>` on
that page must resolve without one. The unguessable ULID key is the access
control -- the same posture the platform already takes with
app.core.ids.new_link_token, which alone authorizes a student's entire attempt
including every question stem.
"""

from fastapi import APIRouter
from fastapi.responses import Response

from app.core.exceptions import NotFoundError
from app.services.storage import get_object_store
from app.services.storage import keys as storage_keys

router = APIRouter(prefix="/images", tags=["images"])


@router.get("/{key:path}")
def get_question_image(key: str) -> Response:
    # Validate before the key reaches the store: this route serves bytes off a
    # path built from user input, so traversal here would read arbitrary files
    # off the host. 404 rather than 400 so the route never reveals whether a
    # rejected key was malformed or simply absent.
    if not storage_keys.is_valid_image_key(key):
        raise NotFoundError("image not found")

    data = get_object_store().get_bytes(key)
    return Response(
        content=data,
        media_type=storage_keys.content_type_for_key(key),
        headers={
            # The type is derived from the key we minted, never from the request
            # or the original upload headers; nosniff stops a browser
            # second-guessing it and executing the bytes as something else.
            "X-Content-Type-Options": "nosniff",
            # Keys are immutable -- replacing an image mints a new ULID -- so a
            # far-future cache is safe and keeps student traffic off this route.
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )
