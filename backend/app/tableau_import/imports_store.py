"""S3 layout for one import run (private to the project, never served to portal users):

clients/{c}/projects/{p}/imports/{import_id}/plan.json            sanitized plan (no credentials)
clients/{c}/projects/{p}/imports/{import_id}/report.json          one entry per sheet run
clients/{c}/projects/{p}/imports/{import_id}/reference/index.json {sheet: view_id | null}
clients/{c}/projects/{p}/imports/{import_id}/reference/{n}.csv    Tableau view CSV (REST, once per import)
"""

import re
from typing import Any

from botocore.exceptions import ClientError

from app import store
from app import tableau_client as tc
from app.aws import s3
from app.config import get_settings

IMPORT_ID = re.compile(r"^[0-9a-f]{32}$")


def prefix(c: str, p: str, import_id: str) -> str:
    return f"{store.project_prefix(c, p)}imports/{import_id}/"


def put_plan(c: str, p: str, import_id: str, plan: dict[str, Any], link: dict[str, str]) -> None:
    store.s3_put_json(prefix(c, p, import_id) + "plan.json", {"link": link, "plan": plan})


def get_plan(c: str, p: str, import_id: str) -> dict[str, Any] | None:
    return store.s3_get_json(prefix(c, p, import_id) + "plan.json")


def get_report(c: str, p: str, import_id: str) -> dict[str, Any] | None:
    return store.s3_get_json(prefix(c, p, import_id) + "report.json")


def add_report_entry(
    c: str, p: str, import_id: str, link: dict[str, str], entry: dict[str, Any]
) -> dict[str, Any]:
    """Sheet runs are sequential (one request per sheet), so read-modify-write is enough."""
    rep = get_report(c, p, import_id) or {"import_id": import_id, "workbook": link, "sheets": {}}
    rep["sheets"][entry["sheet"]] = entry
    rep["updated_at"] = store.now()
    store.s3_put_json(prefix(c, p, import_id) + "report.json", rep)
    return rep


def _get_text(key: str) -> str | None:
    try:
        return s3().get_object(Bucket=get_settings().data_bucket, Key=key)["Body"].read().decode()
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise


def _put_text(key: str, text: str) -> None:
    s3().put_object(
        Bucket=get_settings().data_bucket,
        Key=key,
        Body=text.encode(),
        ContentType="text/csv",
    )


def load_references(
    c: str, p: str, import_id: str, workbook_id: str, sheet_names: list[str]
) -> tuple[dict[str, str], list[str]]:
    """{sheet name: CSV text} for worksheets that are also REST views. Tableau is called once per
    import (view list + one CSV per matching worksheet); later calls read S3. Read-only."""
    base = prefix(c, p, import_id) + "reference/"
    notes: list[str] = []
    index = store.s3_get_json(base + "index.json")
    if index is None:
        index = {}
        try:
            with tc.session(per_call=True) as s:
                views = {v["name"]: v["id"] for v in s.workbook_views(workbook_id)}
                for n, name in enumerate(sheet_names):
                    vid = views.get(name)
                    index[name] = n if vid else None
                    if vid:
                        try:
                            _put_text(f"{base}{n}.csv", s.view_csv(vid))
                        except tc.TableauError as e:
                            index[name] = None
                            notes.append(f"reference data for '{name}' unavailable: {str(e)[:120]}")
        except tc.TableauError as e:
            return {}, [f"Tableau reference data unavailable: {str(e)[:160]}"]
        store.s3_put_json(base + "index.json", index)
    out: dict[str, str] = {}
    for name, n in index.items():
        if n is not None and (txt := _get_text(f"{base}{n}.csv")) is not None:
            out[name] = txt
    return out, notes
