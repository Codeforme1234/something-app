"""Object storage for question images and knowledge-base documents.

One Protocol (protocol.py) with a single S3 implementation. The Protocol stays
because it is what the services depend on and what the test suite substitutes
against; the local-filesystem implementation is gone, because it never touched
boto3 and so could not catch a bucket, credential or content-type mistake.

Each environment has its own bucket (`quizdeck-media-dev`, `quizdeck-media`),
and tests intercept boto3 in-process with moto.
"""

from functools import lru_cache

from app.services.storage.protocol import ObjectStore


@lru_cache
def get_object_store() -> ObjectStore:
    # Imported lazily so the boto3 S3 client is built on first use rather than
    # at import time, which keeps Settings construction errors readable.
    from app.services.storage.s3 import S3ObjectStore

    return S3ObjectStore()


def question_image_url(image_key: str | None) -> str | None:
    return get_object_store().public_url(image_key) if image_key else None
