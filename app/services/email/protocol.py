from typing import Protocol


class EmailSender(Protocol):
    def send(self, to: str, subject: str, html: str, text: str) -> None:
        """Send one email. Raise on transport failure -- callers (see
        app/services/student_service.py) decide whether a failed send should
        be swallowed, since the invitation record must survive it either way."""
        ...
