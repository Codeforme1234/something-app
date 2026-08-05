"""Dev email "sender": writes one JSON file per message instead of sending
it, so `GET /api/v1/dev/outbox` (app/routers/dev.py) can list them as a mock
inbox.
"""

import json
import logging
import re
import secrets
from pathlib import Path

from app.core.clock import now
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# The link is built elsewhere as f"{frontend_origin}/t/{token}"; re-deriving
# that exact shape here is simpler and more precise than trying to sniff out
# "any URL" from arbitrary email HTML/text.
_TOKEN_CHARS = r"[A-Za-z0-9_\-]+"


def _extract_student_link(html: str, text: str, frontend_origin: str) -> str | None:
    pattern = re.compile(re.escape(frontend_origin) + r"/t/" + _TOKEN_CHARS)
    match = pattern.search(text) or pattern.search(html)
    return match.group(0) if match else None


class OutboxEmailSender:
    def send(self, to: str, subject: str, html: str, text: str) -> None:
        settings = get_settings()
        outbox_dir = Path(settings.outbox_dir)
        outbox_dir.mkdir(parents=True, exist_ok=True)

        sent_at = now()
        message = {
            "to": to,
            "subject": subject,
            "html": html,
            "text": text,
            "sent_at": sent_at.isoformat(),
            "student_link": _extract_student_link(html, text, settings.frontend_origin),
        }

        # Sortable filename: epoch-millis prefix (newest sorts last, so
        # app/routers/dev.py reverses the glob to list newest-first) plus a
        # random suffix so two sends in the same millisecond don't collide.
        filename = f"{int(sent_at.timestamp() * 1000):016d}-{secrets.token_hex(4)}.json"
        (outbox_dir / filename).write_text(json.dumps(message, indent=2))
        logger.info("outbox: wrote %s to %s", filename, to)
