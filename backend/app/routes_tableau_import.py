"""Tableau import (builder only, read-only against Tableau and BigQuery). Nothing is published."""

import re
import uuid
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import store
from app import tableau_client as tc
from app.auth import Builder
from app.models import TableauLink, ViewSpec
from app.routes_chat import _sse
from app.routes_core import canon, need_project
from app.tableau_config import redact
from app.tableau_import import imports_store as ist
from app.tableau_import.agent import run_sheet, verify_sheet
from app.tableau_import.parser import ParseError, plan_from_download
from app.users import audit

router = APIRouter(prefix="/api")
BASE = "/clients/{c}/projects/{p}"


@router.put(BASE + "/tableau-link")
def put_link(c: str, p: str, body: TableauLink, user: Builder) -> dict[str, Any]:
    p = canon(c, p)[0]
    need_project(c, p, "write")
    cfg = store.get_project_config(c, p)
    cfg.tableau_link = body
    store.put_project_config(c, p, cfg)
    audit(user.email or user.sub, "tableau_link_set", body.workbook_id, c)
    return body.model_dump()


@router.get(BASE + "/tableau-link")
def get_link(c: str, p: str, _: Builder) -> dict[str, Any] | None:
    p = canon(c, p)[0]
    need_project(c, p)
    link = store.get_project_config(c, p).tableau_link
    return link.model_dump() if link else None


def _linked(c: str, p: str) -> TableauLink:
    need_project(c, p, "write")  # plan / run / verify: archived projects are read-only
    link = store.get_project_config(c, p).tableau_link
    if not link:
        raise HTTPException(400, "no Tableau workbook is linked to this project")
    return link


@router.get(BASE + "/tableau-import/plan")
def plan(c: str, p: str, user: Builder) -> dict[str, Any]:
    """Downloads the workbook (temp dir, deleted), returns the sanitized plan. No data."""
    p = canon(c, p)[0]
    link = _linked(c, p)
    try:
        with tc.session() as s:
            data = s.download_workbook(link.workbook_id)
        parsed = plan_from_download(data)
    except tc.TableauNotConfigured:
        raise HTTPException(503, "Tableau is not configured") from None
    except tc.TableauBusy as e:
        raise HTTPException(503, str(e)) from None
    except tc.TableauError as e:
        raise HTTPException(502, redact(str(e))) from None
    except ParseError as e:
        raise HTTPException(422, str(e)) from None
    finally:
        data = b""
    import_id = uuid.uuid4().hex
    ist.put_plan(c, p, import_id, parsed, link.model_dump())
    audit(user.email or user.sub, "tableau_import_plan", link.workbook_id, c)
    return {"import_id": import_id, "workbook": link.model_dump(), "plan": parsed}


class RunIn(BaseModel):
    import_id: str
    current_spec: ViewSpec  # the analyst's LOCAL, possibly unsaved spec
    view_name: str = ""


@router.post(BASE + "/tableau-import/sheets/{sheet_name}/run")
def run(c: str, p: str, sheet_name: str, body: RunIn, user: Builder) -> StreamingResponse:
    p = canon(c, p)[0]
    link = _linked(c, p)
    if not ist.IMPORT_ID.match(body.import_id):
        raise HTTPException(400, "invalid import id")
    audit(
        user.email or user.sub, "tableau_import_sheet", f"{link.workbook_id}:{sheet_name}"[:200], c
    )

    def events() -> Iterator[dict[str, Any]]:
        yield {"type": "status", "text": "Thinking..."}
        yield from run_sheet(
            c, p, body.import_id, sheet_name, body.current_spec, view_name=body.view_name
        )

    return StreamingResponse(
        _sse(events()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class VerifyIn(BaseModel):
    import_id: str
    current_spec: ViewSpec
    confirm: bool = False  # explicit OK when the source is a view or the estimate is large


@router.post(BASE + "/tableau-import/sheets/{sheet_name}/verify")
def verify(c: str, p: str, sheet_name: str, body: VerifyIn, user: Builder) -> StreamingResponse:
    """VERIFY mode (opt-in): one query within the byte budget, one comparison. No LLM."""
    p = canon(c, p)[0]
    link = _linked(c, p)
    if not ist.IMPORT_ID.match(body.import_id):
        raise HTTPException(400, "invalid import id")
    who = user.email or user.sub
    audit(who, "tableau_import_verify", f"{link.workbook_id}:{sheet_name}"[:200], c)
    if body.confirm:
        audit(who, "tableau_import_verify_confirmed", f"{link.workbook_id}:{sheet_name}"[:200], c)
    return StreamingResponse(
        _sse(
            verify_sheet(c, p, body.import_id, sheet_name, body.current_spec, confirm=body.confirm)
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(BASE + "/tableau-import/{import_id}/report")
def report(c: str, p: str, import_id: str, _: Builder) -> dict[str, Any]:
    p = canon(c, p)[0]
    need_project(c, p)
    if not re.fullmatch(r"[0-9a-f]{32}", import_id):
        raise HTTPException(400, "invalid import id")
    rep = ist.get_report(c, p, import_id)
    if not rep:
        raise HTTPException(404, "no report yet")
    return rep
