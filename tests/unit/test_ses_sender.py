"""Unit tests for SesEmailSender. boto3's sesv2 client is mocked throughout
-- these tests never call real AWS. The expected payload shape below was
checked against botocore's actual sesv2 SendEmail input shape (FromEmailAddress
/ Destination.ToAddresses / Content.Simple.{Subject,Body.{Html,Text}}), which
is what app/services/email/ses.py sends -- so this suite also guards against
the payload silently drifting from the real API in the future.
"""

from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.services.email import ses as ses_module
from app.services.email.ses import SesEmailSender


def _settings(**overrides) -> Settings:
    defaults = dict(ses_from_address="noreply@quizdeck.example.com", aws_region="us-east-1")
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _mock_boto_client(monkeypatch) -> MagicMock:
    mock_client = MagicMock()
    monkeypatch.setattr(ses_module.boto3, "client", MagicMock(return_value=mock_client))
    return mock_client


def test_send_builds_the_sesv2_send_email_payload(monkeypatch):
    monkeypatch.setattr(ses_module, "get_settings", lambda: _settings())
    mock_client = _mock_boto_client(monkeypatch)

    SesEmailSender().send(
        to="student@example.com",
        subject="You're invited",
        html="<p>Hi</p>",
        text="Hi",
    )

    mock_client.send_email.assert_called_once_with(
        FromEmailAddress="noreply@quizdeck.example.com",
        Destination={"ToAddresses": ["student@example.com"]},
        Content={
            "Simple": {
                "Subject": {"Data": "You're invited", "Charset": "UTF-8"},
                "Body": {
                    "Html": {"Data": "<p>Hi</p>", "Charset": "UTF-8"},
                    "Text": {"Data": "Hi", "Charset": "UTF-8"},
                },
            }
        },
    )


def test_client_is_sesv2_in_the_configured_region(monkeypatch):
    monkeypatch.setattr(ses_module, "get_settings", lambda: _settings(aws_region="eu-west-1"))
    mock_boto_client = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(ses_module.boto3, "client", mock_boto_client)

    SesEmailSender()

    mock_boto_client.assert_called_once_with("sesv2", region_name="eu-west-1")


def test_from_address_comes_from_settings_not_the_caller(monkeypatch):
    monkeypatch.setattr(ses_module, "get_settings", lambda: _settings(ses_from_address="a@b.com"))
    mock_client = _mock_boto_client(monkeypatch)

    SesEmailSender().send(to="x@example.com", subject="s", html="h", text="t")

    assert mock_client.send_email.call_args.kwargs["FromEmailAddress"] == "a@b.com"


def test_missing_ses_from_address_raises_assertion_error(monkeypatch):
    monkeypatch.setattr(ses_module, "get_settings", lambda: _settings(ses_from_address=None))
    _mock_boto_client(monkeypatch)

    with pytest.raises(AssertionError, match="SES_FROM_ADDRESS"):
        SesEmailSender()
