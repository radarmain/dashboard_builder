"""Archive / restore / delete-preview / permanent delete (all under /api). Rules: app/lifecycle.py.

Client: everything admin only. Project and view: archive + restore admin or analyst; preview and
permanent delete admin only (a plain 403 for analysts before anything else is checked).
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app import lifecycle, store
from app.auth import Admin, Builder, User
from app.routes_core import canon, clean, need_client, need_project, need_view
from app.users import audit

router = APIRouter(prefix="/api")
log = logging.getLogger("rdb.lifecycle")

C = "/clients/{c}"
P = C + "/projects/{p}"
V = P + "/views/{v}"


def _item(c: str, p: str | None, v: str | None) -> dict[str, Any]:
    if v and p:
        return need_view(c, p, v)
    if p:
        return need_project(c, p)
    return need_client(c)


def _set(c: str, p: str | None, v: str | None, user: User, archived: bool) -> dict[str, Any]:
    item = _item(c, p, v)
    already = lifecycle.state(item) == (lifecycle.ARCHIVED if archived else lifecycle.ACTIVE)
    if not already:
        lifecycle.set_archived(c, p, v, user.sub, archived)
        what = "view" if v else "project" if p else "client"
        audit(
            user.sub, ("archive_" if archived else "restore_") + what, lifecycle.path_of(c, p, v), c
        )
    return clean(_item(c, p, v))


def _delete_target(c: str, p: str | None, v: str | None) -> tuple[dict[str, Any], str]:
    """Raw item (a `deleting` item must stay reachable so a retry can resume) and its slug."""
    if p:
        p, v2 = canon(c, p, v)
        v = v2 or None
    item = lifecycle._get(lifecycle.item_key(c, p, v))
    if not item:
        raise HTTPException(404, "not found")
    return item, item["slug"]


def _path(item: dict[str, Any], p: str | None, v: str | None) -> tuple[str | None, str | None]:
    """Canonical (project, view) slugs for an item (the URL may have used an old alias)."""
    if v:
        return item["project_slug"], item["slug"]
    if p:
        return item["slug"], None
    return None, None


def _preview(c: str, p: str | None, v: str | None) -> dict[str, Any]:
    item, slug = _delete_target(c, p, v)
    if lifecycle.state(item) == lifecycle.DELETING:
        raise HTTPException(404, "not found")
    return {
        "slug": slug,
        "name": item.get("name", slug),
        "lifecycle": lifecycle.state(item),
        **lifecycle.preview(c, *_path(item, p, v)),
    }


def _delete(c: str, p: str | None, v: str | None, confirm: str, user: User) -> dict[str, Any]:
    item, slug = _delete_target(c, p, v)
    if lifecycle.state(item) == lifecycle.ACTIVE:
        raise HTTPException(409, "archive it first: only archived items can be deleted permanently")
    if confirm != slug:
        raise HTTPException(400, "confirmation slug does not match")
    cp, cv = _path(item, p, v)
    try:
        counts = lifecycle.permanent_delete(c, cp, cv, user.sub)
    except lifecycle.Missing:
        raise HTTPException(404, "not found") from None
    except lifecycle.NotArchived:
        raise HTTPException(409, "archive it first") from None
    except Exception:  # noqa: BLE001 - item stays "deleting"; the same call resumes
        log.exception("permanent delete failed for %s", lifecycle.path_of(c, cp, cv))
        raise HTTPException(
            503, "Delete didn't finish. Nothing is lost; run the same delete again to resume."
        ) from None
    return {"deleted": lifecycle.path_of(c, cp, cv), "counts": counts}


# ---------- client (admin only) ----------
@router.post(C + "/archive")
def archive_client(c: str, user: Admin) -> dict[str, Any]:
    return _set(c, None, None, user, True)


@router.post(C + "/restore")
def restore_client(c: str, user: Admin) -> dict[str, Any]:
    return _set(c, None, None, user, False)


@router.get(C + "/delete-preview")
def client_preview(c: str, _: Admin) -> dict[str, Any]:
    return _preview(c, None, None)


@router.delete(C)
def delete_client(c: str, user: Admin, confirm: str = "") -> dict[str, Any]:
    return _delete(c, None, None, confirm, user)


# ---------- project ----------
@router.post(P + "/archive")
def archive_project(c: str, p: str, user: Builder) -> dict[str, Any]:
    return _set(c, canon(c, p)[0], None, user, True)


@router.post(P + "/restore")
def restore_project(c: str, p: str, user: Builder) -> dict[str, Any]:
    return _set(c, canon(c, p)[0], None, user, False)


@router.get(P + "/delete-preview")
def project_preview(c: str, p: str, _: Admin) -> dict[str, Any]:
    return _preview(c, p, None)


@router.delete(P)
def delete_project(c: str, p: str, user: Admin, confirm: str = "") -> dict[str, Any]:
    return _delete(c, p, None, confirm, user)


# ---------- view ----------
@router.post(V + "/archive")
def archive_view(c: str, p: str, v: str, user: Builder) -> dict[str, Any]:
    p, v = canon(c, p, v)
    return clean(store.view_public(_set(c, p, v, user, True)))


@router.post(V + "/restore")
def restore_view(c: str, p: str, v: str, user: Builder) -> dict[str, Any]:
    p, v = canon(c, p, v)
    return clean(store.view_public(_set(c, p, v, user, False)))


@router.get(V + "/delete-preview")
def view_preview(c: str, p: str, v: str, _: Admin) -> dict[str, Any]:
    return _preview(c, p, v)


@router.delete(V)
def delete_view(c: str, p: str, v: str, user: Admin, confirm: str = "") -> dict[str, Any]:
    return _delete(c, p, v, confirm, user)
