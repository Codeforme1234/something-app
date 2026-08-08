import os
from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Which dotenv file backs Settings. There is one per environment -- `.env.dev`
#: and `.env.prod` -- rather than a single `.env` that gets edited back and
#: forth, because "which environment am I pointed at" should be visible in the
#: command you ran, not in the current contents of an untracked file. Both name
#: real AWS resources, so guessing wrong is a write to the wrong account.
ENV_FILE = os.getenv("QUIZDECK_ENV_FILE", ".env.dev")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    app_env: Literal["dev", "prod"] = "dev"
    frontend_origin: str = "http://localhost:3000"

    auth_mode: Literal["fake", "cognito"] = "fake"
    email_mode: Literal["outbox", "ses"] = "outbox"
    llm_mode: Literal["fake", "openai"] = "fake"

    # No defaults on the two resource names. Every environment addresses real
    # AWS now, so a default here would be a table or bucket that some
    # environment silently reads and writes because its env file was incomplete
    # -- the failure mode should be a startup error, not a wrong account.
    table_name: str
    aws_region: str = "ap-south-1"
    # Local dev only, and the same trade-off as ses_profile:
    # naming a profile keeps the long-lived secret in ~/.aws/credentials instead
    # of a second copy in .env, and stops a read or write landing in whatever
    # account this machine's default profile happens to point at. Leave unset in
    # a deployment, where the task or instance role supplies credentials.
    # Deliberately NOT called AWS_PROFILE: botocore reads that name natively, so
    # a test or script trying to blank it out hits ProfileNotFound("") inside
    # boto3 before this setting is ever consulted.
    dynamo_profile: str | None = None

    cognito_user_pool_id: str | None = None
    cognito_client_id: str | None = None
    cognito_region: str | None = None

    ses_from_address: str | None = None
    # SES gets its own region for the same reason Cognito does: the verified
    # identity does not have to live wherever the table does. Unset -> aws_region.
    ses_region: str | None = None
    # Local dev only. The verified identity lives in its own AWS account, which
    # is usually not the account this machine's default profile points at --
    # naming the profile keeps a real send from going out (or failing) through
    # an unrelated account, the same reasoning as the explicit credentials in
    # app/repositories/table.py. Leave unset in a deployment, where the task or
    # instance role supplies credentials through the normal chain.
    ses_profile: str | None = None

    openai_api_key: str | None = None
    # No default model, for either call. A default is a model name compiled into
    # the image: switching model then needs a deploy, and -- worse -- an env file
    # that forgets one silently bills a *different* model than the one it names.
    # Both are required when LLM_MODE=openai and ignored otherwise, so fake mode
    # does not have to name a model it will never call.
    openai_model: str | None = None
    # Used for both the structured extraction call and the vision pass that
    # transcribes diagram pages.
    openai_extraction_model: str | None = None
    # Applied to every OpenAI request. The extraction call overrides the timeout
    # with the much longer one below.
    openai_timeout_seconds: float = 60.0
    # The SDK's own retry, distinct from the single repair retry the generator
    # and extractor do when the model answers in an unusable shape.
    openai_max_retries: int = 1
    # Its own timeout, well above openai_timeout_seconds: one call extracts a
    # whole paper, which is minutes of output tokens. Raising the shared default
    # instead would slacken the deadline on cheap calls too.
    openai_extraction_timeout_seconds: int = 600

    submit_grace_seconds: int = 30
    outbox_dir: str = ".dev/outbox"

    # Where uploaded question images and knowledge-base documents live. There is
    # no local-filesystem alternative: it could not catch a bucket, credential or
    # content-type mistake, and a container filesystem is ephemeral, so every
    # image would vanish on the next deploy.
    s3_bucket: str
    # Local dev against real S3, exactly as dynamo_profile/ses_profile. Without
    # it the S3 client falls back to the default chain, which on a developer
    # machine is usually a different account -- and unlike a wrong table name
    # that fails loudly, a wrong bucket usually fails as AccessDenied much later.
    # Refused in prod.
    s3_profile: str | None = None
    # OPTIONAL, and the switch that decides how images reach a student's browser:
    #   unset -> images are proxied through GET /api/v1/images/{key}. The bucket
    #            stays fully private with no bucket policy at all. Simplest and
    #            secure by default, but every image byte crosses the API.
    #   set    -> images are served directly from this origin (a CloudFront
    #            distribution, or the bucket's own public URL). Needs the bucket
    #            or distribution to allow public GET on the tests/ prefix, and
    #            keeps student traffic off the API entirely.
    # Either way the stored value is the KEY, never a URL, so switching is a
    # config change with no data migration.
    image_public_base_url: str | None = None
    # How the browser reaches this API -- used to build proxied image URLs.
    api_public_origin: str = "http://localhost:8000"
    max_image_bytes: int = 2_000_000
    # Ceiling for the upload routes only; every other route keeps
    # app.main.MAX_BODY_BYTES.
    max_upload_bytes: int = 20_000_000

    # Inbox that receives support requests submitted from the app.
    support_email: str = "support@quizdeck.local"

    # Credits a brand-new company starts with. One test creation spends one.
    starting_credits: int = 20

    # AI generation is metered separately from test creation, because a run
    # costs real money. Both are debited: creating any test still spends one
    # `starting_credits` credit (CLAUDE.md rule 8), and an AI run additionally
    # spends from this pool.
    starting_ai_credits: int = 20
    # Settings rather than constants so pricing can change without a deploy.
    ai_credit_cost_prompt: int = 1
    ai_credit_cost_pdf: int = 2

    max_pdf_bytes: int = 20_000_000
    # Bounds worst-case parse and render cost. A JEE paper is ~25 pages.
    max_pdf_pages: int = 60

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
            if self.ses_profile:
                # A container has no ~/.aws: credentials come from the task role.
                fakes.append("SES_PROFILE (local credential profile)")
            if self.dynamo_profile:
                # Same reason as SES_PROFILE.
                fakes.append("DYNAMO_PROFILE (local credential profile)")
            if self.s3_profile:
                # Same reason as SES_PROFILE.
                fakes.append("S3_PROFILE (local credential profile)")
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
        if self.llm_mode == "openai":
            missing = [
                name
                for name, value in (
                    ("OPENAI_API_KEY", self.openai_api_key),
                    # Named explicitly rather than defaulted, so the model being
                    # billed is always the one written in the env file.
                    ("OPENAI_MODEL", self.openai_model),
                    ("OPENAI_EXTRACTION_MODEL", self.openai_extraction_model),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"LLM_MODE=openai requires {', '.join(missing)}")
        # A dev env file pointed at the prod table or bucket is the one mistake
        # this whole two-file split exists to prevent, and it is silent: writes
        # succeed, against the wrong data. Catch the obvious spelling of it.
        if self.app_env == "dev":
            prod_resources = [
                name
                for name, value in (("TABLE_NAME", self.table_name), ("S3_BUCKET", self.s3_bucket))
                if value.endswith("-prod") or value == "quizdeck-media"
            ]
            if prod_resources:
                raise ValueError(
                    f"APP_ENV=dev must not point at production resources: {', '.join(prod_resources)}"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
