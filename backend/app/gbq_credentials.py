"""Load the BigQuery service account from the existing Secrets Manager secret (read-only).

Never log or print the secret value.
"""

import json
import os
from typing import Any

import boto3
from google.oauth2 import service_account

# Query jobs need the full `bigquery` OAuth scope (`bigquery.readonly` can't create jobs).
# Read-only behavior is enforced by sql_validation (SELECT-only + table allowlist), and by the
# service account's own IAM roles.
BQ_SCOPES = ["https://www.googleapis.com/auth/bigquery"]


def parse_sa_info(value: Any) -> dict[str, Any]:
    """The key's value may be a JSON string or an already-parsed object."""
    if isinstance(value, (str, bytes)):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("GBQ secret value is not a JSON object")
    return value


def load_sa_info(
    secret_name: str | None = None,
    secret_key: str | None = None,
    session: boto3.Session | None = None,
) -> dict[str, Any]:
    secret_name = secret_name or os.environ.get("RDB_GBQ_SECRET_NAME", "GBQ_lambda_secrets")
    secret_key = secret_key or os.environ.get("RDB_GBQ_SECRET_KEY", "GLOBAL_GBQ_KEY")
    client = (session or boto3.Session()).client("secretsmanager")
    raw = client.get_secret_value(SecretId=secret_name)["SecretString"]
    outer = json.loads(raw)
    if not isinstance(outer, dict) or secret_key not in outer:
        raise KeyError(f"key {secret_key!r} not found in secret {secret_name!r}")
    return parse_sa_info(outer[secret_key])


def load_credentials(
    secret_name: str | None = None,
    secret_key: str | None = None,
    session: boto3.Session | None = None,
) -> service_account.Credentials:
    info = load_sa_info(secret_name, secret_key, session)
    return service_account.Credentials.from_service_account_info(info, scopes=BQ_SCOPES)
