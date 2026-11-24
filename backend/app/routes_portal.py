"""Client-facing portal API (/api/portal). Published views only; never drafts, SQL, table names,
revision numbers, user emails, byte counts or cache details.

Authorization is `ClientAccess` (admin/analyst: any client, which powers Client Preview; client
users: assigned clients only; anything else 404). It runs before any S3/BigQuery access.
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from app import lifecycle, store
from app.auth import ClientAccess, CurrentUser
from app.query_runner import QueryFailed, QueryRejected, run_query
from app.routes_core import canon, need_client, need_project

router = APIRouter(prefix="/api/portal")

NOT_FOUND = HTTPException(404, "not found")


def _client_ref(item: dict[str, Any]) -> dict[str, str]:
    return {"slug": item["slug"], "name": item["name"]}


def _card(view: dict[str, Any], project: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    counts = {k: int(v) for k, v in (summary.get("counts") or {}).items()}
    return {
        "slug": view["slug"],
        "name": view["name"],
        "description": view.get("description") or None,
        "project_slug": project["slug"],
        "project_name": project["name"],
        "published_at": summary.get("published_at"),
        "counts": counts,
        "chart_kinds": list(summary.get("chart_kinds") or []),
    }


def _project_cards(c: str, project: dict[str, Any]) -> dict[str, Any]:
    cards = []
    for v in store.list_views(c, project["slug"]):
        if lifecycle.state(v) != lifecycle.ACTIVE:
            continue
        summary = store.published_summary(v)
        if summary is not None:
            cards.append(_card(v, project, summary))
    cards.sort(key=lambda x: x["name"].lower())
    return {"slug": project["slug"], "name": project["name"], "views": cards}


def _published(c: str, p: str, v: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(view item, published spec, pointer). 404 unless the view is published."""
    need_project(c, p, "portal")  # lifecycle first: nothing below runs for archived content
    item = store.get_view(c, p, v)
    if (
        not item
        or lifecycle.state(item) != lifecycle.ACTIVE
        or item.get("published_revision") is None
    ):
        raise NOT_FOUND
    ptr = store.s3_get_json(f"{store.view_prefix(c, p, v)}published.json")
    spec = store.read_revision(c, p, v, ptr["revision"]) if ptr else None
    if not ptr or not spec:
        raise NOT_FOUND
    return item, spec, ptr


@router.get("/me")
def portal_me(user: CurrentUser) -> list[dict[str, str]]:
    """Clients a client user is assigned to. Builders get [] (they open clients directly)."""
    if user.is_builder or "client" not in user.groups:
        return []
    out = []
    for slug in store.list_access(user.sub):
        item = store.get_client(slug)
        if item and lifecycle.state(item) == lifecycle.ACTIVE:
            out.append(_client_ref(item))
    return sorted(out, key=lambda x: x["name"].lower())


@router.get("/clients/{client_slug}")
def portal_client(client_slug: str, _: ClientAccess) -> dict[str, Any]:
    client = need_client(client_slug, "portal")
    projects = [
        _project_cards(client_slug, p)
        for p in store.list_projects(client_slug)
        if lifecycle.state(p) == lifecycle.ACTIVE
    ]
    projects = [p for p in projects if p["views"]]
    projects.sort(key=lambda p: p["name"].lower())
    return {"client": _client_ref(client), "projects": projects}


@router.get("/clients/{client_slug}/projects/{p}")
def portal_project(client_slug: str, p: str, _: ClientAccess) -> dict[str, Any]:
    p = canon(client_slug, p)[0]
    client = need_client(client_slug, "portal")
    project = need_project(client_slug, p, "portal")
    return {"client": _client_ref(client), "project": _project_cards(client_slug, project)}


@router.get("/clients/{client_slug}/projects/{p}/views/{v}")
def portal_view(client_slug: str, p: str, v: str, _: ClientAccess) -> dict[str, Any]:
    p, v = canon(client_slug, p, v)
    client = need_client(client_slug, "portal")
    project = need_project(client_slug, p, "portal")
    item, spec, ptr = _published(client_slug, p, v)
    elements = [
        {
            "id": e["id"],
            "type": e["type"],
            "title": e.get("title", ""),
            "chart": e.get("chart"),
            "format": e.get("format", {}),
        }
        for e in spec.get("elements", [])
    ]
    return {
        "client": _client_ref(client),
        "project": {"slug": project["slug"], "name": project["name"]},
        "slug": item["slug"],  # canonical; the page replaces the URL when it differs
        "name": item["name"],
        "description": item.get("description") or None,
        "published_at": ptr.get("published_at"),
        "layout": spec.get("layout", []),
        "elements": elements,
    }


@router.get("/clients/{client_slug}/projects/{p}/views/{v}/data/{element_id}")
def portal_data(
    client_slug: str, p: str, v: str, element_id: str, _: ClientAccess
) -> dict[str, Any]:
    """Runs the PUBLISHED element's saved SQL via the validated runner. No ?draft here."""
    p, v = canon(client_slug, p, v)
    need_client(client_slug, "portal")
    _, spec, _ptr = _published(client_slug, p, v)
    el = next((e for e in spec.get("elements", []) if e["id"] == element_id), None)
    if not el or not el.get("sql"):
        raise NOT_FOUND
    cfg = store.get_project_config(client_slug, p)
    try:
        r = run_query(cfg, el["sql"], ttl=el.get("cache_ttl_seconds"))
    except (QueryRejected, QueryFailed):
        # Error text can echo SQL / table names: never send it to clients.
        raise HTTPException(502, "This item couldn't be loaded right now.") from None
    return {"columns": r["columns"], "rows": r["rows"], "truncated": bool(r.get("truncated"))}
