"""Per-user favorites (builder roles only). Items live under the user's own partition:

  PK USER#{sub}   SK FAV#CLIENT#{c} | FAV#PROJECT#{c}#{p} | FAV#VIEW#{c}#{p}#{v}   created_at

Reads resolve old slugs through aliases (and rewrite the stored item), prune items that no longer
exist or are being deleted, and hide archived ones. Never exposed to client users or the portal.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import lifecycle, renames, store
from app.auth import Builder
from app.aws import ddb_table

router = APIRouter(prefix="/api/me")
MAX_FAVORITES = 50


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")  # keeps insertion order


class FavIn(BaseModel):
    type: Literal["client", "project", "view"]
    client: str
    project: str | None = None
    view: str | None = None


def _sk(type_: str, c: str, p: str | None, v: str | None) -> str:
    parts = {"client": [c], "project": [c, p], "view": [c, p, v]}[type_]
    return f"FAV#{type_.upper()}#" + "#".join(x or "" for x in parts)


def _parse(sk: str) -> FavIn | None:
    _, t, rest = sk.split("#", 2)
    bits = rest.split("#")
    type_ = t.lower()
    try:
        if type_ == "client" and len(bits) == 1:
            return FavIn(type="client", client=bits[0])
        if type_ == "project" and len(bits) == 2:
            return FavIn(type="project", client=bits[0], project=bits[1])
        if type_ == "view" and len(bits) == 3:
            return FavIn(type="view", client=bits[0], project=bits[1], view=bits[2])
    except ValueError:
        return None
    return None


def _list(sub: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(f"USER#{sub}") & Key("SK").begins_with("FAV#")
    }
    while True:
        r = ddb_table().query(**kwargs)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return sorted(items, key=lambda i: (i.get("created_at", ""), i["SK"]))


def _delete(sub: str, sk: str) -> None:
    ddb_table().delete_item(Key={"PK": f"USER#{sub}", "SK": sk})


class _Lookup:
    """Per-request memo so a client or project shared by many favorites is read once."""

    def __init__(self) -> None:
        self.memo: dict[tuple[str, ...], Any] = {}

    def get(self, *key: str) -> dict[str, Any] | None:
        if key not in self.memo:
            fn = {1: store.get_client, 2: store.get_project, 3: store.get_view}[len(key)]
            self.memo[key] = fn(*key)
        return self.memo[key]


def _canonical(f: FavIn) -> FavIn:
    if f.type == "client" or not f.project:
        return f
    p, v = renames.resolve(f.client, f.project, f.view if f.type == "view" else None)
    return FavIn(
        type=f.type, client=f.client, project=p, view=(v or None) if f.type == "view" else None
    )


def _resolve(f: FavIn, look: _Lookup) -> tuple[dict[str, Any], str] | None:
    """(entry fields, effective lifecycle) or None when the item is gone."""
    client = look.get(f.client)
    if not client:
        return None
    chain: list[dict[str, Any] | None] = [client]
    names = [client.get("name") or f.client]
    status = None
    if f.project:
        project = look.get(f.client, f.project)
        if not project:
            return None
        chain.append(project)
        names.append(project.get("name") or f.project)
        status = project.get("status")
    if f.view and f.project:
        view = look.get(f.client, f.project, f.view)
        if not view:
            return None
        chain.append(view)
        names.append(view.get("name") or f.view)
        pub, d = view.get("published_revision"), view.get("draft_revision")
        status = (
            "draft" if pub is None else "changes" if d is not None and d != pub else "published"
        )
    return {
        "name": names[-1],
        "path": " / ".join(names[:-1]),
        "status": status,
    }, lifecycle.effective(*chain)


def read_favorites(sub: str) -> list[dict[str, Any]]:
    look = _Lookup()
    out: list[dict[str, Any]] = []
    for it in _list(sub):
        f = _parse(it["SK"])
        if not f:
            _delete(sub, it["SK"])
            continue
        canon = _canonical(f)
        sk = _sk(canon.type, canon.client, canon.project, canon.view)
        res = _resolve(canon, look)
        if not res or res[1] == lifecycle.DELETING:  # gone or being deleted: prune
            _delete(sub, it["SK"])
            continue
        if sk != it["SK"]:  # renamed since it was saved: rewrite lazily
            ddb_table().put_item(
                Item={"PK": f"USER#{sub}", "SK": sk, "created_at": it["created_at"]}
            )
            _delete(sub, it["SK"])
        if res[1] == lifecycle.ARCHIVED:  # hidden, but kept so restoring brings it back
            continue
        out.append({**canon.model_dump(), **res[0], "created_at": it["created_at"]})
    return out


def add_favorite(sub: str, f: FavIn) -> None:
    if (f.type != "client" and not f.project) or (f.type == "view" and not f.view):
        raise HTTPException(400, "project/view slug required")
    f = _canonical(f)
    res = _resolve(f, _Lookup())
    if not res or res[1] == lifecycle.DELETING:
        raise HTTPException(404, "not found")
    sk = _sk(f.type, f.client, f.project, f.view)
    existing = _list(sub)
    if any(i["SK"] == sk for i in existing):
        return
    if len(existing) >= MAX_FAVORITES:
        raise HTTPException(409, f"You can have up to {MAX_FAVORITES} favorites. Remove one first.")
    try:
        ddb_table().put_item(
            Item={"PK": f"USER#{sub}", "SK": sk, "created_at": _stamp()},
            ConditionExpression="attribute_not_exists(PK)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


@router.get("/favorites")
def get_favorites(user: Builder) -> list[dict[str, Any]]:
    return read_favorites(user.sub)


@router.put("/favorites", status_code=204)
def put_favorite(body: FavIn, user: Builder) -> None:
    add_favorite(user.sub, body)


@router.delete("/favorites", status_code=204)
def delete_favorite(body: FavIn, user: Builder) -> None:
    canon = _canonical(body)  # remove the stored item whether it holds the old or new slug
    for sk in {
        _sk(body.type, body.client, body.project, body.view),
        _sk(canon.type, canon.client, canon.project, canon.view),
    }:
        _delete(user.sub, sk)
