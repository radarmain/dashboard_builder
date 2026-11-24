"""Archive / restore / permanent delete of clients, projects and views (docs/DATA_MODEL.md).

States: "active" (default, also when the field is missing) | "archived" | "deleting".
Effective state: archived if the item or any ancestor is archived; deleting beats archived.
`deleting` is an internal marker that hides the item everywhere while a delete is in progress.

Permanent delete (idempotent, resumable; a retry of the same DELETE call finishes the job):
  1. mark the item lifecycle="deleting"
  2. delete S3 objects under ITS prefix only (bucket is versioned: this leaves delete markers)
  3. delete the DynamoDB items in batches of 25, the item's own record LAST
Cognito is never touched: a client delete only removes users' access items for that client.

All AWS access goes through the small `_s3_*` / `_ddb_*` helpers so tests can replace them.
"""

import re
from typing import Any

from boto3.dynamodb.conditions import Key

from app import store
from app.aws import ddb_table, s3
from app.config import get_settings
from app.users import audit

ACTIVE, ARCHIVED, DELETING = "active", "archived", "deleting"
BATCH = 25  # DynamoDB BatchWriteItem limit
_PREFIX_OK = re.compile(r"^clients/[a-z0-9-]+/(projects/[a-z0-9-]+/(views/[a-z0-9-]+/)?)?$")
_REV = re.compile(r"/views/[^/]+/revisions/\d{6}\.json$")


class NotArchived(Exception):
    """Permanent delete is only allowed on archived (or already-deleting) items."""


class Missing(Exception):
    pass


# ---------------------------------------------------------------- pure rules
def state(item: dict[str, Any] | None) -> str:
    v = (item or {}).get("lifecycle") or ACTIVE
    return v if v in (ARCHIVED, DELETING) else ACTIVE


def combine(parent: str, own: str) -> str:
    """Effective state of a child given its parent's effective state."""
    if DELETING in (parent, own):
        return DELETING
    return ARCHIVED if ARCHIVED in (parent, own) else ACTIVE


def effective(*chain: dict[str, Any] | None) -> str:
    """Effective state for an ancestor chain, outermost first (client, project, view)."""
    out = ACTIVE
    for item in chain:
        out = combine(out, state(item))
    return out


# ---------------------------------------------------------------- keys / prefixes
def item_key(c: str, p: str | None = None, v: str | None = None) -> dict[str, str]:
    if v:
        return {"PK": f"CLIENT#{c}", "SK": f"VIEW#{p}#{v}"}
    if p:
        return {"PK": f"CLIENT#{c}", "SK": f"PROJECT#{p}"}
    return {"PK": f"CLIENT#{c}", "SK": "META"}


def prefix_of(c: str, p: str | None = None, v: str | None = None) -> str:
    if v and p:
        return store.view_prefix(c, p, v)
    if p:
        return store.project_prefix(c, p)
    return f"clients/{c}/"


def path_of(c: str, p: str | None = None, v: str | None = None) -> str:
    return "/".join(x for x in (c, p, v) if x)


# ---------------------------------------------------------------- AWS helpers (replaceable)
def _get(key: dict[str, str]) -> dict[str, Any] | None:
    return ddb_table().get_item(Key=key, ConsistentRead=True).get("Item")


def _set_lifecycle(key: dict[str, str], values: dict[str, Any]) -> None:
    names = {f"#{k}": k for k in values}
    vals = {f":{k}": v for k, v in values.items()}
    ddb_table().update_item(
        Key=key,
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in values),
        ConditionExpression="attribute_exists(PK)",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )


def _query(pk: str, sk_prefix: str = "") -> list[dict[str, Any]]:
    cond = Key("PK").eq(pk)
    if sk_prefix:
        cond = cond & Key("SK").begins_with(sk_prefix)
    out: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": cond, "ConsistentRead": True}
    while True:
        r = ddb_table().query(**kwargs)
        out += r["Items"]
        if "LastEvaluatedKey" not in r:
            return out
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def _ddb_delete(keys: list[dict[str, str]]) -> None:
    """Deletes in batches of 25 (batch_writer retries unprocessed items). Absent keys are fine."""
    for i in range(0, len(keys), BATCH):
        with ddb_table().batch_writer() as bw:
            for k in keys[i : i + BATCH]:
                bw.delete_item(Key={"PK": k["PK"], "SK": k["SK"]})


def _ddb_delete_one(key: dict[str, str]) -> None:
    ddb_table().delete_item(Key=key)


def _s3_list(prefix: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    pager = s3().get_paginator("list_objects_v2")
    for page in pager.paginate(Bucket=get_settings().data_bucket, Prefix=prefix):
        out += [(o["Key"], int(o["Size"])) for o in page.get("Contents", [])]
    return out


def _s3_delete(keys: list[str]) -> None:
    """Versioned bucket: creates delete markers; older versions stay for the lifecycle window."""
    for i in range(0, len(keys), 1000):
        r = s3().delete_objects(
            Bucket=get_settings().data_bucket,
            Delete={"Objects": [{"Key": k} for k in keys[i : i + 1000]], "Quiet": True},
        )
        if r.get("Errors"):
            raise RuntimeError(f"S3 delete failed for {len(r['Errors'])} object(s)")


# ---------------------------------------------------------------- archive / restore
def set_archived(c: str, p: str | None, v: str | None, actor: str, archived: bool) -> None:
    """Idempotent. Audit line is written by the caller route."""
    if archived:
        _set_lifecycle(
            item_key(c, p, v),
            {"lifecycle": ARCHIVED, "archived_at": store.now(), "archived_by": actor},
        )
    else:
        _set_lifecycle(
            item_key(c, p, v), {"lifecycle": ACTIVE, "archived_at": None, "archived_by": None}
        )


# ---------------------------------------------------------------- alias cleanup
def _project_alias_keys(c: str, p: str, aliases: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Every alias that (directly or through a chain) points at project p, plus view aliases
    keyed by or pointing into any of those project slugs."""
    names = {p}
    changed = True
    while changed:
        changed = False
        for a in aliases:
            sk = a["SK"]
            if sk.startswith("ALIAS#PROJECT#") and a.get("target") in names:
                old = sk.split("#", 2)[2]
                if old not in names:
                    names.add(old)
                    changed = True
    out = []
    for a in aliases:
        sk = a["SK"]
        if sk.startswith("ALIAS#PROJECT#"):
            if sk.split("#", 2)[2] in names and sk.split("#", 2)[2] != p:
                out.append({"PK": a["PK"], "SK": sk})
        elif sk.startswith("ALIAS#VIEW#"):
            src_p = sk.split("#")[2]
            tgt_p = str(a.get("target", "")).split("#")[0]
            if src_p in names or tgt_p in names:
                out.append({"PK": a["PK"], "SK": sk})
    return out


def _view_alias_keys(p: str, v: str, aliases: list[dict[str, Any]]) -> list[dict[str, str]]:
    pairs = {(p, v)}
    changed = True
    while changed:
        changed = False
        for a in aliases:
            if not a["SK"].startswith("ALIAS#VIEW#"):
                continue
            _, _, ap, av = a["SK"].split("#", 3)
            tp, _, tv = str(a.get("target", "")).partition("#")
            if (tp, tv) in pairs and (ap, av) not in pairs:
                pairs.add((ap, av))
                changed = True
    return [
        {"PK": a["PK"], "SK": a["SK"]}
        for a in aliases
        if a["SK"].startswith("ALIAS#VIEW#") and tuple(a["SK"].split("#", 3)[2:4]) in pairs
    ]


# ---------------------------------------------------------------- preview
def preview(c: str, p: str | None = None, v: str | None = None) -> dict[str, int]:
    objs = _s3_list(prefix_of(c, p, v))
    pk = f"CLIENT#{c}"
    if v:
        projects, views, users = 0, 1, 0
    elif p:
        projects, views, users = 1, len(_query(pk, f"VIEW#{p}#")), 0
    else:
        projects, views = len(_query(pk, "PROJECT#")), len(_query(pk, "VIEW#"))
        users = len(_query(pk, "USER#"))
    return {
        "projects": projects,
        "views": views,
        "revisions": sum(1 for k, _ in objs if _REV.search(k)),
        "s3_objects": len(objs),
        "s3_bytes": sum(s for _, s in objs),
        "client_users": users,
    }


# ---------------------------------------------------------------- permanent delete
def _own_cleanup(c: str, p: str | None, v: str | None) -> None:
    """Delete every DynamoDB item belonging to the item; its own record is removed LAST."""
    pk = f"CLIENT#{c}"
    own = item_key(c, p, v)
    if v and p:
        aliases = _query(pk, "ALIAS#VIEW#")
        _ddb_delete(_view_alias_keys(p, v, aliases))
    elif p:
        items = _query(pk, f"VIEW#{p}#") + _query(pk, f"IMPORT#{p}#")
        aliases = _query(pk, "ALIAS#")
        _ddb_delete(
            [{"PK": i["PK"], "SK": i["SK"]} for i in items] + _project_alias_keys(c, p, aliases)
        )
    else:
        items = _query(pk)
        mirrors = [i["SK"].split("#", 1)[1] for i in items if i["SK"].startswith("USER#")]
        # access items first (found through the mirrors), so a retry can still find them
        _ddb_delete([{"PK": f"USER#{sub}", "SK": f"CLIENT#{c}"} for sub in mirrors])
        _ddb_delete([{"PK": i["PK"], "SK": i["SK"]} for i in items if i["SK"] != own["SK"]])
    _ddb_delete_one(own)


def permanent_delete(c: str, p: str | None, v: str | None, actor: str) -> dict[str, int]:
    key = item_key(c, p, v)
    item = _get(key)
    if not item:
        raise Missing(path_of(c, p, v))
    if state(item) not in (ARCHIVED, DELETING):
        raise NotArchived(path_of(c, p, v))
    prefix = prefix_of(c, p, v)
    if not _PREFIX_OK.match(prefix):  # never delete outside one client/project/view prefix
        raise ValueError("refusing to delete: bad prefix")
    counts = preview(c, p, v)
    what = "view" if v else "project" if p else "client"
    audit(actor, f"delete_{what}_started", f"{path_of(c, p, v)} {counts}", c)
    _set_lifecycle(key, {"lifecycle": DELETING})  # hides it everywhere; retries resume
    _s3_delete([k for k, _ in _s3_list(prefix)])
    _own_cleanup(c, p, v)
    audit(actor, f"delete_{what}", f"{path_of(c, p, v)} {counts}", c)
    return counts
