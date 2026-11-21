"""Create a client user: Cognito user (rdb-users pool ONLY) + `client` group + DynamoDB access item.

Cognito emails the temporary password; nothing secret is printed or stored.
Usage: AWS_PROFILE=concrete AWS_REGION=us-east-2 .venv/bin/python scripts/create_client_user.py EMAIL CLIENT_SLUG
Safe to rerun (existing user: just ensures the group and access item).
"""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
REGION = "us-east-2"
POOL_NAME = "rdb-users"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    email, slug = sys.argv[1].strip().lower(), sys.argv[2].strip()
    pool_id = os.environ["COGNITO_USER_POOL_ID"]
    table_name = os.environ.get("RDB_TABLE_NAME", "rdb_main")

    session = boto3.Session(profile_name="concrete", region_name=REGION)
    idp = session.client("cognito-idp")
    name = idp.describe_user_pool(UserPoolId=pool_id)["UserPool"]["Name"]
    if name != POOL_NAME:  # hard stop: never touch any other pool
        print(f"Refusing: pool {pool_id} is named {name!r}, expected {POOL_NAME!r}.")
        return 1

    table = session.resource("dynamodb").Table(table_name)
    if "Item" not in table.get_item(Key={"PK": f"CLIENT#{slug}", "SK": "META"}):
        print(f"Client {slug!r} does not exist. Create it in the Builder first.")
        return 1

    try:
        idp.admin_create_user(
            UserPoolId=pool_id,
            Username=email,
            DesiredDeliveryMediums=["EMAIL"],  # Cognito emails the temporary password
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
        )
        print(f"created {email}; Cognito emailed a temporary password")
    except ClientError as e:
        if e.response["Error"]["Code"] != "UsernameExistsException":
            raise
        print(f"{email} already exists; ensuring group + access")

    idp.admin_add_user_to_group(UserPoolId=pool_id, Username=email, GroupName="client")
    sub = next(
        a["Value"]
        for a in idp.admin_get_user(UserPoolId=pool_id, Username=email)["UserAttributes"]
        if a["Name"] == "sub"
    )
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    ser = TypeSerializer()  # access item + CLIENT#/USER# mirror, always together
    items = [
        {"PK": f"USER#{sub}", "SK": f"CLIENT#{slug}", "created_at": ts},
        {
            "PK": f"CLIENT#{slug}",
            "SK": f"USER#{sub}",
            "email": email,
            "added_by": "script",
            "added_at": ts,
        },
    ]
    session.client("dynamodb").transact_write_items(
        TransactItems=[
            {"Put": {"TableName": table_name, "Item": {k: ser.serialize(v) for k, v in i.items()}}}
            for i in items
        ]
    )
    print(f"{email} is in group 'client' with access to {slug!r}. Sign in at http://localhost:5174")
    return 0


if __name__ == "__main__":
    sys.exit(main())
