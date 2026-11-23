"""Create Secrets Manager secret `rdb_gemini` from the Gemini values in .env. Never prints values.

  AWS_PROFILE=concrete python scripts/create_gemini_secret.py [--force]

Refuses to overwrite an existing secret without --force. Tagged Project=RDB.
Keys: api_key, default_model, decision_model (from GEMINI_API_KEY, GEMINI_DEFAULT_MODEL,
GEMINI_DECISION_MODEL). The deployed API reads it via RDB_GEMINI_SECRET_NAME=rdb_gemini.
"""

import json
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config as _config  # noqa: E402,F401  (loads .env)

NAME = "rdb_gemini"
KEYS = "api_key, default_model, decision_model"


def main() -> None:
    force = "--force" in sys.argv[1:]
    env = os.environ
    api_key = (env.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        sys.exit("GEMINI_API_KEY missing in .env")
    body = json.dumps(
        {
            "api_key": api_key,
            "default_model": (env.get("GEMINI_DEFAULT_MODEL") or "gemini-3.8-flash").strip(),
            "decision_model": (env.get("GEMINI_DECISION_MODEL") or "gemini-decision-1").strip(),
        }
    )
    sm = boto3.Session(
        profile_name=env.get("AWS_PROFILE", "concrete"),
        region_name=env.get("AWS_REGION", "us-east-2"),
    ).client("secretsmanager")
    try:
        sm.describe_secret(SecretId=NAME)
        exists = True
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        exists = False
    if exists and not force:
        sys.exit(f"Secret {NAME} already exists; refusing to overwrite (use --force to update it).")
    if exists:
        sm.put_secret_value(SecretId=NAME, SecretString=body)
        sm.tag_resource(SecretId=NAME, Tags=[{"Key": "Project", "Value": "RDB"}])
        print(f"Updated secret {NAME} (keys: {KEYS})")
    else:
        sm.create_secret(
            Name=NAME,
            SecretString=body,
            Description="RDB Gemini API key and model names",
            Tags=[{"Key": "Project", "Value": "RDB"}],
        )
        print(f"Created secret {NAME} (keys: {KEYS})")


if __name__ == "__main__":
    main()
