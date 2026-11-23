"""Tableau (read-only, builder only) + system alerts (admin only). Never returns tokens/secrets."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app import alerts
from app import tableau_client as tc
from app.auth import Admin, Builder
from app.tableau_config import load_config, redact

router = APIRouter(prefix="/api")


def _run(fn: Any) -> Any:
    try:
        return fn()
    except tc.TableauNotConfigured:
        raise HTTPException(503, "Tableau is not configured") from None
    except tc.TableauBusy as e:
        raise HTTPException(503, str(e)) from None
    except tc.TableauError as e:
        raise HTTPException(502, redact(str(e))) from None


def _days_left() -> int | None:
    cfg = load_config()
    if not cfg or not cfg.token_expires:
        return None
    return (cfg.token_expires - datetime.now(UTC).date()).days


@router.get("/alerts")
def get_alerts(_: Admin) -> list[dict[str, Any]]:
    return alerts.list_alerts()


@router.get("/tableau/status")
def status(_: Builder) -> dict[str, Any]:
    cfg = load_config()
    if cfg is None:
        return {"connected": False, "configured": False}

    def go() -> dict[str, Any]:
        with tc.session() as s:
            return {**s.server_info(), "site": s.site_info()["content_url"]}

    base: dict[str, Any] = {
        "configured": True,
        "server": cfg.host,
        "site": cfg.site,
        "days_until_token_expiry": _days_left(),
    }
    try:
        info = tc.cached("status", go)
        return {
            **base,
            "connected": True,
            "version": info.get("product_version"),
            "rest_api_version": info.get("rest_api_version"),
        }
    except tc.TableauError as e:
        return {**base, "connected": False, "error": redact(str(e))}


@router.get("/tableau/projects")
def projects(_: Builder) -> list[dict[str, Any]]:
    def go() -> list[dict[str, Any]]:
        with tc.session() as s:
            return s.projects()

    return _run(lambda: tc.cached("projects", go))


@router.get("/tableau/workbooks")
def workbooks(_: Builder, project: str | None = Query(None)) -> list[dict[str, Any]]:
    def go() -> list[dict[str, Any]]:
        with tc.session() as s:
            return s.workbooks(project)

    return _run(lambda: tc.cached(f"workbooks:{project}", go))


@router.get("/tableau/workbooks/{workbook_id}")
def workbook(workbook_id: str, _: Builder) -> dict[str, Any]:
    def go() -> dict[str, Any]:
        with tc.session() as s:
            return {**s.workbook_detail(workbook_id), "views": s.workbook_views(workbook_id)}

    return _run(lambda: tc.cached(f"wb:{workbook_id}", go))
