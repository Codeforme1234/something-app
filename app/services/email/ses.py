"""Production email sender. Only imported when EMAIL_MODE=ses (see
app/services/email/__init__.py), so dev never needs sesv2 permissions."""

import boto3

from app.core.config import get_settings


class SesEmailSender:
    def __init__(self) -> None:
        settings = get_settings()
        assert settings.ses_from_address, "EMAIL_MODE=ses requires SES_FROM_ADDRESS"
        self._from_address = settings.ses_from_address
        # profile_name=None is boto3's normal credential chain, which is what a
        # deployment uses; SES_PROFILE is the local-dev escape hatch (see
        # Settings.ses_profile). SES_REGION falls back to AWS_REGION.
        # `or None` so an explicitly blanked SES_PROFILE means "the normal
        # chain", not a lookup for a profile literally named "" -- which
        # botocore answers with ProfileNotFound. Matches DYNAMO_PROFILE/S3_PROFILE.
        session = boto3.Session(profile_name=settings.ses_profile or None)
        self._client = session.client(
            "sesv2", region_name=settings.ses_region or settings.aws_region
        )

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
