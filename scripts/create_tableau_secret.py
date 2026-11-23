"""Create Secrets Manager secret `rdb_tableau` from the Tableau values in .env. Never prints values.

  AWS_PROFILE=concrete python scripts/create_tableau_secret.py [--force]

Refuses to overwrite an existing secret without --force. Tagged Project=RDB.
Keys: server_url, site, token_name, token_secret, token_expires (ISO YYYY-MM-DD).
"""

import json
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.tableau_config import _from_env  # noqa: E402  (also loads .env)

NAME = "rdb_tableau"


def main() -> None:
    force = "--force" in sys.argv[1:]
    cfg = _from_env()
    if cfg is None:
        sys.exit("Tableau values missing in .env (TABLEAU_SERVER_URL/SITE/TOKEN_NAME/API_TOKEN)")
    if not cfg.token_expires:
        sys.exit("TABLEAU_API_TOKEN_EXP missing in .env")
    body = json.dumps(
        {
            "server_url": cfg.server_url,
            "site": cfg.site,
            "token_name": cfg.token_name,
            "token_secret": cfg.token_secret.get_secret_value(),
            "token_expires": cfg.token_expires.isoformat(),
        }
    )
    sm = boto3.Session(
        profile_name=os.environ.get("AWS_PROFILE", "concrete"),
        region_name=os.environ.get("AWS_REGION", "us-east-2"),
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
        print(
            f"Updated secret {NAME} (keys: server_url, site, token_name, token_secret, token_expires)"
        )
    else:
        sm.create_secret(
            Name=NAME,
            SecretString=body,
            Description="RDB Tableau Cloud PAT (read-only inventory)",
            Tags=[{"Key": "Project", "Value": "RDB"}],
        )
        print(
            f"Created secret {NAME} (keys: server_url, site, token_name, token_secret, token_expires)"
        )


if __name__ == "__main__":
    main()
