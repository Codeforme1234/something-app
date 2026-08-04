from functools import lru_cache

import boto3

from app.core.config import get_settings


def _boto_kwargs() -> dict:
    settings = get_settings()
    kwargs: dict = {"region_name": settings.aws_region}
    if settings.dynamo_endpoint_url:
        kwargs["endpoint_url"] = settings.dynamo_endpoint_url
    # Explicit credentials keep local dev from picking up an unrelated AWS
    # profile (e.g. an SSO login) off the machine's shared config.
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    return kwargs


@lru_cache
def get_dynamodb():
    return boto3.resource("dynamodb", **_boto_kwargs())


@lru_cache
def get_table():
    return get_dynamodb().Table(get_settings().table_name)


@lru_cache
def get_client():
    return boto3.client("dynamodb", **_boto_kwargs())
