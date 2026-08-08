"""Magic-byte validation for uploaded images.

A declared Content-Type header is just a claim by the client; nothing stops
an upload from naming its bytes "image/png" while actually shipping HTML or
JavaScript. This check is what makes serving those bytes back from our own
origin (app/routers/images.py, later) safe: the serve route sits on the same
origin as the bearer token used for auth, so if it ever served attacker
content with a browser-executable type, that content would run with this
app's credentials. Checking the real magic bytes against the declared type
closes that off before anything is written to disk.
"""

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_WEBP_RIFF = b"RIFF"
_WEBP_TAG = b"WEBP"
_PDF_MAGIC = b"%PDF-"

#: Types whose bytes carry no checkable signature. Plain text and markdown are
#: any bytes at all, so there is nothing to verify -- which is safe only because
#: neither is ever served back as a rendered document.
UNVERIFIABLE_TYPES = frozenset({"text/plain", "text/markdown"})


def matches_declared_type(data: bytes, content_type: str) -> bool:
    """True iff `data` starts with the magic bytes for `content_type`.

    Returns False (never raises) for an unrecognized content type or data
    too short to contain the relevant magic bytes -- both are just "not a
    match" from the caller's point of view. Types in UNVERIFIABLE_TYPES also
    return False; callers must decide explicitly to skip the check for those
    rather than get a silent pass here.
    """
    if content_type == "image/png":
        return data.startswith(_PNG_MAGIC)
    if content_type == "image/jpeg":
        return data.startswith(_JPEG_MAGIC)
    if content_type == "image/webp":
        # RIFF <4-byte size> WEBP -- the size field is bytes 4:8, so the
        # format tag itself sits at 8:12.
        return data[:4] == _WEBP_RIFF and data[8:12] == _WEBP_TAG
    if content_type == "application/pdf":
        return data.startswith(_PDF_MAGIC)
    return False


def is_decodable_text(data: bytes) -> bool:
    """True iff `data` is valid UTF-8. The closest thing to a signature check a
    .txt/.md upload has: it rejects a binary file renamed to .txt, which would
    otherwise reach the model as mojibake."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True
