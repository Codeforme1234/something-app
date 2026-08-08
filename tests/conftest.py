"""Session-wide AWS sandbox for the whole suite.

Every environment now addresses real AWS -- there is no DynamoDB Local, no
MinIO, and no local-filesystem ObjectStore to fall back on. The safety that
used to come from "the dev config happens to point at localhost" has to come
from here instead, so this module makes it structurally impossible for a test
to reach an AWS account:

1. `QUIZDECK_ENV_FILE` is pointed at a path that does not exist, before
   `app.core.config` is imported. Without this, `get_settings()` would read
   `.env.dev` and hand every test the real dev table and bucket names.
2. Credentials in the environment are obviously-fake constants, so a request
   that somehow escaped moto would be rejected by AWS rather than accepted.
3. The three credential-profile settings are blanked, so nothing loads the
   real `quizdeck` profile out of ~/.aws.
4. `mock_aws()` wraps the entire session, intercepting boto3 in-process.

Steps 1-3 have to happen at import time, because pytest imports this module
before any test module, and `app.core.config` reads QUIZDECK_ENV_FILE once at
*its* import.
"""

import os

os.environ["QUIZDECK_ENV_FILE"] = ".env.does-not-exist-in-tests"

# Obviously fake, and not a valid key id shape, so a leaked request fails
# loudly at AWS rather than authenticating as somebody.
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "ap-south-1"
# botocore reads AWS_PROFILE natively; a developer shell that has one set would
# otherwise send moto looking for it.
os.environ.pop("AWS_PROFILE", None)
os.environ["DYNAMO_PROFILE"] = ""
os.environ["S3_PROFILE"] = ""
os.environ["SES_PROFILE"] = ""

# Resource names Settings now requires. Deliberately not the dev or prod names:
# if the sandbox ever failed open, the error should be "no such table", not a
# successful write to something real.
os.environ["TABLE_NAME"] = "quizdeck-test"
os.environ["S3_BUCKET"] = "quizdeck-media-test"
os.environ["AWS_REGION"] = "ap-south-1"

import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

TEST_TABLE_NAME = os.environ["TABLE_NAME"]
TEST_BUCKET = os.environ["S3_BUCKET"]
TEST_REGION = os.environ["AWS_REGION"]


@pytest.fixture(scope="session", autouse=True)
def _aws_sandbox():
    """Intercept every boto3 call in-process for the whole session."""
    with mock_aws():
        yield
