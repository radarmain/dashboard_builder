"""POST .../views/{v}/chat: builder-only SSE stream from the chat agent."""

import json
import queue
import threading
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import store
from app.auth import Builder
from app.chat_agent import run_chat
from app.chat_tools import ToolContext
from app.config import get_settings
from app.models import ViewSpec
from app.query_runner import run_query
from app.routes_core import canon, need_view
from app.routes_gbq import schema_key

router = APIRouter(prefix="/api")

EXPLORE_ROW_CAP = 50


class ChatTurn(BaseModel):
    role: str
    content: str = Field(max_length=20000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=60)
    current_spec: ViewSpec  # the analyst's LOCAL, possibly unsaved spec


def _context(c: str, p: str, spec: ViewSpec) -> ToolContext:
    cfg = store.get_project_config(c, p)
    schemas: dict[str, dict[str, Any]] = {}
    for fqn in cfg.tables:
        _, dataset, table = fqn.split(".", 2)
        snap = store.s3_get_json(schema_key(c, p, dataset, table))
        if snap:
            schemas[fqn] = snap
    cap = get_settings().chat_max_bytes_billed

    def run(sql: str) -> dict[str, Any]:
        # same validated path as every other query; tighter bytes/rows for exploration
        return run_query(cfg, sql, max_bytes=cap, row_cap=EXPLORE_ROW_CAP)

    return ToolContext(
        tables=list(cfg.tables), schemas=schemas, spec=spec.model_copy(deep=True), run=run
    )


HEARTBEAT_SECONDS = 15.0
THINKING = {"type": "status", "text": "Thinking..."}


def _sse(events: Iterator[dict[str, Any]], heartbeat: float = HEARTBEAT_SECONDS) -> Iterator[str]:
    """Events as SSE lines, plus a `: keep-alive` comment whenever `heartbeat` seconds pass
    without an event (proxies like CloudFront wait ~30s for bytes)."""
    q: queue.Queue[Any] = queue.Queue()
    end = object()

    def work() -> None:
        try:
            for ev in events:
                q.put(ev)
        except Exception as e:  # noqa: BLE001
            q.put({"type": "error", "message": f"{type(e).__name__}: {str(e)[:300]}"})
            q.put({"type": "done"})
        finally:
            q.put(end)

    threading.Thread(target=work, daemon=True).start()
    while True:
        try:
            item = q.get(timeout=heartbeat)
        except queue.Empty:
            yield ": keep-alive\n\n"
            continue
        if item is end:
            return
        yield f"data: {json.dumps(item)}\n\n"


def _events(c: str, p: str, name: str, body: ChatIn) -> Iterator[dict[str, Any]]:
    yield THINKING  # first byte immediately; context loading (S3 reads) happens after
    ctx = _context(c, p, body.current_spec)
    first = True
    for ev in run_chat(ctx, name, body.message, [t.model_dump() for t in body.history]):
        if first and ev == THINKING:  # already sent above
            first = False
            continue
        first = False
        yield ev


@router.post("/clients/{c}/projects/{p}/views/{v}/chat")
def chat(c: str, p: str, v: str, body: ChatIn, _: Builder) -> StreamingResponse:
    p, v = canon(c, p, v)
    item = need_view(c, p, v)
    return StreamingResponse(
        _sse(_events(c, p, item.get("name", v), body)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
