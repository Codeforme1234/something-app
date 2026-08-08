"""boto3 handles for the single DynamoDB table.

Always real AWS. There is no local-endpoint branch: every environment has its
own table (`quizdeck-dev`, `quizdeck-prod`), and the test suite intercepts
boto3 in-process with moto rather than pointing it at an emulator, so an
`endpoint_url` switch would only be a way to aim production at the wrong place.
"""

from functools import lru_cache

import boto3

from app.core.config import get_settings


def _session() -> boto3.Session:
    # profile_name=None is boto3's normal credential chain -- what a deployment
    # uses, where the task or instance role supplies credentials. Naming a
    # profile is for local dev, where this machine's default profile points at a
    # different account (same reasoning as SES_PROFILE). `or None` so an
    # explicitly blanked DYNAMO_PROFILE means "the normal chain", not a lookup
    # for a profile literally named "".
    return boto3.Session(profile_name=get_settings().dynamo_profile or None)


@lru_cache
def get_dynamodb():
    return _session().resource("dynamodb", region_name=get_settings().aws_region)


@lru_cache
def get_table():
    return get_dynamodb().Table(get_settings().table_name)


@lru_cache
def get_client():
    return _session().client("dynamodb", region_name=get_settings().aws_region)
