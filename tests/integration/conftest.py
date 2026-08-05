"""Integration tests need `docker compose up -d` running (DynamoDB Local on
:8001 — see CLAUDE.md / docker-compose.yml).

These tests use their own table and drop it afterwards, so a test run never
reads or pollutes the data you are looking at in the dev app.
"""

import os

import pytest
from botocore.exceptions import ClientError

TEST_TABLE_NAME = "quizdeck-integration-tests"


@pytest.fixture(scope="session", autouse=True)
def _integration_table():
    os.environ["TABLE_NAME"] = TEST_TABLE_NAME

    # Settings and the boto3 handles are cached, so they must be rebuilt after
    # pointing TABLE_NAME at the throwaway table.
    from app.core.config import get_settings
    from app.repositories import table as table_module

    for cached in (
        get_settings,
        table_module.get_dynamodb,
        table_module.get_table,
        table_module.get_client,
    ):
        cached.cache_clear()

    client = table_module.get_client()
    _delete_table_if_exists(client)
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

    yield

    _delete_table_if_exists(client)


def _delete_table_if_exists(client) -> None:
    try:
        client.delete_table(TableName=TEST_TABLE_NAME)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
