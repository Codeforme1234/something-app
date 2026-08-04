"""Create the single DynamoDB table. Idempotent.

Only PK (hash) + SK (range) are defined — no GSIs, no other attributes.
Every business field lives inside the JSON `data` blob on each item.
"""

import sys

from botocore.exceptions import ClientError

sys.path.insert(0, ".")

from app.core.config import get_settings  # noqa: E402
from app.repositories.table import get_client  # noqa: E402


def main() -> None:
    settings = get_settings()
    client = get_client()
    try:
        client.create_table(
            TableName=settings.table_name,
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
        print(f"created table {settings.table_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("ResourceInUseException", "TableAlreadyExistsException"):
            print(f"table {settings.table_name} already exists")
        else:
            raise


if __name__ == "__main__":
    main()
