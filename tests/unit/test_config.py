import pytest
from pydantic import ValidationError

from app.core.config import Settings


#: The two fields that have no default, since every environment now names real
#: AWS resources. Anything constructing Settings has to supply them.
RESOURCES = {"table_name": "quizdeck-test", "s3_bucket": "quizdeck-media-test"}


def test_prod_refuses_fake_auth():
    with pytest.raises(ValueError, match="mock modes"):
        Settings(
            app_env="prod",
            auth_mode="fake",
            email_mode="ses",
            ses_from_address="a@b.com",
            llm_mode="openai",
            openai_api_key="sk-test",
            _env_file=None,
            **RESOURCES,
        )


@pytest.mark.parametrize("field", ["table_name", "s3_bucket"])
def test_the_resource_names_have_no_default(field, monkeypatch):
    """A default would be a real table or bucket that an environment with an
    incomplete env file silently reads and writes. Fail at startup instead."""
    # tests/conftest.py puts both in os.environ for the whole session, and
    # pydantic-settings reads os.environ even with _env_file=None.
    monkeypatch.delenv(field.upper(), raising=False)
    supplied = {k: v for k, v in RESOURCES.items() if k != field}

    with pytest.raises(ValidationError, match=field):
        Settings(_env_file=None, **supplied)


def test_cognito_mode_requires_pool_settings():
    with pytest.raises(ValueError, match="COGNITO_USER_POOL_ID"):
        Settings(auth_mode="cognito", _env_file=None, **RESOURCES)


def test_dev_defaults_to_mock_auth_email_and_llm():
    """Storage has no mock any more -- only auth, email and the LLM do."""
    settings = Settings(_env_file=None, **RESOURCES)
    assert (
        settings.app_env,
        settings.auth_mode,
        settings.email_mode,
        settings.llm_mode,
    ) == ("dev", "fake", "outbox", "fake")


@pytest.mark.parametrize(
    ("field", "value"),
    [("table_name", "quizdeck-prod"), ("s3_bucket", "quizdeck-media")],
)
def test_dev_refuses_to_point_at_production_resources(field, value):
    """The one mistake the two-file env split exists to prevent, and the silent
    one: writes succeed, against the wrong data."""
    config = {**RESOURCES, field: value}

    with pytest.raises(ValueError, match="must not point at production"):
        Settings(app_env="dev", _env_file=None, **config)


def test_prod_may_of_course_point_at_production_resources():
    Settings(**_real_config(table_name="quizdeck-prod", s3_bucket="quizdeck-media"))


def _real_config(**overrides) -> dict:
    """The exact combination docs/aws-setup.md tells an operator to flip to."""
    config = {
        "app_env": "prod",
        "frontend_origin": "https://app.example.com",
        "auth_mode": "cognito",
        "cognito_user_pool_id": "us-east-1_Pool123",
        "cognito_client_id": "client-abc",
        "cognito_region": "us-east-1",
        "email_mode": "ses",
        "ses_from_address": "noreply@quizdeck.example.com",
        "llm_mode": "openai",
        "openai_api_key": "sk-live-test",
        "openai_model": "gpt-4o-mini",
        "openai_extraction_model": "gpt-5.6-terra",
        "table_name": "quizdeck-prod",
        "s3_bucket": "quizdeck-media",
        "_env_file": None,
    }
    config.update(overrides)
    return config


@pytest.mark.parametrize(
    ("field", "env_var"),
    [
        ("openai_api_key", "OPENAI_API_KEY"),
        ("openai_model", "OPENAI_MODEL"),
        ("openai_extraction_model", "OPENAI_EXTRACTION_MODEL"),
    ],
)
def test_openai_mode_requires_the_key_and_both_models(field, env_var, monkeypatch):
    """Neither model has a code default. A default would mean the model actually
    billed is whatever was compiled into the image, not what the env file says."""
    monkeypatch.delenv(env_var, raising=False)

    with pytest.raises(ValueError, match=env_var):
        Settings(**_real_config(**{field: None}))


def test_fake_llm_mode_needs_no_model():
    """Fake mode never calls OpenAI, so it must not have to name a model."""
    settings = Settings(llm_mode="fake", _env_file=None, **RESOURCES)

    assert settings.openai_model is None
    assert settings.openai_extraction_model is None


def test_openai_client_knobs_are_configurable():
    """Timeout and retries used to be module constants in app/llm/client.py --
    the two knobs you reach for during an incident, needing a deploy to change."""
    settings = Settings(**_real_config(openai_timeout_seconds=12.5, openai_max_retries=4))

    assert (settings.openai_timeout_seconds, settings.openai_max_retries) == (12.5, 4)
    # Extraction keeps its own, much longer deadline: one call reads a whole paper.
    assert settings.openai_extraction_timeout_seconds > settings.openai_timeout_seconds


def test_prod_accepts_a_fully_real_config():
    """Settings must accept this without raising -- this is the config the
    "flip the switches" section of docs/aws-setup.md promises works."""
    settings = Settings(**_real_config())

    assert settings.app_env == "prod"
    assert settings.table_name == "quizdeck-prod"
    assert settings.s3_bucket == "quizdeck-media"
    # Optional: unset means images are proxied through the API rather than served
    # from a CDN, which needs no bucket policy at all.
    assert settings.image_public_base_url is None


def test_prod_accepts_a_public_image_base_url():
    settings = Settings(**_real_config(image_public_base_url="https://media.example.com"))

    assert settings.image_public_base_url == "https://media.example.com"


def test_prod_refuses_a_local_ses_profile():
    """A container has no ~/.aws -- credentials come from the task role."""
    with pytest.raises(ValueError, match="SES_PROFILE"):
        Settings(**_real_config(ses_profile="quizdeck"))


def test_prod_refuses_a_local_dynamo_profile():
    """Same reason as SES_PROFILE: no ~/.aws in a container."""
    with pytest.raises(ValueError, match="DYNAMO_PROFILE"):
        Settings(**_real_config(dynamo_profile="quizdeck"))


def test_prod_refuses_a_local_s3_profile():
    """Same reason as SES_PROFILE: no ~/.aws in a container."""
    with pytest.raises(ValueError, match="S3_PROFILE"):
        Settings(**_real_config(s3_profile="quizdeck"))


def test_ses_region_defaults_to_unset_so_aws_region_applies():
    """SesEmailSender falls back to aws_region; see tests/unit/test_ses_sender.py."""
    settings = Settings(
        email_mode="ses", ses_from_address="a@b.com", _env_file=None, **RESOURCES
    )

    assert settings.ses_region is None
