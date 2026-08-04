import secrets

from ulid import ULID


def new_ulid() -> str:
    return str(ULID())


def new_link_token() -> str:
    # ~144 bits of entropy; this token alone authorizes a student session
    return secrets.token_urlsafe(24)
