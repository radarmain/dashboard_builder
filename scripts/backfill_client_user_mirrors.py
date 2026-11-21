"""Backfill CLIENT#{slug} / USER#{sub} mirror items from existing USER#{sub} / CLIENT#{slug} access items.

Idempotent (rerun any time), never deletes anything. Emails come from Cognito (rdb-users pool only).
Usage: AWS_PROFILE=concrete AWS_REGION=us-east-2 .venv/bin/python scripts/backfill_client_user_mirrors.py [--dry-run]
"""

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
ap = argparse.ArgumentParser()
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()

pool = os.environ["COGNITO_USER_POOL_ID"]
session = boto3.Session(profile_name="concrete", region_name="us-east-2")
idp = session.client("cognito-idp")
if idp.describe_user_pool(UserPoolId=pool)["UserPool"]["Name"] != "rdb-users":
    raise SystemExit("Refusing: pool is not rdb-users")
table = session.resource("dynamodb").Table(os.environ.get("RDB_TABLE_NAME", "rdb_main"))

items: list[dict] = []
kw = {"FilterExpression": Attr("PK").begins_with("USER#") & Attr("SK").begins_with("CLIENT#")}
while True:
    r = table.scan(**kw)
    items += r["Items"]
    if "LastEvaluatedKey" not in r:
        break
    kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]

emails: dict[str, str | None] = {}


def email_of(sub: str) -> str | None:
    if sub not in emails:
        users = idp.list_users(UserPoolId=pool, Filter=f'sub = "{sub}"', Limit=1)["Users"]
        attrs = {a["Name"]: a["Value"] for a in users[0]["Attributes"]} if users else {}
        emails[sub] = attrs.get("email", "").lower() or None
    return emails[sub]


created = existed = skipped = 0
for it in items:
    sub, slug = it["PK"].split("#", 1)[1], it["SK"].split("#", 1)[1]
    email = email_of(sub)
    if not email:
        skipped += 1
        print(f"skip: no Cognito user for sub {sub[:8]}... (client {slug})")
        continue
    mirror = {
        "PK": f"CLIENT#{slug}",
        "SK": f"USER#{sub}",
        "email": email,
        "added_by": "backfill",
        "added_at": it.get("created_at") or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if args.dry_run:
        continue
    try:  # never overwrite an existing mirror
        table.put_item(Item=mirror, ConditionExpression="attribute_not_exists(PK)")
        created += 1
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        existed += 1

print(
    f"access items: {len(items)}; mirrors created: {created}; already present: {existed}; "
    f"skipped: {skipped}" + (" (dry run, nothing written)" if args.dry_run else "")
)
