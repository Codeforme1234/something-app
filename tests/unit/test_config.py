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
