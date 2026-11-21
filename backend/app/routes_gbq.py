"""BigQuery browsing, project settings/schemas, query preview, and the viewer data route."""

import re
from typing import Any

from fastapi import APIRouter, HTTPException

from app import gbq, store
from app.auth import Builder, ClientAccess
from app.config import get_settings
from app.models import ProjectConfig, ProjectSettingsIn, QueryIn
from app.query_runner import QueryFailed, QueryRejected, run_query
from app.routes_core import load_published, need_project, need_view
from app.sql_validation import SqlValidationError, validate_allowlist_entry

router = APIRouter(prefix="/api")

IDENT = re.compile(r"^[A-Za-z0-9_\-]+$")


def _gbq_err(e: Exception) -> HTTPException:
    # Surface the exact BigQuery error (permissions etc.); these messages contain no credentials.
    return HTTPException(502, f"{type(e).__name__}: {str(e)[:600]}")


def _ident(*parts: str) -> None:
    if not all(IDENT.match(x) for x in parts):
        raise HTTPException(400, "invalid identifier")


# ---------- BigQuery browsing (metadata API only) ----------
@router.get("/gbq/projects")
def gbq_projects(_: Builder) -> list[str]:
    try:
        return gbq.list_projects()
    except Exception as e:  # noqa: BLE001
        raise _gbq_err(e) from None


@router.get("/gbq/projects/{bq}/datasets")
def gbq_datasets(bq: str, _: Builder) -> list[str]:
    _ident(bq)
    try:
        return gbq.list_datasets(bq)
    except Exception as e:  # noqa: BLE001
        raise _gbq_err(e) from None


@router.get("/gbq/projects/{bq}/datasets/{d}/tables")
def gbq_tables(bq: str, d: str, _: Builder) -> list[dict[str, str]]:
    _ident(bq, d)
    try:
        return gbq.list_tables(bq, d)
    except Exception as e:  # noqa: BLE001
        raise _gbq_err(e) from None


# ---------- project settings + schema snapshots ----------
def schema_key(c: str, p: str, dataset: str, table: str) -> str:
    return f"{store.project_prefix(c, p)}schemas/{dataset}.{table}.json"


@router.put("/clients/{c}/projects/{p}/settings")
def put_settings(c: str, p: str, body: ProjectSettingsIn, _: Builder) -> dict[str, Any]:
    need_project(c, p)
    _ident(body.bq_project)
    if not body.tables:
        raise HTTPException(400, "select at least one table")
    tables: list[str] = []
    for entry in dict.fromkeys(body.tables):  # de-dupe, keep order
        try:
            validate_allowlist_entry(entry)
        except SqlValidationError as e:
            raise HTTPException(400, str(e)) from None
        proj, dataset, table = entry.split(".")
        _ident(proj, dataset, table)
        if proj != body.bq_project:
            raise HTTPException(400, f"{entry} is not in project {body.bq_project}")
        tables.append(entry)

    snapshots: dict[str, dict[str, Any]] = {}
    for entry in tables:  # verify existence + snapshot BEFORE saving anything
        try:
            snapshots[entry] = gbq.table_snapshot(entry)
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "NotFound":
                raise HTTPException(400, f"table not found: {entry}") from None
            raise _gbq_err(e) from None

    for entry, snap in snapshots.items():
        _, dataset, table = entry.split(".")
        key = schema_key(c, p, dataset, table)
        old = store.s3_get_json(key)
        if old:  # keep analyst descriptions for columns that still exist
            cols = {col["name"] for col in snap["columns"]}
            snap["descriptions"] = {
                k: v for k, v in old.get("descriptions", {}).items() if k in cols
            }
        store.s3_put_json(key, snap)

    prev = store.get_project_config(c, p)
    cfg = ProjectConfig(
        bq_project=body.bq_project,
        tables=tables,
        max_bytes_billed=body.max_bytes_billed if body.max_bytes_billed else prev.max_bytes_billed,
    )
    store.put_project_config(c, p, cfg)
    return cfg.model_dump()


@router.get("/clients/{c}/projects/{p}/settings")
def get_settings_route(c: str, p: str, _: Builder) -> dict[str, Any]:
    need_project(c, p)
    cfg = store.get_project_config(c, p).model_dump()
    cfg["default_max_bytes_billed"] = get_settings().default_max_bytes_billed
    return cfg


def _snapshot(c: str, p: str, dt: str) -> tuple[str, dict[str, Any]]:
    need_project(c, p)
    dataset, _, table = dt.partition(".")
    _ident(dataset, table or "x")
    key = schema_key(c, p, dataset, table)
    snap = store.s3_get_json(key)
    if not snap:
        raise HTTPException(404, "schema snapshot not found")
    return key, snap


@router.get("/clients/{c}/projects/{p}/schemas/{dt}")
def get_schema(c: str, p: str, dt: str, _: Builder) -> dict[str, Any]:
    return _snapshot(c, p, dt)[1]


@router.put("/clients/{c}/projects/{p}/schemas/{dt}/descriptions")
def put_descriptions(c: str, p: str, dt: str, body: dict[str, str], _: Builder) -> dict[str, Any]:
    key, snap = _snapshot(c, p, dt)
    cols = {col["name"] for col in snap["columns"]}
    unknown = sorted(set(body) - cols)
    if unknown:
        raise HTTPException(400, f"unknown columns: {unknown}")
    snap["descriptions"] = {k: v.strip() for k, v in body.items() if v.strip()}
    store.s3_put_json(key, snap)
    return snap


# ---------- queries ----------
def _run(c: str, p: str, sql: str, *, refresh: bool, ttl: int | None) -> dict[str, Any]:
    cfg = store.get_project_config(c, p)
    try:
        return run_query(cfg, sql, refresh=refresh, ttl=ttl)
    except QueryRejected as e:
        raise HTTPException(400, str(e)) from None
    except QueryFailed as e:
        raise HTTPException(502, str(e)) from None


@router.post("/clients/{c}/projects/{p}/query")
def query(c: str, p: str, body: QueryIn, _: Builder) -> dict[str, Any]:
    need_project(c, p)
    return _run(c, p, body.sql, refresh=body.refresh, ttl=None)


@router.get("/clients/{client_slug}/projects/{p}/views/{v}/data/{element_id}")
def element_data(
    client_slug: str, p: str, v: str, element_id: str, user: ClientAccess, draft: bool = False
) -> dict[str, Any]:
    c = client_slug
    # ClientAccess (authorization) has already run before anything below touches the cache.
    if draft:
        if not user.is_builder:
            raise HTTPException(403, "builder access required for draft")
        item = need_view(c, p, v)
        n = item.get("draft_revision")
        spec = store.read_revision(c, p, v, n) if n is not None else None
        if not spec:
            raise HTTPException(404, "no draft")
    else:
        spec = load_published(c, p, v)
    el = next((e for e in spec.get("elements", []) if e["id"] == element_id), None)
    if not el or not el.get("sql"):
        raise HTTPException(404, "element not found")
    # SQL comes only from the saved spec, never from the request.
    return _run(c, p, el["sql"], refresh=False, ttl=el.get("cache_ttl_seconds"))
