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


def _mock_session(monkeypatch) -> MagicMock:
    """Patches boto3.Session and returns the mock Session *class*, so tests can
    assert on both the session (profile) and the client (service, region)."""
    mock_session_cls = MagicMock()
    monkeypatch.setattr(ses_module.boto3, "Session", mock_session_cls)
    return mock_session_cls


def _mock_boto_client(monkeypatch) -> MagicMock:
    return _mock_session(monkeypatch).return_value.client.return_value


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
    mock_session_cls = _mock_session(monkeypatch)

    SesEmailSender()

    mock_session_cls.return_value.client.assert_called_once_with(
        "sesv2", region_name="eu-west-1"
    )


def test_ses_region_overrides_aws_region(monkeypatch):
    # The verified identity does not have to live in the table's region.
    monkeypatch.setattr(
        ses_module,
        "get_settings",
        lambda: _settings(aws_region="us-east-1", ses_region="ap-south-1"),
    )
    mock_session_cls = _mock_session(monkeypatch)

    SesEmailSender()

    mock_session_cls.return_value.client.assert_called_once_with(
        "sesv2", region_name="ap-south-1"
    )


def test_no_profile_configured_uses_the_default_credential_chain(monkeypatch):
    monkeypatch.setattr(ses_module, "get_settings", lambda: _settings())
    mock_session_cls = _mock_session(monkeypatch)

    SesEmailSender()

    mock_session_cls.assert_called_once_with(profile_name=None)


def test_ses_profile_selects_that_named_profile(monkeypatch):
    monkeypatch.setattr(ses_module, "get_settings", lambda: _settings(ses_profile="quizdeck"))
    mock_session_cls = _mock_session(monkeypatch)

    SesEmailSender()

    mock_session_cls.assert_called_once_with(profile_name="quizdeck")


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
