from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "prod"] = "dev"
    frontend_origin: str = "http://localhost:3000"

    auth_mode: Literal["fake", "cognito"] = "fake"
    email_mode: Literal["outbox", "ses"] = "outbox"
    llm_mode: Literal["fake", "openai"] = "fake"

    table_name: str = "quizdeck-main"
    aws_region: str = "us-east-1"
    dynamo_endpoint_url: str | None = None
    # Set these for DynamoDB Local (it accepts anything). Leave unset against
    # real AWS so boto3 uses the normal credential chain.
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None

    cognito_user_pool_id: str | None = None
    cognito_client_id: str | None = None
    cognito_region: str | None = None

    ses_from_address: str | None = None

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    submit_grace_seconds: int = 30
    outbox_dir: str = ".dev/outbox"

    @model_validator(mode="after")
    def _no_fakes_in_prod(self) -> "Settings":
        if self.app_env == "prod":
            fakes = []
            if self.auth_mode == "fake":
                fakes.append("AUTH_MODE=fake")
            if self.email_mode == "outbox":
                fakes.append("EMAIL_MODE=outbox")
            if self.llm_mode == "fake":
                fakes.append("LLM_MODE=fake")
            if self.dynamo_endpoint_url:
                fakes.append("DYNAMO_ENDPOINT_URL (local Dynamo)")
            if fakes:
                raise ValueError(
                    f"APP_ENV=prod cannot run with mock modes: {', '.join(fakes)}"
                )
        if self.auth_mode == "cognito" and not (
            self.cognito_user_pool_id and self.cognito_client_id and self.cognito_region
        ):
            raise ValueError(
                "AUTH_MODE=cognito requires COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID and COGNITO_REGION"
            )
        if self.email_mode == "ses" and not self.ses_from_address:
            raise ValueError("EMAIL_MODE=ses requires SES_FROM_ADDRESS")
        if self.llm_mode == "openai" and not self.openai_api_key:
            raise ValueError("LLM_MODE=openai requires OPENAI_API_KEY")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
