"""Production email sender. Only imported when EMAIL_MODE=ses (see
app/services/email/__init__.py), so dev never needs sesv2 permissions."""

import boto3

from app.core.config import get_settings


class SesEmailSender:
    def __init__(self) -> None:
        settings = get_settings()
        assert settings.ses_from_address, "EMAIL_MODE=ses requires SES_FROM_ADDRESS"
        self._from_address = settings.ses_from_address
        self._client = boto3.client("sesv2", region_name=settings.aws_region)

    def send(self, to: str, subject: str, html: str, text: str) -> None:
        self._client.send_email(
            FromEmailAddress=self._from_address,
            Destination={"ToAddresses": [to]},
            Content={
                "Simple": {
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": html, "Charset": "UTF-8"},
                        "Text": {"Data": text, "Charset": "UTF-8"},
                    },
                }
            },
        )
