from typing import Protocol


class ObjectStore(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        """Write `data` under `key`, creating any parent structure the
        backend needs. Overwrites silently if `key` already exists -- keys
        are ULID-derived (see app/services/storage/keys.py) so a real
        collision never happens in practice."""
        ...

    def get_bytes(self, key: str) -> bytes:
        """Read back the bytes stored at `key`. Raise
        app.core.exceptions.NotFoundError if `key` was never written (or was
        since deleted) -- callers should not have to distinguish "missing"
        from any other failure mode."""
        ...

    def public_url(self, key: str) -> str:
        """Build the URL a browser uses to fetch `key`. Pure string
        building, no I/O and no existence check -- callers that need to know
        whether the object is actually there should call get_bytes."""
        ...

    def delete_many(self, keys: list[str]) -> None:
        """Best-effort cleanup for a batch of keys, e.g. when a question is
        edited and its old image is replaced. Never raises: a stray orphaned
        blob is a disk-usage problem, not a correctness one, so a failed
        delete here must not fail the caller's request."""
        ...
