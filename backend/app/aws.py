"""boto3 handles (profile comes from AWS_PROFILE in env / .env)."""

from functools import lru_cache
from typing import Any

import boto3

from app.config import get_settings


@lru_cache
def session() -> boto3.Session:
    return boto3.Session(region_name=get_settings().region)


@lru_cache
def ddb_table() -> Any:
    return session().resource("dynamodb").Table(get_settings().table_name)


@lru_cache
def s3() -> Any:
    return session().client("s3")


@lru_cache
def ddb_client() -> Any:
    return session().client("dynamodb")


@lru_cache
def idp() -> Any:
    return session().client("cognito-idp")
