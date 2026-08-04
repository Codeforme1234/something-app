"""Generic JSON-blob store over the single DynamoDB table.

Every item looks the same to DynamoDB:
    {PK, SK, entityType, version, data: "<json>"}

`data` is the JSON dump of a Pydantic model, so the only schema lives in
Python. Business fields are therefore invisible to DynamoDB, which is why
concurrency control uses the top-level `version` attribute rather than
conditions on business fields.
"""

from typing import TypeVar

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from pydantic import BaseModel

from app.core.exceptions import ConflictError
from app.repositories.table import get_table

M = TypeVar("M", bound=BaseModel)


class Stored[T: BaseModel]:
    """A model plus the version it was read at (needed for optimistic writes)."""

    def __init__(self, model: T, version: int):
        self.model = model
        self.version = version


def _encode(pk: str, sk: str, entity_type: str, model: BaseModel, version: int) -> dict:
    return {
        "PK": pk,
        "SK": sk,
        "entityType": entity_type,
        "version": version,
        "data": model.model_dump_json(),
    }


def put_new(pk: str, sk: str, entity_type: str, model: BaseModel) -> None:
    """Create an item that must not already exist."""
    try:
        get_table().put_item(
            Item=_encode(pk, sk, entity_type, model, 1),
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ConflictError("item already exists") from e
        raise


def put_overwrite(pk: str, sk: str, entity_type: str, model: BaseModel) -> None:
    """Create or replace unconditionally (idempotent writes, e.g. profile upsert)."""
    get_table().put_item(Item=_encode(pk, sk, entity_type, model, 1))


def put_versioned(
    pk: str, sk: str, entity_type: str, model: BaseModel, expected_version: int
) -> int:
    """Replace an item only if its stored version matches. Returns the new version."""
    new_version = expected_version + 1
    try:
        get_table().put_item(
            Item=_encode(pk, sk, entity_type, model, new_version),
            ConditionExpression="version = :v",
            ExpressionAttributeValues={":v": expected_version},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ConflictError("item was modified concurrently") from e
        raise
    return new_version


def get(pk: str, sk: str, model_cls: type[M]) -> Stored[M] | None:
    resp = get_table().get_item(Key={"PK": pk, "SK": sk})
    item = resp.get("Item")
    if not item:
        return None
    return Stored(model_cls.model_validate_json(item["data"]), int(item["version"]))


def query_prefix(
    pk: str, sk_prefix: str, model_cls: type[M], *, descending: bool = False
) -> list[Stored[M]]:
    out: list[Stored[M]] = []
    kwargs = {
        "KeyConditionExpression": Key("PK").eq(pk) & Key("SK").begins_with(sk_prefix),
        "ScanIndexForward": not descending,
    }
    while True:
        resp = get_table().query(**kwargs)
        out.extend(
            Stored(model_cls.model_validate_json(i["data"]), int(i["version"]))
            for i in resp.get("Items", [])
        )
        if "LastEvaluatedKey" not in resp:
            return out
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def delete(pk: str, sk: str) -> None:
    get_table().delete_item(Key={"PK": pk, "SK": sk})


def batch_write(items: list[dict], delete_keys: list[dict] | None = None) -> None:
    """Bulk put encoded items and/or delete keys (batching handled by boto3)."""
    table = get_table()
    with table.batch_writer() as batch:
        for key in delete_keys or []:
            batch.delete_item(Key=key)
        for item in items:
            batch.put_item(Item=item)


def encode_item(pk: str, sk: str, entity_type: str, model: BaseModel) -> dict:
    """Encode for batch_write callers."""
    return _encode(pk, sk, entity_type, model, 1)
