"""Renaming project and view slugs without breaking old links (docs/DATA_MODEL.md, aliases).

Order (every step is safe to retry; a failure leaves the old slug fully working):
  1. copy every S3 object under the old prefix to the new prefix, verify count + sizes
  2. DynamoDB TransactWriteItems: put new item(s), delete old item(s), put alias for the old slug
     (projects with more items than fit in one transaction go in batches, with a
     `rename_in_progress` marker on the old project item)
  3. delete the old S3 objects (best effort; bucket versioning is the safety net)

Aliases (PK CLIENT#{c}):
  SK ALIAS#PROJECT#{old}                -> target "{new}"
  SK ALIAS#VIEW#{old_project}#{old_view} -> target "{new_project}#{new_view}"
Client slugs are never renamed (they are subdomains and the key for user access).

The low-level helpers (_get_item, _query, _transact, _s3_*) are the only code that touches AWS,
so tests replace them with an in-memory fake.
"""

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from app import store
from app.aws import ddb_client, ddb_table, s3
from app.config import get_settings

TX_LIMIT = 100  # DynamoDB TransactWriteItems max operations
MAX_HOPS = 10  # alias chain depth guard
_SER = TypeSerializer()
log = logging.getLogger("rdb.renames")

_audit = logging.getLogger("rdb.audit")
if not _audit.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _audit.addHandler(_h)
    _audit.setLevel(logging.INFO)
    _audit.propagate = False


class SlugConflict(Exception):
    """The new slug is taken (by a live resource or another resource's old alias). HTTP 409."""


class RenameFailed(Exception):
    """A step failed; nothing is half done, or the message says exactly how to retry."""


# ---------------------------------------------------------------- AWS access (replaceable)
def _pk(c: str) -> str:
    return f"CLIENT#{c}"


def _get_item(key: dict[str, str]) -> dict[str, Any] | None:
    return ddb_table().get_item(Key=key, ConsistentRead=True).get("Item")


def _query(pk: str, sk_prefix: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(pk) & Key("SK").begins_with(sk_prefix),
        "ConsistentRead": True,
    }
    while True:
        r = ddb_table().query(**kwargs)
        out += r["Items"]
        if "LastEvaluatedKey" not in r:
            return out
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def _typed(d: dict[str, Any]) -> dict[str, Any]:
    return {k: _SER.serialize(v) for k, v in d.items()}


def _transact(ops: list[dict[str, Any]]) -> None:
    """ops: {"op": "put", "item", "new": bool} | {"op": "delete", "key", "expect": {attr: val}}
    | {"op": "mark", "key", "value"}. All-or-nothing; len(ops) <= TX_LIMIT."""
    table = get_settings().table_name
    items: list[dict[str, Any]] = []
    for o in ops:
        if o["op"] == "put":
            p: dict[str, Any] = {"TableName": table, "Item": _typed(o["item"])}
            if o.get("new"):
                p["ConditionExpression"] = "attribute_not_exists(PK)"
            items.append({"Put": p})
        elif o["op"] == "delete":
            d: dict[str, Any] = {"TableName": table, "Key": _typed(o["key"])}
            cond, vals = ["attribute_exists(PK)"], {}
            for i, (k, v) in enumerate((o.get("expect") or {}).items()):
                cond.append(f"{k} = :e{i}")
                vals[f":e{i}"] = _SER.serialize(v)
            d["ConditionExpression"] = " AND ".join(cond)
            if vals:
                d["ExpressionAttributeValues"] = vals
            items.append({"Delete": d})
        else:  # mark
            items.append(
                {
                    "Update": {
                        "TableName": table,
                        "Key": _typed(o["key"]),
                        "UpdateExpression": "SET rename_in_progress = :m",
                        "ConditionExpression": "attribute_exists(PK) AND "
                        "(attribute_not_exists(rename_in_progress) OR rename_in_progress = :m)",
                        "ExpressionAttributeValues": {":m": _SER.serialize(o["value"])},
                    }
                }
            )
    try:
        ddb_client().transact_write_items(TransactItems=items)
    except ClientError as e:
        if e.response["Error"]["Code"] == "TransactionCanceledException":
            raise RenameFailed(
                "The item changed (or the new slug was taken) while renaming. Nothing was "
                "changed; try again."
            ) from None
        raise


def _s3_list(prefix: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for page in (
        s3()
        .get_paginator("list_objects_v2")
        .paginate(Bucket=get_settings().data_bucket, Prefix=prefix)
    ):
        for o in page.get("Contents", []):
            out[o["Key"]] = int(o["Size"])
    return out


def _s3_copy(src: str, dst: str) -> None:
    bucket = get_settings().data_bucket
    s3().copy_object(Bucket=bucket, Key=dst, CopySource={"Bucket": bucket, "Key": src})


def _s3_delete(keys: list[str]) -> None:
    for i in range(0, len(keys), 1000):
        s3().delete_objects(
            Bucket=get_settings().data_bucket,
            Delete={"Objects": [{"Key": k} for k in keys[i : i + 1000]], "Quiet": True},
        )


# ---------------------------------------------------------------- aliases
def _alias_target(c: str, sk: str) -> str | None:
    item = _get_item({"PK": _pk(c), "SK": sk})
    return str(item["target"]) if item else None


def resolve(c: str, p: str, v: str | None = None) -> tuple[str, str | None]:
    """Canonical (project, view) for possibly-old slugs. Unknown slugs come back unchanged.
    View aliases are keyed by the project slug at rename time, so a project rename chains."""
    for _ in range(MAX_HOPS):
        changed = False
        if v is not None:
            t = _alias_target(c, f"ALIAS#VIEW#{p}#{v}")
            if t:
                p, v = t.split("#", 1)
                changed = True
        t = _alias_target(c, f"ALIAS#PROJECT#{p}")
        if t:
            p, changed = t, True
        if not changed:
            break
    return p, v


def project_slug_free(c: str, slug: str) -> bool:
    """For CREATING a project: no live project and no old alias on this slug."""
    return (
        _get_item({"PK": _pk(c), "SK": f"PROJECT#{slug}"}) is None
        and _alias_target(c, f"ALIAS#PROJECT#{slug}") is None
    )


def view_slug_free(c: str, p: str, slug: str) -> bool:
    return (
        _get_item({"PK": _pk(c), "SK": f"VIEW#{p}#{slug}"}) is None
        and _alias_target(c, f"ALIAS#VIEW#{p}#{slug}") is None
    )


def _alias_put(c: str, sk: str, target: str) -> dict[str, Any]:
    item = {"PK": _pk(c), "SK": sk, "target": target, "created_at": store.now()}
    return {"op": "put", "item": item}


def _check_target(c: str, kind: str, p: str, old: str, new: str) -> bool:
    """Raise SlugConflict if `new` can't be used. True if it is an old alias of THIS resource
    (reclaim: the alias is removed in the same transaction)."""
    if kind == "project":
        if _get_item({"PK": _pk(c), "SK": f"PROJECT#{new}"}):
            raise SlugConflict(f'A project with the slug "{new}" already exists.')
        if _alias_target(c, f"ALIAS#PROJECT#{new}") is None:
            return False
        if resolve(c, new)[0] == old:
            return True
        raise SlugConflict(f'"{new}" is the old link of another project and can\'t be reused.')
    if _get_item({"PK": _pk(c), "SK": f"VIEW#{p}#{new}"}):
        raise SlugConflict(f'A view with the slug "{new}" already exists in this project.')
    if _alias_target(c, f"ALIAS#VIEW#{p}#{new}") is None:
        return False
    if resolve(c, p, new) == (p, old):
        return True
    raise SlugConflict(f'"{new}" is the old link of another view and can\'t be reused.')


# ---------------------------------------------------------------- S3 move
def copy_prefix(src: str, dst: str) -> int:
    """Copy every object under src to dst (overwrites; idempotent), then verify names + sizes."""
    assert src.endswith("/") and dst.endswith("/") and not dst.startswith(src)
    objs = _s3_list(src)
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda k: _s3_copy(k, dst + k[len(src) :]), objs))
    got = _s3_list(dst)
    bad = [k for k, size in objs.items() if got.get(dst + k[len(src) :]) != size]
    if bad:
        raise RenameFailed(
            f"Copying the files failed verification ({len(bad)} of {len(objs)} differ). "
            "Nothing was changed; try again."
        )
    return len(objs)


def delete_prefix_best_effort(prefix: str) -> None:
    try:
        keys = [k for k in _s3_list(prefix) if k.startswith(prefix)]
        _s3_delete(keys)
    except Exception:  # noqa: BLE001 - leftovers are harmless; versioning is the safety net
        log.exception("could not delete old objects under %s", prefix)


# ---------------------------------------------------------------- audit
def audit(actor: str, action: str, client: str, old: str, new: str) -> None:
    _audit.info(
        json.dumps(
            {"audit": action, "actor": actor, "client": client, "old_slug": old, "new_slug": new},
            sort_keys=True,
        )
    )


# ---------------------------------------------------------------- view rename
def _expect(item: dict[str, Any]) -> dict[str, Any]:
    """Optimistic check on delete: the old item must be unchanged since we read it."""
    return {k: item[k] for k in ("updated_at", "rev_counter") if k in item}


def rename_view(c: str, p: str, old: str, new: str, actor: str) -> dict[str, Any]:
    item = _get_item({"PK": _pk(c), "SK": f"VIEW#{p}#{old}"})
    if item is None:
        raise store.NotFound("view")
    if new == old:
        return item
    reclaim = _check_target(c, "view", p, old, new)
    copy_prefix(store.view_prefix(c, p, old), store.view_prefix(c, p, new))
    ops: list[dict[str, Any]] = [
        {"op": "put", "item": {**item, "SK": f"VIEW#{p}#{new}", "slug": new}, "new": True},
        {"op": "delete", "key": {"PK": _pk(c), "SK": item["SK"]}, "expect": _expect(item)},
        _alias_put(c, f"ALIAS#VIEW#{p}#{old}", f"{p}#{new}"),
    ]
    if reclaim:
        ops.append({"op": "delete", "key": {"PK": _pk(c), "SK": f"ALIAS#VIEW#{p}#{new}"}})
    _transact(ops)
    delete_prefix_best_effort(store.view_prefix(c, p, old))
    audit(actor, "rename_view_slug", c, f"{p}/{old}", f"{p}/{new}")
    return {**item, "slug": new}


# ---------------------------------------------------------------- project rename
def _rekey(item: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    kind, _, rest = item["SK"].partition("#")  # VIEW#{project}#{slug} / IMPORT#{project}#{id}
    assert rest.startswith(old + "#")
    out = {**item, "SK": f"{kind}#{new}#{rest[len(old) + 1 :]}"}
    if out.get("project_slug") == old:
        out["project_slug"] = new
    return out


def rename_project(c: str, old: str, new: str, actor: str) -> dict[str, Any]:
    item = _get_item({"PK": _pk(c), "SK": f"PROJECT#{old}"})
    if item is None:
        raise store.NotFound("project")
    if new == old:
        return item
    pending = item.get("rename_in_progress")
    if pending and pending != new:
        raise SlugConflict(
            f'A rename to "{pending}" is still in progress for this project. '
            f'Retry that rename ("{pending}") first.'
        )
    reclaim = _check_target(c, "project", old, old, new) if not pending else False
    copy_prefix(store.project_prefix(c, old), store.project_prefix(c, new))

    # everything keyed by the project slug in the sort key: views, import budget items
    children = _query(_pk(c), f"VIEW#{old}#") + _query(_pk(c), f"IMPORT#{old}#")
    pkey = {"PK": _pk(c), "SK": item["SK"]}
    new_proj = {k: v for k, v in item.items() if k != "rename_in_progress"}
    new_proj.update({"SK": f"PROJECT#{new}", "slug": new})
    final: list[dict[str, Any]] = [
        {"op": "put", "item": new_proj, "new": True},
        {"op": "delete", "key": pkey, "expect": _expect(item)},
        _alias_put(c, f"ALIAS#PROJECT#{old}", new),
    ]
    if reclaim:
        final.append({"op": "delete", "key": {"PK": _pk(c), "SK": f"ALIAS#PROJECT#{new}"}})

    def move(ch: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"op": "put", "item": _rekey(ch, old, new), "new": True},
            {"op": "delete", "key": {"PK": ch["PK"], "SK": ch["SK"]}, "expect": _expect(ch)},
        ]

    if len(final) + 2 * len(children) <= TX_LIMIT:
        _transact(final + [o for ch in children for o in move(ch)])
    else:
        _move_in_batches(item, pkey, new, children, final, move)
    delete_prefix_best_effort(store.project_prefix(c, old))
    audit(actor, "rename_project_slug", c, old, new)
    return new_proj


def _move_in_batches(
    item: dict[str, Any],
    pkey: dict[str, str],
    new: str,
    children: list[dict[str, Any]],
    final: list[dict[str, Any]],
    move: Any,
) -> None:
    """Too many items for one transaction: mark the old project, move children in batches, then
    swap the project item. Any failure leaves the marker; retrying the same rename continues
    (moved children are no longer under the old slug, so they are not listed again)."""
    step = (TX_LIMIT - 2) // 2
    try:
        _transact([{"op": "mark", "key": pkey, "value": new}])
        for i in range(0, len(children), step):
            _transact([o for ch in children[i : i + step] for o in move(ch)])
        # the marker bumped nothing the final delete expects (no updated_at/rev_counter change)
        _transact(final)
    except RenameFailed as e:
        raise RenameFailed(
            f"The rename is only partly done ({e}). Retry the same rename to finish it; "
            "the old links keep working meanwhile."
        ) from None
    except ClientError as e:
        raise RenameFailed(
            f"The rename is only partly done ({e.response['Error']['Code']}). "
            "Retry the same rename to finish it."
        ) from None
