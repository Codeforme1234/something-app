from datetime import UTC, datetime


def now() -> datetime:
    """Single source of server time — everything timing-related goes through here."""
    return datetime.now(UTC)
