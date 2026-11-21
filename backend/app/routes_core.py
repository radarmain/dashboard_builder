"""Clients / projects / views / revisions / admin access. All under /api."""

from typing import Any

from fastapi import APIRouter, HTTPException

from app import cognito, store
from app.auth import Admin, Builder, ClientAccess, CurrentUser
from app.models import (
    AccessGrant,
    ClientCreate,
    ProjectCreate,
    ViewCreate,
    ViewPatch,
    ViewSpec,
)
from app.slugs import CLIENT_SLUG_MAX, RESERVED_SLUGS
from app.sql_validation import SqlValidationError, validate_sql

router = APIRouter(prefix="/api")

HIDDEN = {"PK", "SK", "rev_counter"}


def clean(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in HIDDEN}


def need_client(c: str) -> dict[str, Any]:
    item = store.get_client(c)
    if not item:
        raise HTTPException(404, "client not found")
    return item


def need_project(c: str, p: str) -> dict[str, Any]:
    need_client(c)
    item = store.get_project(c, p)
    if not item:
        raise HTTPException(404, "project not found")
    return item


def need_view(c: str, p: str, v: str) -> dict[str, Any]:
    need_project(c, p)
    item = store.get_view(c, p, v)
    if not item:
        raise HTTPException(404, "view not found")
    return item


def strip_sql(spec: dict[str, Any]) -> dict[str, Any]:
    """Client users never receive SQL."""
    out = dict(spec)
    out["elements"] = [{**e, "sql": ""} for e in spec.get("elements", [])]
    return out


# ---------- clients ----------
@router.get("/slug-rules")
def slug_rules(_: Builder) -> dict[str, Any]:
    return {
        "reserved": sorted(RESERVED_SLUGS),
        "client_slug_max": CLIENT_SLUG_MAX,
        "base_domain": "radar-analytics.com",
    }


@router.get("/clients")
def list_clients(_: Builder) -> list[dict[str, Any]]:
    return [clean(i) for i in store.list_clients()]


@router.post("/clients", status_code=201)
def create_client(body: ClientCreate, user: Builder) -> dict[str, Any]:
    try:
        return clean(store.create_client(body.slug, body.name, user.email or user.sub))
    except store.Conflict:
        raise HTTPException(409, "client slug already exists") from None


@router.get("/clients/{c}")
def get_client(c: str, _: Builder) -> dict[str, Any]:
    return clean(need_client(c))


# ---------- projects ----------
@router.get("/clients/{c}/projects")
def list_projects(c: str, _: Builder) -> list[dict[str, Any]]:
    need_client(c)
    return [clean(i) for i in store.list_projects(c)]


@router.post("/clients/{c}/projects", status_code=201)
def create_project(c: str, body: ProjectCreate, _: Builder) -> dict[str, Any]:
    need_client(c)
    try:
        return clean(store.create_project(c, body.slug, body.name))
    except store.Conflict:
        raise HTTPException(409, "project slug already exists") from None


@router.get("/clients/{c}/projects/{p}")
def get_project(c: str, p: str, _: Builder) -> dict[str, Any]:
    item = clean(need_project(c, p))
    item["config"] = store.get_project_config(c, p).model_dump()
    return item


# ---------- views ----------
@router.get("/clients/{c}/projects/{p}/views")
def list_views(c: str, p: str, _: Builder) -> list[dict[str, Any]]:
    need_project(c, p)
    return [clean(store.view_public(i)) for i in store.list_views(c, p)]


@router.post("/clients/{c}/projects/{p}/views", status_code=201)
def create_view(c: str, p: str, body: ViewCreate, _: Builder) -> dict[str, Any]:
    need_project(c, p)
    try:
        item = store.create_view(c, p, body.slug, body.name, body.description)
        return clean(store.view_public(item))
    except store.Conflict:
        raise HTTPException(409, "view slug already exists") from None


@router.get("/clients/{c}/projects/{p}/views/{v}")
def get_view(c: str, p: str, v: str, _: Builder) -> dict[str, Any]:
    return clean(store.view_public(need_view(c, p, v)))


@router.patch("/clients/{c}/projects/{p}/views/{v}")
def patch_view(c: str, p: str, v: str, body: ViewPatch, _: Builder) -> dict[str, Any]:
    need_view(c, p, v)
    item = store.update_view_meta(c, p, v, body.name, body.description)
    return clean(store.view_public(item))


# ---------- save / draft / publish / revisions ----------
@router.post("/clients/{c}/projects/{p}/views/{v}/save")
def save_view(c: str, p: str, v: str, spec: ViewSpec, user: Builder) -> dict[str, Any]:
    need_view(c, p, v)
    n = store.write_revision(c, p, v, spec, user.email or user.sub)
    return {"revision": n, "draft_revision": n}


@router.get("/clients/{c}/projects/{p}/views/{v}/draft")
def get_draft(c: str, p: str, v: str, _: Builder) -> dict[str, Any]:
    item = need_view(c, p, v)
    n = item.get("draft_revision")
    spec = store.read_revision(c, p, v, n) if n is not None else None
    if not spec:
        raise HTTPException(404, "no draft")
    return spec


@router.get("/clients/{client_slug}/projects/{p}/views/{v}/published")
def get_published(client_slug: str, p: str, v: str, user: ClientAccess) -> dict[str, Any]:
    spec = load_published(client_slug, p, v)
    return spec if user.is_builder else strip_sql(spec)


def load_published(c: str, p: str, v: str) -> dict[str, Any]:
    need_view(c, p, v)
    ptr = store.s3_get_json(f"{store.view_prefix(c, p, v)}published.json")
    spec = store.read_revision(c, p, v, ptr["revision"]) if ptr else None
    if not spec:
        raise HTTPException(404, "not published")
    return spec


@router.post("/clients/{c}/projects/{p}/views/{v}/publish")
def publish_view(c: str, p: str, v: str, user: Builder) -> dict[str, Any]:
    item = need_view(c, p, v)
    n = item.get("draft_revision")
    spec = store.read_revision(c, p, v, n) if n is not None else None
    if not spec:
        raise HTTPException(409, "nothing to publish: save a draft first")
    cfg = store.get_project_config(c, p)
    for el in spec.get("elements", []):  # refuse to publish SQL that wouldn't run
        if el.get("sql"):
            try:
                validate_sql(el["sql"], cfg.tables)
            except SqlValidationError as e:
                raise HTTPException(400, f"element {el['id']}: {e}") from None
    return {"published_revision": store.publish(c, p, v, user.email or user.sub, spec)}


@router.get("/clients/{c}/projects/{p}/views/{v}/revisions")
def list_revisions(c: str, p: str, v: str, _: Builder) -> list[dict[str, Any]]:
    item = need_view(c, p, v)
    out = []
    for n in store.list_revision_numbers(c, p, v)[: store.KEEP_REVISIONS]:
        spec = store.read_revision(c, p, v, n) or {}
        out.append(
            {
                "revision": n,
                "updated_at": spec.get("updated_at"),
                "updated_by": spec.get("updated_by"),
                "is_draft": n == item.get("draft_revision"),
                "is_published": n == item.get("published_revision"),
            }
        )
    return out


@router.post("/clients/{c}/projects/{p}/views/{v}/revisions/{n}/restore")
def restore_revision(c: str, p: str, v: str, n: int, user: Builder) -> dict[str, Any]:
    need_view(c, p, v)
    old = store.read_revision(c, p, v, n)
    if not old:
        raise HTTPException(404, "revision not found")
    new_n = store.write_revision(c, p, v, ViewSpec.model_validate(old), user.email or user.sub)
    return {"revision": new_n, "draft_revision": new_n, "restored_from": n}


# ---------- admin: client user access (maintains the CLIENT#/USER# mirror too) ----------
@router.post("/admin/access", status_code=201)
def grant_access(body: AccessGrant, user: Admin) -> dict[str, str]:
    need_client(body.client_slug)
    target = cognito.get_by_sub(body.user_sub)
    if not target:
        raise HTTPException(404, "user not found")
    if target.groups != ["client"]:
        raise HTTPException(409, "only client users can be given client access")
    store.grant_access(target.sub, body.client_slug, target.email.lower(), user.sub)
    return {"user_sub": body.user_sub, "client_slug": body.client_slug}


@router.delete("/admin/access")
def revoke_access(body: AccessGrant, _: Admin) -> dict[str, str]:
    store.revoke_access(body.user_sub, body.client_slug)
    return {"revoked": "ok"}


@router.get("/me")
def me(user: CurrentUser) -> dict[str, Any]:
    return {"sub": user.sub, "email": user.email, "groups": user.groups}
