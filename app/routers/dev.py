"""Dev-only endpoints. Mounted by main.py only when APP_ENV=dev."""

import json
from pathlib import Path

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(prefix="/dev", tags=["dev"])


@router.get("/outbox")
def list_outbox() -> list[dict]:
    """Mock inbox: the emails the outbox sender wrote, newest first."""
    outbox = Path(get_settings().outbox_dir)
    if not outbox.exists():
        return []
    messages = []
    for path in sorted(outbox.glob("*.json"), reverse=True):
        try:
            messages.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return messages
