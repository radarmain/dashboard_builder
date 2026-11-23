"""validate -> cache check -> dry run (byte cap) -> run (cap, timeout, row cap) -> cache write.

Authorization is the caller's job and MUST happen before run_query (so the cache is never read
for an unauthorized caller).
"""

import base64
import hashlib
import json
import re
from datetime import UTC, date, datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Any

from app import store
from app.config import get_settings
from app.gbq import get_client, job_config
from app.models import ProjectConfig
from app.sql_validation import SqlValidationError, validate_sql


class QueryRejected(Exception):
    """Validation failure or byte cap exceeded -> HTTP 400."""


class QueryFailed(Exception):
    """BigQuery error -> HTTP 502. Message is safe (no SQL echo of data, no creds)."""


def json_safe(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, Decimal):
        return str(v)  # preserve precision
    if isinstance(v, (datetime, date, dtime)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return base64.b64encode(bytes(v)).decode()
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    return str(v)


def cache_key(sql: str, bq_project: str, params: dict[str, Any] | None = None) -> str:
    h = hashlib.sha256(
        json.dumps([sql, bq_project, params or {}], sort_keys=True).encode()
    ).hexdigest()
    return f"cache/{h}.json"


def _read_cache(key: str, ttl: int) -> dict[str, Any] | None:
    entry = store.s3_get_json(key)
    if not entry:
        return None
    age = (datetime.now(UTC) - datetime.fromisoformat(entry["created_at"])).total_seconds()
    return entry["result"] if age <= ttl else None


def run_query(
    cfg: ProjectConfig,
    sql: str,
    *,
    refresh: bool = False,
    ttl: int | None = None,
    params: dict[str, Any] | None = None,
    max_bytes: int | None = None,
    row_cap: int | None = None,
) -> dict[str, Any]:
    """max_bytes / row_cap only ever tighten the project's limits (used by chat exploration)."""
    s = get_settings()
    if not cfg.bq_project or not cfg.tables:
        raise QueryRejected("project has no connected BigQuery tables")
    try:
        v = validate_sql(sql, cfg.tables)
    except SqlValidationError as e:
        raise QueryRejected(str(e)) from None

    ttl = s.default_cache_ttl_seconds if ttl is None else ttl
    if row_cap is not None:
        params = {**(params or {}), "row_cap": row_cap}
    key = cache_key(v.sql, cfg.bq_project, params)
    if not refresh and ttl > 0:
        hit = _read_cache(key, ttl)
        if hit is not None:
            return {**hit, "cache_hit": True}

    cap = cfg.max_bytes_billed or s.default_max_bytes_billed
    if max_bytes is not None:
        cap = min(cap, max_bytes)
    rows_limit = min(s.query_row_cap, row_cap) if row_cap is not None else s.query_row_cap
    client = get_client()
    try:
        dry = client.query(v.sql, job_config=job_config(dry_run=True, use_query_cache=False))
        est = int(dry.total_bytes_processed or 0)
        if est > cap:
            raise QueryRejected(f"query would process {est} bytes, over the cap of {cap}")
        job = client.query(
            v.sql,
            job_config=job_config(
                maximum_bytes_billed=cap, job_timeout_ms=s.query_timeout_seconds * 1000
            ),
        )
        it = job.result(timeout=s.query_timeout_seconds, max_results=rows_limit + 1)
        rows = [json_safe(dict(r.items())) for r in it]
        columns = [f.name for f in it.schema]
    except QueryRejected:
        raise
    except Exception as e:  # noqa: BLE001
        hint = (
            " The query is slow; for a view source, use a materialized table."
            if "Timeout" in type(e).__name__ or "timed out" in str(e).lower()
            else ""
        )
        raise QueryFailed(f"{type(e).__name__}: {str(e)[:400]}{hint}") from None

    truncated = len(rows) > rows_limit
    rows = rows[:rows_limit]
    result = {
        "columns": columns,
        "rows": [[r.get(c) for c in columns] for r in rows],
        "truncated": truncated,
        "bytes_processed": int(job.total_bytes_processed or est),
        "cache_hit": False,
    }
    store.s3_put_json(
        key, {"created_at": datetime.now(UTC).isoformat(), "ttl": ttl, "result": result}
    )
    return result


# ---------- dry run (zero bytes scanned) ----------
_BQ_NAME = re.compile(r"`([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)`")


def _view_info(client: Any, fqn: str) -> tuple[bool, list[str]]:
    """(is_view, base tables named in the view's SQL). Metadata call only; failures -> unknown."""
    try:
        t = client.get_table(fqn)
    except Exception:  # noqa: BLE001
        return False, []
    if getattr(t, "table_type", "") != "VIEW":
        return False, []
    base = [
        b for b in dict.fromkeys(_BQ_NAME.findall(getattr(t, "view_query", "") or "")) if b != fqn
    ]
    return True, base


def dry_run_query(cfg: ProjectConfig, sql: str, *, client: Any = None) -> dict[str, Any]:
    """Validate (same allowlist as run_query), then BigQuery DRY-RUN: nothing executes, 0 bytes.

    Returns the run_query result shape with no rows, plus `schema` [{name, type}], `bytes_processed`
    (the estimate), `source_is_view` and `base_tables`. Errors -> QueryRejected / QueryFailed."""
    if not cfg.bq_project or not cfg.tables:
        raise QueryRejected("project has no connected BigQuery tables")
    try:
        v = validate_sql(sql, cfg.tables)
    except SqlValidationError as e:
        raise QueryRejected(str(e)) from None
    client = client or get_client()
    try:
        job = client.query(v.sql, job_config=job_config(dry_run=True, use_query_cache=False))
        schema = [{"name": f.name, "type": f.field_type} for f in (job.schema or [])]
        est = int(job.total_bytes_processed or 0)
    except Exception as e:  # noqa: BLE001
        raise QueryFailed(f"{type(e).__name__}: {str(e)[:500]}") from None
    is_view, base = False, []
    for fqn in v.tables:
        view, b = _view_info(client, fqn)
        is_view = is_view or view
        base += [x for x in b if x not in base]
    return {
        "columns": [c["name"] for c in schema],
        "schema": schema,
        "rows": [],
        "truncated": False,
        "bytes_processed": est,
        "cache_hit": False,
        "dry_run": True,
        "source_is_view": is_view,
        "base_tables": base[:10],
    }


def needs_confirmation(est: dict[str, Any], threshold: int) -> dict[str, Any] | None:
    """Gate for imported elements: views and big scans need an explicit OK."""
    reasons = []
    if est.get("source_is_view"):
        reasons.append(
            "the source is a view (every run re-executes its query over the base tables)"
        )
    if int(est.get("bytes_processed") or 0) > threshold:
        reasons.append(f"it would scan about {int(est['bytes_processed']) / 1e9:.1f} GB")
    if not reasons:
        return None
    return {
        "needs_confirmation": True,
        "reasons": reasons,
        "estimated_bytes": int(est.get("bytes_processed") or 0),
        "source_is_view": bool(est.get("source_is_view")),
        "base_tables": est.get("base_tables", []),
        "suggestion": "Consider a materialized table for the base data; views are slow and costly.",
    }
