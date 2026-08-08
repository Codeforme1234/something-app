"""Integration tests run entirely against moto (see tests/conftest.py), so they
need no docker, no AWS account, and no network.

The table and the bucket are created inside the mock and torn down afterwards.
Modes with a real backend are still pinned to their fake here rather than
inherited: tests/conftest.py already guarantees no env file is loaded, but
pinning keeps the intent local and survives someone giving the suite an env
file later. EMAIL_MODE in particular must never be `ses` -- a test run must not
mail a real student.
"""

import os

import pytest

from tests.conftest import TEST_BUCKET, TEST_REGION, TEST_TABLE_NAME


@pytest.fixture(scope="session", autouse=True)
def _aws_resources(_aws_sandbox):
    os.environ["EMAIL_MODE"] = "outbox"
    os.environ["AUTH_MODE"] = "fake"
    os.environ["LLM_MODE"] = "fake"

    # Settings and the boto3/sender/store handles are cached, so they must be
    # rebuilt now that the environment above is in place.
    from app.core.config import get_settings
    from app.repositories import table as table_module
    from app.services.email import get_email_sender
    from app.services.storage import get_object_store

    caches = (
        get_settings,
        get_email_sender,
        get_object_store,
        table_module.get_dynamodb,
        table_module.get_table,
        table_module.get_client,
    )
    for cached in caches:
        cached.cache_clear()

    client = table_module.get_client()
    client.create_table(
        TableName=TEST_TABLE_NAME,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    import boto3

    boto3.client("s3", region_name=TEST_REGION).create_bucket(
        Bucket=TEST_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": TEST_REGION},
    )

    yield

    client.delete_table(TableName=TEST_TABLE_NAME)
    for cached in caches:
        cached.cache_clear()


@pytest.fixture(autouse=True)
def _empty_table(_aws_resources):
    """Each test starts from an empty table.

    The suite used to get this from a fresh table per session plus tests that
    minted their own ULIDs; keeping it per-test is cheap under moto and stops
    one test's leftover rows from deciding another's `list` ordering.
    """
    yield
    from app.repositories.table import get_table

    table = get_table()
    scan = table.scan(ProjectionExpression="PK,SK")
    with table.batch_writer() as batch:
        for item in scan.get("Items", []):
            batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
