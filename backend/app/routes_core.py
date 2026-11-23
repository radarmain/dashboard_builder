"""Clients / projects / views / revisions / admin access. All under /api."""

from typing import Any

from fastapi import APIRouter, HTTPException

from app import cognito, renames, store
from app.auth import Admin, Builder, ClientAccess, CurrentUser
from app.models import (
    AccessGrant,
    ClientCreate,
    ProjectCreate,
    ProjectPatch,
    PublishIn,
    ViewCreate,
    ViewPatch,
    ViewSpec,
)
from app.slugs import CLIENT_SLUG_MAX, RESERVED_SLUGS
from app.sql_validation import SqlValidationError, validate_sql
from app.users import audit

router = APIRouter(prefix="/api")

HIDDEN = {"PK", "SK", "rev_counter"}


def clean(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in HIDDEN}


def canon(c: str, p: str, v: str | None = None) -> tuple[str, str]:
    """Canonical (project, view) slugs; old slugs resolve through aliases (renames.py).
    Call after authorization. Unknown slugs come back unchanged (-> 404 as before)."""
    p2, v2 = renames.resolve(c, p, v)
    return p2, v2 or ""


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
    out["elements"] = [
        {**{k: v for k, v in e.items() if k not in ("import", "review_status")}, "sql": ""}
        for e in spec.get("elements", [])
    ]
    return out


UNREVIEWED = {"unverified", "needs_review", "differs"}


def unreviewed_imports(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Imported elements still needing an analyst and not marked reviewed."""
    return [
        {
            "id": e["id"],
            "title": e.get("title", ""),
            "status": e["import"]["status"],
            "sheet": e["import"].get("sheet_name", ""),
        }
        for e in spec.get("elements", [])
        if (e.get("import") or {}).get("status") in UNREVIEWED
        and e.get("review_status") != "reviewed"
    ]


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
    if not renames.project_slug_free(c, body.slug):
        raise HTTPException(409, "project slug already exists (or is an old link of a project)")
    try:
        return clean(store.create_project(c, body.slug, body.name))
    except store.Conflict:
        raise HTTPException(409, "project slug already exists") from None


@router.patch("/clients/{c}/projects/{p}")
def patch_project(c: str, p: str, body: ProjectPatch, user: Builder) -> dict[str, Any]:
    p = canon(c, p)[0]
    item = need_project(c, p)
    try:
        if body.slug and body.slug != p:
            item = renames.rename_project(c, p, body.slug, user.sub)
            p = body.slug
        if body.name:
            item = store.update_project_name(c, p, body.name)
    except renames.SlugConflict as e:
        raise HTTPException(409, str(e)) from None
    except renames.RenameFailed as e:
        raise HTTPException(503, str(e)) from None
    return clean(item)


@router.get("/clients/{c}/projects/{p}")
def get_project(c: str, p: str, _: Builder) -> dict[str, Any]:
    p = canon(c, p)[0]
    item = clean(need_project(c, p))
    item["config"] = store.get_project_config(c, p).model_dump()
    return item


# ---------- views ----------
@router.get("/clients/{c}/projects/{p}/views")
def list_views(c: str, p: str, _: Builder) -> list[dict[str, Any]]:
    p = canon(c, p)[0]
    need_project(c, p)
    return [clean(store.view_public(i)) for i in store.list_views(c, p)]


@router.post("/clients/{c}/projects/{p}/views", status_code=201)
def create_view(c: str, p: str, body: ViewCreate, _: Builder) -> dict[str, Any]:
    p = canon(c, p)[0]
    need_project(c, p)
    if not renames.view_slug_free(c, p, body.slug):
        raise HTTPException(409, "view slug already exists (or is an old link of a view)")
    try:
        item = store.create_view(c, p, body.slug, body.name, body.description)
        return clean(store.view_public(item))
    except store.Conflict:
        raise HTTPException(409, "view slug already exists") from None


@router.get("/clients/{c}/projects/{p}/views/{v}")
def get_view(c: str, p: str, v: str, _: Builder) -> dict[str, Any]:
    p, v = canon(c, p, v)
    return clean(store.view_public(need_view(c, p, v)))


@router.patch("/clients/{c}/projects/{p}/views/{v}")
def patch_view(c: str, p: str, v: str, body: ViewPatch, user: Builder) -> dict[str, Any]:
    p, v = canon(c, p, v)
    need_view(c, p, v)
    try:
        if body.slug and body.slug != v:
            renames.rename_view(c, p, v, body.slug, user.sub)
            v = body.slug
    except renames.SlugConflict as e:
        raise HTTPException(409, str(e)) from None
    except renames.RenameFailed as e:
        raise HTTPException(503, str(e)) from None
    if body.name or body.description is not None:
        item = store.update_view_meta(c, p, v, body.name, body.description)
    else:  # slug-only change: don't touch updated_at
        item = need_view(c, p, v)
    return clean(store.view_public(item))


# ---------- save / draft / publish / revisions ----------
@router.post("/clients/{c}/projects/{p}/views/{v}/save")
def save_view(c: str, p: str, v: str, spec: ViewSpec, user: Builder) -> dict[str, Any]:
    p, v = canon(c, p, v)
    need_view(c, p, v)
    n = store.write_revision(c, p, v, spec, user.email or user.sub)
    return {"revision": n, "draft_revision": n}


@router.get("/clients/{c}/projects/{p}/views/{v}/draft")
def get_draft(c: str, p: str, v: str, _: Builder) -> dict[str, Any]:
    p, v = canon(c, p, v)
    item = need_view(c, p, v)
    n = item.get("draft_revision")
    spec = store.read_revision(c, p, v, n) if n is not None else None
    if not spec:
        raise HTTPException(404, "no draft")
    return {**spec, "view": {**spec.get("view", {}), "slug": v}}


@router.get("/clients/{client_slug}/projects/{p}/views/{v}/published")
def get_published(client_slug: str, p: str, v: str, user: ClientAccess) -> dict[str, Any]:
    p, v = canon(client_slug, p, v)
    spec = load_published(client_slug, p, v)
    return spec if user.is_builder else strip_sql(spec)


def load_published(c: str, p: str, v: str) -> dict[str, Any]:
    need_view(c, p, v)
    ptr = store.s3_get_json(f"{store.view_prefix(c, p, v)}published.json")
    spec = store.read_revision(c, p, v, ptr["revision"]) if ptr else None
    if not spec:
        raise HTTPException(404, "not published")
    # canonical slug, not the one saved inside an old revision
    return {**spec, "view": {**spec.get("view", {}), "slug": v}}


@router.post("/clients/{c}/projects/{p}/views/{v}/publish")
def publish_view(
    c: str, p: str, v: str, user: Builder, body: PublishIn | None = None
) -> dict[str, Any]:
    p, v = canon(c, p, v)
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
    who = user.email or user.sub
    if pending := unreviewed_imports(spec):
        if not (body and body.confirm_unreviewed):
            raise HTTPException(
                409,
                {
                    "needs_confirmation": True,
                    "reason": "unreviewed_imports",
                    "elements": pending,
                },
            )
        audit(
            who,
            "publish_unreviewed_confirmed",
            f"{v}:{','.join(x['id'] for x in pending)}"[:300],
            c,
        )
    return {"published_revision": store.publish(c, p, v, who, spec)}


@router.get("/clients/{c}/projects/{p}/views/{v}/revisions")
def list_revisions(c: str, p: str, v: str, _: Builder) -> list[dict[str, Any]]:
    p, v = canon(c, p, v)
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
    p, v = canon(c, p, v)
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
