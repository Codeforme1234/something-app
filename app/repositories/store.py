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
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from pydantic import BaseModel

from app.core.exceptions import ConflictError
from app.repositories.table import get_client, get_table

M = TypeVar("M", bound=BaseModel)
_serializer = TypeSerializer()


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


def transact_put_new_and_update(
    *,
    new_pk: str,
    new_sk: str,
    new_entity_type: str,
    new_model: BaseModel,
    update_pk: str,
    update_sk: str,
    update_entity_type: str,
    update_model: BaseModel,
    update_expected_version: int,
) -> int:
    """Atomically create a brand-new item and update an existing versioned
    item in a single all-or-nothing transaction (e.g. a submission plus the
    session it completes). Both writes land or neither does.

    The resource-level Table object has no transaction method, so this goes
    through the low-level client (`app.repositories.table.get_client`) with
    items hand-serialized to DynamoDB's AttributeValue wire format. Returns
    the new version of the updated item. Raises ConflictError if either
    condition fails.
    """
    new_version = update_expected_version + 1
    table_name = get_table().name
    try:
        get_client().transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": table_name,
                        "Item": _to_low_level(_encode(new_pk, new_sk, new_entity_type, new_model, 1)),
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                {
                    "Put": {
                        "TableName": table_name,
                        "Item": _to_low_level(
                            _encode(update_pk, update_sk, update_entity_type, update_model, new_version)
                        ),
                        "ConditionExpression": "version = :v",
                        "ExpressionAttributeValues": {":v": _serializer.serialize(update_expected_version)},
                    }
                },
            ]
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "TransactionCanceledException":
            raise ConflictError("already submitted") from e
        raise
    return new_version


def _to_low_level(item: dict) -> dict:
    return {k: _serializer.serialize(v) for k, v in item.items()}


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
