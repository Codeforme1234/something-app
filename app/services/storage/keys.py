"""Object-store key shapes for question images.

These are deliberately not in app/repositories/keys.py: that module is only
DynamoDB PK/SK strings for the single table (rule 1 in CLAUDE.md). A
question-image key is a *filesystem/S3* path, addressed by a different
store entirely (app/services/storage/protocol.py), so mixing the two would
blur "which store does this key belong to" for no benefit.

Two key shapes:

    tests/<test_id>/q/<ULID>.<ext>     question images (see below)
    kb/<owner-hash>/<ULID>.<ext>       source documents a test is generated from

Both are validated with an allowlist regex rather than by rejecting known-bad
characters, so path traversal, absolute paths, query strings, and double
extensions are impossible by construction instead of by enumeration.
"""

import hashlib
import re

from app.core.ids import new_ulid

CONTENT_TYPE_EXTENSIONS: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
_EXTENSION_CONTENT_TYPES: dict[str, str] = {ext: content_type for content_type, ext in CONTENT_TYPE_EXTENSIONS.items()}

# Crockford base32, the alphabet python-ulid encodes with (excludes I, L, O, U
# so a ULID is never visually confused with a different one).
_ULID_PATTERN = "[0-9A-HJKMNP-TV-Z]{26}"
_EXTENSION_PATTERN = "|".join(CONTENT_TYPE_EXTENSIONS.values())
_IMAGE_TAIL_RE = re.compile(rf"^{_ULID_PATTERN}\.({_EXTENSION_PATTERN})$")
_IMAGE_KEY_RE = re.compile(rf"^tests/{_ULID_PATTERN}/q/{_ULID_PATTERN}\.({_EXTENSION_PATTERN})$")


def question_image_prefix(test_id: str) -> str:
    return f"tests/{test_id}/q/"


def new_question_image_key(test_id: str, content_type: str) -> str:
    """Mint a fresh key for an upload to `test_id`.

    Raises:
        ValueError: If `content_type` is not one of CONTENT_TYPE_EXTENSIONS.
    """
    try:
        ext = CONTENT_TYPE_EXTENSIONS[content_type]
    except KeyError:
        raise ValueError(f"unsupported image content type: {content_type}") from None
    return f"{question_image_prefix(test_id)}{new_ulid()}.{ext}"


def belongs_to_test(key: str, test_id: str) -> bool:
    """True iff `key` is exactly `question_image_prefix(test_id)` followed by
    a ULID + known extension. This is the check a write path (attach/delete
    an image on one of *this* test's questions) uses before trusting a key
    supplied by the caller -- an allowlist, not a blocklist, so there is no
    "did I think of every bad character" gap."""
    prefix = question_image_prefix(test_id)
    if not key.startswith(prefix):
        return False
    return bool(_IMAGE_TAIL_RE.match(key[len(prefix) :]))


def is_valid_image_key(key: str) -> bool:
    """True iff `key` has the full tests/<ULID>/q/<ULID>.<ext> shape, for the
    serve route, which has no test_id in hand to scope a belongs_to_test
    check -- it only knows the key requested."""
    return bool(_IMAGE_KEY_RE.match(key))


def content_type_for_key(key: str) -> str:
    """Reverse-map a key's extension back to a content type, e.g. for the
    Content-Type header on the serve route.

    Raises:
        ValueError: If `key` has no extension in CONTENT_TYPE_EXTENSIONS.
    """
    ext = key.rsplit(".", 1)[-1] if "." in key else ""
    try:
        return _EXTENSION_CONTENT_TYPES[ext]
    except KeyError:
        raise ValueError(f"unknown image extension for key: {key}") from None


# --- knowledge-base source files ---------------------------------------------
#
# The document a teacher generates a test FROM: a question paper, a syllabus
# page, a photo of a worksheet. Keyed by teacher rather than by test, because
# the upload happens before any test exists.
#
# The owner segment is a HASH of the sub, not the sub itself. That keeps the key
# path-safe whatever a Cognito sub contains, keeps the identity out of storage
# access logs, and still lets ownership be checked with a pure string compare and
# zero lookups (CLAUDE.md rule 3).

KB_PREFIX = "kb"

KB_CONTENT_TYPE_EXTENSIONS: dict[str, str] = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "text/plain": "txt",
    "text/markdown": "md",
}
_KB_EXTENSION_CONTENT_TYPES: dict[str, str] = {
    # png/jpg/webp appear in both maps with the same meaning; dict order means
    # the first spelling of each extension wins, which is what we want.
    ext: content_type
    for content_type, ext in KB_CONTENT_TYPE_EXTENSIONS.items()
}
_KB_EXTENSION_PATTERN = "|".join(KB_CONTENT_TYPE_EXTENSIONS.values())
_OWNER_PATTERN = "[0-9a-f]{32}"
_KB_KEY_RE = re.compile(rf"^{KB_PREFIX}/{_OWNER_PATTERN}/{_ULID_PATTERN}\.({_KB_EXTENSION_PATTERN})$")


def owner_segment(teacher_sub: str) -> str:
    """Stable, path-safe, non-identifying namespace for one teacher."""
    return hashlib.sha256(teacher_sub.encode("utf-8")).hexdigest()[:32]


def knowledge_base_prefix(teacher_sub: str) -> str:
    return f"{KB_PREFIX}/{owner_segment(teacher_sub)}/"


def new_knowledge_base_key(teacher_sub: str, content_type: str) -> str:
    """Mint a fresh key for a source document uploaded by `teacher_sub`.

    Raises:
        ValueError: If `content_type` is not one of KB_CONTENT_TYPE_EXTENSIONS.
    """
    try:
        ext = KB_CONTENT_TYPE_EXTENSIONS[content_type]
    except KeyError:
        raise ValueError(f"unsupported knowledge-base content type: {content_type}") from None
    return f"{knowledge_base_prefix(teacher_sub)}{new_ulid()}.{ext}"


def kb_belongs_to_teacher(key: str, teacher_sub: str) -> bool:
    """True iff `key` is a well-formed knowledge-base key in this teacher's
    namespace. An allowlist, so traversal, absolute paths, query strings, and
    double extensions are impossible by construction -- and a cross-tenant key
    fails on a string comparison without touching the store."""
    if not _KB_KEY_RE.match(key):
        return False
    return key.startswith(knowledge_base_prefix(teacher_sub))


def is_valid_knowledge_base_key(key: str) -> bool:
    return bool(_KB_KEY_RE.match(key))


def kb_content_type_for_key(key: str) -> str:
    """Reverse-map a knowledge-base key's extension back to a content type.

    Raises:
        ValueError: If the extension is not one we store.
    """
    ext = key.rsplit(".", 1)[-1] if "." in key else ""
    try:
        return _KB_EXTENSION_CONTENT_TYPES[ext]
    except KeyError:
        raise ValueError(f"unknown knowledge-base extension for key: {key}") from None
