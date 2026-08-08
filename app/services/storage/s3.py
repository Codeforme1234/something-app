"""S3-backed ObjectStore -- the only ObjectStore.

There is no local-filesystem alternative: it never touched boto3, so it could
not catch a bucket, credential or content-type mistake, and a container
filesystem is ephemeral, so every question image would vanish on the next
deploy. Each environment gets its own bucket instead, and the test suite
intercepts boto3 in-process with moto.

Builds its own boto3 client rather than sharing app/repositories/table.py's,
because the region and the credential profile are configured separately for
each service. Same shape as app/services/email/ses.py.
"""

import logging

import boto3
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.core.exceptions import NotFoundError, UpstreamError
from app.services.storage import urls

logger = logging.getLogger(__name__)

#: S3 rejects a DeleteObjects request with more keys than this.
_DELETE_BATCH = 1000

#: Object keys are immutable -- replacing an image mints a new ULID -- so a
#: far-future cache is safe and is what makes the direct-serve mode worthwhile.
_CACHE_CONTROL = "public, max-age=31536000, immutable"

#: Error codes S3 uses for "that key isn't there". get_object returns NoSuchKey;
#: head_object returns a bare 404 with no code.
_MISSING_CODES = {"NoSuchKey", "404", "NotFound"}


class S3ObjectStore:
    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.s3_bucket
        self._api_public_origin = settings.api_public_origin
        self._public_base_url = settings.image_public_base_url

        # profile_name=None is the normal chain, which is what a deployment
        # uses. Naming a profile is for local dev, where this machine's default
        # profile points at a different account -- and a wrong account here
        # surfaces as a late AccessDenied, not a fast failure. Same reasoning as
        # DYNAMO_PROFILE and SES_PROFILE.
        session = boto3.Session(profile_name=settings.s3_profile or None)
        self._client = session.client("s3", region_name=settings.aws_region)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                # Pinned from the key's extension by the caller, never taken from
                # the upload's own headers -- so S3 can never be talked into
                # serving these bytes as active content.
                ContentType=content_type,
                CacheControl=_CACHE_CONTROL,
            )
        except ClientError as exc:
            logger.warning("s3 put failed for %s: %s", key, exc)
            raise UpstreamError("could not store the file, please try again") from exc

    def get_bytes(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING_CODES:
                raise NotFoundError("object not found") from exc
            logger.warning("s3 get failed for %s: %s", key, exc)
            raise UpstreamError("could not read the file, please try again") from exc
        return response["Body"].read()

    def public_url(self, key: str) -> str:
        return urls.public_url_for(
            key,
            api_public_origin=self._api_public_origin,
            public_base_url=self._public_base_url,
        )

    def delete_many(self, keys: list[str]) -> None:
        """Best-effort, and deliberately never raises: an orphaned object is a
        storage-cost problem, while a raised error here would fail a teacher's
        delete after their rows are already gone."""
        for start in range(0, len(keys), _DELETE_BATCH):
            batch = keys[start : start + _DELETE_BATCH]
            try:
                self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
            except ClientError:
                logger.exception("s3 delete failed for %d key(s)", len(batch))
