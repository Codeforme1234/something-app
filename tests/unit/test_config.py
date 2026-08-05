import pytest

from app.core.config import Settings


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
        )


def test_prod_refuses_local_dynamo_endpoint():
    with pytest.raises(ValueError, match="mock modes"):
        Settings(
            app_env="prod",
            auth_mode="cognito",
            cognito_user_pool_id="pool",
            cognito_client_id="client",
            cognito_region="us-east-1",
            email_mode="ses",
            ses_from_address="a@b.com",
            llm_mode="openai",
            openai_api_key="sk-test",
            dynamo_endpoint_url="http://localhost:8001",
            _env_file=None,
        )


def test_cognito_mode_requires_pool_settings():
    with pytest.raises(ValueError, match="COGNITO_USER_POOL_ID"):
        Settings(auth_mode="cognito", _env_file=None)


def test_dev_defaults_are_all_mocks():
    settings = Settings(_env_file=None)
    assert (settings.app_env, settings.auth_mode, settings.email_mode, settings.llm_mode) == (
        "dev",
        "fake",
        "outbox",
        "fake",
    )


def test_prod_accepts_a_fully_real_config():
    """The exact combination docs/aws-setup.md tells an operator to flip to:
    cognito + ses + openai, no local Dynamo endpoint. Settings must accept
    this without raising -- this is the config the "flip the switches"
    section of that doc promises works."""
    settings = Settings(
        app_env="prod",
        frontend_origin="https://app.example.com",
        auth_mode="cognito",
        cognito_user_pool_id="us-east-1_Pool123",
        cognito_client_id="client-abc",
        cognito_region="us-east-1",
        email_mode="ses",
        ses_from_address="noreply@quizdeck.example.com",
        llm_mode="openai",
        openai_api_key="sk-live-test",
        dynamo_endpoint_url=None,
        aws_access_key_id=None,
        aws_secret_access_key=None,
        _env_file=None,
    )
    assert settings.app_env == "prod"
    assert settings.dynamo_endpoint_url is None
