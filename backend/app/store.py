"""DynamoDB + S3 persistence (docs/DATA_MODEL.md). Keep it thin."""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from app.aws import ddb_client, ddb_table, s3
from app.config import get_settings
from app.models import ProjectConfig, ViewSpec

KEEP_REVISIONS = 10
_SER = TypeSerializer()


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plain(item: dict[str, Any]) -> dict[str, Any]:
    """DynamoDB returns Decimal for numbers; convert integral ones to int."""
    return {k: (int(v) if isinstance(v, Decimal) and v == int(v) else v) for k, v in item.items()}


# ---------- S3 helpers ----------
def s3_get_json(key: str) -> Any | None:
    try:
        obj = s3().get_object(Bucket=get_settings().data_bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise
    return json.loads(obj["Body"].read())


def s3_put_json(key: str, data: Any) -> None:
    s3().put_object(
        Bucket=get_settings().data_bucket,
        Key=key,
        Body=json.dumps(data, separators=(",", ":")).encode(),
        ContentType="application/json",
    )


def project_prefix(c: str, p: str) -> str:
    return f"clients/{c}/projects/{p}/"


def view_prefix(c: str, p: str, v: str) -> str:
    return f"{project_prefix(c, p)}views/{v}/"


# ---------- clients ----------
def create_client(slug: str, name: str, created_by: str) -> dict[str, Any]:
    item = {
        "PK": f"CLIENT#{slug}",
        "SK": "META",
        "slug": slug,
        "name": name,
        "created_at": now(),
        "created_by": created_by,
        "lifecycle": "active",
    }
    _put_new(item)
    return item


def _put_new(item: dict[str, Any]) -> None:
    try:
        ddb_table().put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise Conflict("slug already exists") from None
        raise


def get_client(slug: str) -> dict[str, Any] | None:
    return ddb_table().get_item(Key={"PK": f"CLIENT#{slug}", "SK": "META"}).get("Item")


def list_clients() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "FilterExpression": "SK = :m",
        "ExpressionAttributeValues": {":m": "META"},
    }
    while True:  # prototype scale: a scan is fine (docs/DATA_MODEL.md)
        r = ddb_table().scan(**kwargs)
        items += [i for i in r["Items"] if i["PK"].startswith("CLIENT#")]
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return sorted(items, key=lambda i: i["slug"])


# ---------- projects ----------
def create_project(c: str, slug: str, name: str) -> dict[str, Any]:
    item = {
        "PK": f"CLIENT#{c}",
        "SK": f"PROJECT#{slug}",
        "client_slug": c,
        "slug": slug,
        "name": name,
        "status": "active",
        "lifecycle": "active",
        "created_at": now(),
    }
    _put_new(item)
    s3_put_json(project_prefix(c, slug) + "project.json", ProjectConfig().model_dump())
    return item


def get_project(c: str, p: str) -> dict[str, Any] | None:
    return ddb_table().get_item(Key={"PK": f"CLIENT#{c}", "SK": f"PROJECT#{p}"}).get("Item")


def list_projects(c: str) -> list[dict[str, Any]]:
    r = ddb_table().query(
        KeyConditionExpression=Key("PK").eq(f"CLIENT#{c}") & Key("SK").begins_with("PROJECT#")
    )
    return r["Items"]


def update_project_name(c: str, p: str, name: str) -> dict[str, Any]:
    try:
        r = ddb_table().update_item(
            Key={"PK": f"CLIENT#{c}", "SK": f"PROJECT#{p}"},
            UpdateExpression="SET #n = :n",
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeNames={"#n": "name"},
            ExpressionAttributeValues={":n": name},
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise NotFound("project") from None
        raise
    return r["Attributes"]


def get_project_config(c: str, p: str) -> ProjectConfig:
    data = s3_get_json(project_prefix(c, p) + "project.json")
    return ProjectConfig(**data) if data else ProjectConfig()


def put_project_config(c: str, p: str, cfg: ProjectConfig) -> None:
    s3_put_json(project_prefix(c, p) + "project.json", cfg.model_dump())


# ---------- views ----------
def create_view(
    c: str, p: str, slug: str, name: str, description: str | None = None
) -> dict[str, Any]:
    item = {
        "PK": f"CLIENT#{c}",
        "SK": f"VIEW#{p}#{slug}",
        "client_slug": c,
        "project_slug": p,
        "slug": slug,
        "name": name,
        "rev_counter": 0,
        "lifecycle": "active",
        "updated_at": now(),
    }
    if description:
        item["description"] = description
    _put_new(item)
    return item


def update_view_meta(
    c: str, p: str, v: str, name: str | None, description: str | None
) -> dict[str, Any]:
    """Name/description only; takes effect immediately. Empty description removes it."""
    sets, removes, vals = ["updated_at = :t"], [], {":t": now()}
    if name:
        sets.append("#n = :n")
        vals[":n"] = name
    if description is not None:
        if description:
            sets.append("description = :d")
            vals[":d"] = description
        else:
            removes.append("description")
    expr = "SET " + ", ".join(sets) + (" REMOVE " + ", ".join(removes) if removes else "")
    try:
        extra: dict[str, Any] = {"ExpressionAttributeNames": {"#n": "name"}} if name else {}
        r = ddb_table().update_item(
            Key=_view_key(c, p, v),
            UpdateExpression=expr,
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues=vals,
            ReturnValues="ALL_NEW",
            **extra,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise NotFound("view") from None
        raise
    return _plain(r["Attributes"])


def get_view(c: str, p: str, v: str) -> dict[str, Any] | None:
    item = ddb_table().get_item(Key={"PK": f"CLIENT#{c}", "SK": f"VIEW#{p}#{v}"}).get("Item")
    return _plain(item) if item else None


def list_views(c: str, p: str) -> list[dict[str, Any]]:
    r = ddb_table().query(
        KeyConditionExpression=Key("PK").eq(f"CLIENT#{c}") & Key("SK").begins_with(f"VIEW#{p}#")
    )
    return [_plain(i) for i in r["Items"]]


def view_public(item: dict[str, Any]) -> dict[str, Any]:
    d, pub = item.get("draft_revision"), item.get("published_revision")
    return {
        **item,
        "draft_revision": d,
        "published_revision": pub,
        "has_unpublished_changes": d is not None and d != pub,
    }


def _view_key(c: str, p: str, v: str) -> dict[str, str]:
    return {"PK": f"CLIENT#{c}", "SK": f"VIEW#{p}#{v}"}


def _next_revision(c: str, p: str, v: str) -> int:
    """Atomic ADD counter; no read-then-write race."""
    try:
        r = ddb_table().update_item(
            Key=_view_key(c, p, v),
            UpdateExpression="ADD rev_counter :one",
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues={":one": 1},
            ReturnValues="UPDATED_NEW",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise NotFound("view") from None
        raise
    return int(r["Attributes"]["rev_counter"])


def rev_key(c: str, p: str, v: str, n: int) -> str:
    return f"{view_prefix(c, p, v)}revisions/{n:06d}.json"


def write_revision(c: str, p: str, v: str, spec: ViewSpec, user: str) -> int:
    """Allocate a revision number, write the immutable revision file, move the draft pointer."""
    n = _next_revision(c, p, v)
    spec = spec.model_copy(update={"revision": n, "updated_at": now(), "updated_by": user})
    spec.view.slug = v
    s3_put_json(rev_key(c, p, v, n), spec.model_dump(mode="json"))
    s3_put_json(view_prefix(c, p, v) + "draft.json", {"revision": n})
    try:  # only move forward if two saves race
        ddb_table().update_item(
            Key=_view_key(c, p, v),
            UpdateExpression="SET draft_revision = :n, updated_at = :t",
            ConditionExpression="attribute_not_exists(draft_revision) OR draft_revision < :n",
            ExpressionAttributeValues={":n": n, ":t": now()},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
    prune_revisions(c, p, v)
    return n


def read_revision(c: str, p: str, v: str, n: int) -> dict[str, Any] | None:
    return s3_get_json(rev_key(c, p, v, n))


def list_revision_numbers(c: str, p: str, v: str) -> list[int]:
    prefix = f"{view_prefix(c, p, v)}revisions/"
    nums: list[int] = []
    pager = s3().get_paginator("list_objects_v2")
    for page in pager.paginate(Bucket=get_settings().data_bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            m = re.fullmatch(r"(\d{6})\.json", o["Key"][len(prefix) :])
            if m:
                nums.append(int(m.group(1)))
    return sorted(nums, reverse=True)


def prune_revisions(c: str, p: str, v: str) -> list[int]:
    """Keep newest KEEP_REVISIONS; never delete the published revision. Own prefix only."""
    nums = list_revision_numbers(c, p, v)
    item = get_view(c, p, v) or {}
    protect = set(nums[:KEEP_REVISIONS])
    if item.get("published_revision") is not None:
        protect.add(int(item["published_revision"]))
    doomed = [n for n in nums if n not in protect]
    if doomed:
        s3().delete_objects(
            Bucket=get_settings().data_bucket,
            Delete={"Objects": [{"Key": rev_key(c, p, v, n)} for n in doomed], "Quiet": True},
        )
    return doomed


def summarize_spec(spec: dict[str, Any], published_at: str) -> dict[str, Any]:
    """What a client-facing card shows about a published view. No SQL, no revision numbers."""
    counts = {"chart": 0, "table": 0, "kpi": 0, "text": 0}
    kinds: list[str] = []
    for e in spec.get("elements", []):
        counts[e["type"]] = counts.get(e["type"], 0) + 1
        k = (e.get("chart") or {}).get("kind")
        if e["type"] == "chart" and k and k not in kinds:
            kinds.append(k)
    return {"published_at": published_at, "counts": counts, "chart_kinds": kinds}


def publish(c: str, p: str, v: str, user: str, spec: dict[str, Any] | None = None) -> int:
    item = get_view(c, p, v)
    if not item:
        raise NotFound("view")
    n = item.get("draft_revision")
    if n is None:
        raise NotFound("draft")
    ts = now()
    spec = spec or read_revision(c, p, v, int(n)) or {}
    s3_put_json(
        view_prefix(c, p, v) + "published.json",
        {"revision": n, "published_at": ts, "published_by": user},
    )
    ddb_table().update_item(
        Key=_view_key(c, p, v),
        UpdateExpression="SET published_revision = :n, updated_at = :t, published_summary = :s",
        ExpressionAttributeValues={":n": n, ":t": ts, ":s": summarize_spec(spec, ts)},
    )
    return int(n)


def published_summary(item: dict[str, Any]) -> dict[str, Any] | None:
    """The stored summary; views published before summaries existed are backfilled once."""
    if item.get("published_revision") is None:
        return None
    if item.get("published_summary"):
        return item["published_summary"]
    c, p, v = item["client_slug"], item["project_slug"], item["slug"]
    ptr = s3_get_json(f"{view_prefix(c, p, v)}published.json")
    spec = read_revision(c, p, v, int(ptr["revision"])) if ptr else None
    if not ptr or not spec:
        return None
    summary = summarize_spec(spec, ptr.get("published_at", item.get("updated_at", "")))
    ddb_table().update_item(
        Key=_view_key(c, p, v),
        UpdateExpression="SET published_summary = :s",
        ExpressionAttributeValues={":s": summary},
    )
    return summary


# ---------- access ----------
def has_access(sub: str, client_slug: str) -> bool:
    r = ddb_table().get_item(Key={"PK": f"USER#{sub}", "SK": f"CLIENT#{client_slug}"})
    return "Item" in r


def list_access(sub: str) -> list[str]:
    """Client slugs this user is assigned to."""
    r = ddb_table().query(
        KeyConditionExpression=Key("PK").eq(f"USER#{sub}") & Key("SK").begins_with("CLIENT#")
    )
    return [i["SK"].split("#", 1)[1] for i in r["Items"]]


def _typed(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _SER.serialize(v) for k, v in item.items()}


def _tx(
    puts: list[dict[str, Any]] | None = None, deletes: list[dict[str, str]] | None = None
) -> None:
    """All-or-nothing write of access items (one transaction)."""
    table = get_settings().table_name
    ops: list[dict[str, Any]] = [
        {"Put": {"TableName": table, "Item": _typed(i)}} for i in puts or []
    ]
    ops += [{"Delete": {"TableName": table, "Key": _typed(k)}} for k in deletes or []]
    ddb_client().transact_write_items(TransactItems=ops)


def grant_access(sub: str, client_slug: str, email: str, added_by: str) -> bool:
    """Write the access item AND its mirror together. False if the user already had access."""
    if has_access(sub, client_slug):
        return False
    ts = now()
    _tx(
        puts=[
            {"PK": f"USER#{sub}", "SK": f"CLIENT#{client_slug}", "created_at": ts},
            {
                "PK": f"CLIENT#{client_slug}",
                "SK": f"USER#{sub}",
                "email": email,
                "added_by": added_by,
                "added_at": ts,
            },
        ]
    )
    return True


def revoke_access(sub: str, client_slug: str) -> None:
    """Remove the access item AND its mirror together (deleting absent items is fine)."""
    _tx(
        deletes=[
            {"PK": f"USER#{sub}", "SK": f"CLIENT#{client_slug}"},
            {"PK": f"CLIENT#{client_slug}", "SK": f"USER#{sub}"},
        ]
    )


def list_client_users(client_slug: str) -> list[dict[str, Any]]:
    """Mirror items for one client: {sub, email, added_by, added_at}."""
    r = ddb_table().query(
        KeyConditionExpression=Key("PK").eq(f"CLIENT#{client_slug}")
        & Key("SK").begins_with("USER#")
    )
    return [
        {
            "sub": i["SK"].split("#", 1)[1],
            "email": i.get("email", ""),
            "added_by": i.get("added_by", ""),
            "added_at": i.get("added_at", ""),
        }
        for i in r["Items"]
    ]
