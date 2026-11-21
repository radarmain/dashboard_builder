"""BigQuery client + metadata helpers. Read-only; labels every job; never logs row data."""

import os
from functools import lru_cache
from typing import Any

from google.cloud import bigquery

from app.aws import session
from app.config import get_settings
from app.gbq_credentials import load_credentials

LABELS = {"app": "rdb"}


@lru_cache
def get_client() -> bigquery.Client:
    creds = load_credentials(session=session())
    billing = (
        get_settings().gbq_billing_project
        or os.environ.get("RDB_GBQ_BILLING_PROJECT")
        or creds.project_id
    )
    return bigquery.Client(project=billing, credentials=creds)


def job_config(**kw: Any) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(labels=LABELS, use_legacy_sql=False, **kw)


def list_projects() -> list[str]:
    return sorted(p.project_id for p in get_client().list_projects())


def list_datasets(project: str) -> list[str]:
    return sorted(d.dataset_id for d in get_client().list_datasets(project))


def list_tables(project: str, dataset: str) -> list[dict[str, str]]:
    return sorted(
        (
            {"table_id": t.table_id, "type": t.table_type or ""}
            for t in get_client().list_tables(f"{project}.{dataset}")
        ),
        key=lambda t: t["table_id"],
    )


def _col(f: bigquery.SchemaField) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": f.name,
        "type": f.field_type,
        "mode": f.mode,
        "bq_description": f.description or "",
    }
    if f.fields:
        d["fields"] = [_col(c) for c in f.fields]
    return d


def table_snapshot(fqn: str) -> dict[str, Any]:
    """Schema snapshot from the tables.get metadata API (no query, no cost)."""
    t = get_client().get_table(fqn)  # raises google.api_core.exceptions.NotFound if missing
    return {
        "table": fqn,
        "table_type": t.table_type,
        "description": t.description or "",
        "num_rows": t.num_rows,
        "num_bytes": t.num_bytes,
        "columns": [_col(f) for f in t.schema],
        "descriptions": {},  # analyst-written: {column: description}
    }
