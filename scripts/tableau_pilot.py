"""READ-ONLY pilot recon for three clients (SGU, StorageMart, ASCO).

  AWS_PROFILE=concrete .venv/bin/python scripts/tableau_pilot.py download   # REST download, no extract
  AWS_PROFILE=concrete .venv/bin/python scripts/tableau_pilot.py analyze    # parse XML + BigQuery metadata

Downloads/details go to .local/tableau_inventory/pilot/ (gitignored; never commit workbooks).
Never prints usernames, hostnames, keys or SQL text. BigQuery: get_table/get_dataset only.
"""

import csv
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

PILOT = ROOT / ".local" / "tableau_inventory" / "pilot"
ALL = ROOT / ".local" / "tableau_inventory" / "shortlist_all_workbooks.csv"
CLIENTS = {
    "SGU": r"\bsgu\b|st\.? ?george|saint george",
    "StorageMart": r"storage ?mart|storagemart",
    "ASCO": r"\basco\b",
}
PER_CLIENT = 2
LOD = re.compile(r"\{\s*(FIXED|INCLUDE|EXCLUDE)\b", re.I)
TABLE_CALC = re.compile(
    r"\b(WINDOW_[A-Z]+|RUNNING_[A-Z]+|LOOKUP|INDEX|RANK[A-Z_]*|TOTAL|PREVIOUS_VALUE|FIRST|LAST|SIZE)\s*\(",
    re.I,
)
PARAM = re.compile(r"\[Parameters\]", re.I)
SECRET_ATTR = re.compile(r"(pass|token|secret|key|username|user$|credential|oauth)", re.I)


def pick() -> list[dict]:
    rows = list(csv.DictReader(open(ALL)))
    out = []
    for client, pat in CLIENTS.items():
        rx = re.compile(pat, re.I)
        sel = [
            r
            for r in rows
            if (rx.search(r["client project path"]) or rx.search(r["workbook name"]))
            and r["likely duplicate"] == "no"
        ]
        sel.sort(key=lambda r: -int(r["view count"] or 0))
        for r in sel[:PER_CLIENT]:
            out.append({"client": client, **r})
    return out


def fname(w: dict) -> Path:
    return PILOT / f"{w['client']}_{w['workbook id'][:8]}"


def download() -> None:
    from app.tableau_client import TableauError, session

    PILOT.mkdir(parents=True, exist_ok=True)
    wbs = pick()
    with session(per_call=True) as s:
        for w in wbs:
            try:
                r = s._get(
                    s._site_path(f"/workbooks/{w['workbook id']}/content"),
                    {"includeExtract": "False"},
                )
            except TableauError as e:
                print(f"{w['client']} {w['workbook id'][:8]}: FAILED {str(e)[:200]}")
                continue
            ext = "twbx" if r.content[:2] == b"PK" else "twb"
            p = fname(w).with_suffix("." + ext)
            p.write_bytes(r.content)
            print(f"{w['client']} {w['workbook id'][:8]}: ok .{ext} {len(r.content)} bytes")


def load_xml(w: dict) -> bytes | None:
    for ext in ("twbx", "twb"):
        p = fname(w).with_suffix("." + ext)
        if p.exists():
            if ext == "twb":
                return p.read_bytes()
            z = zipfile.ZipFile(p)
            return z.read(next(n for n in z.namelist() if n.endswith(".twb")))
    return None


def parse(xml: bytes) -> dict:
    root = ET.fromstring(xml)
    dss = [d for d in root.findall("./datasources/datasource") if d.get("name") != "Parameters"]
    params_ds = [
        d for d in root.findall("./datasources/datasource") if d.get("name") == "Parameters"
    ]
    classes, tables, creds = Counter(), set(), False
    custom_sql = 0
    published = 0
    for d in dss:
        if d.find("repository-location") is not None:
            published += 1
        for c in d.iter("connection"):
            if c.get("class"):
                classes[c.get("class")] += 1
            for k, v in c.attrib.items():
                if v and SECRET_ATTR.search(k) and k.lower() not in ("authentication",):
                    creds = True
        # BigQuery named connections: project / dataset defaults
        named, other_conn = {}, set()
        for nc in d.iter("named-connection"):
            c = nc.find("connection")
            if c is None:
                continue
            if c.get("class") == "bigquery":
                named[nc.get("name")] = {
                    "project": c.get("project") or c.get("CATALOG"),
                    "dataset": c.get("schema"),
                }
            else:
                other_conn.add(nc.get("name"))
        for r in d.iter("relation"):
            if r.get("type") == "text":
                custom_sql += 1
            elif r.get("type") == "table" and r.get("table") and r.get("connection") in named:
                parts = re.findall(r"\[([^\]]*)\]", r.get("table"))
                cn = named[r.get("connection")]
                if len(parts) == 2 and "." in parts[0]:  # [project.dataset].[table]
                    p, ds = parts[0].split(".", 1)
                    tables.add((p, ds, parts[1]))
                elif len(parts) == 2:  # [dataset].[table]; project from the connection
                    tables.add((cn["project"] or "?", parts[0], parts[1]))
                elif len(parts) == 3:
                    tables.add(tuple(parts))
                else:
                    tables.add((cn["project"] or "?", cn["dataset"] or "?", parts[-1]))
    marks = Counter()
    used = {
        z.get("name")
        for db in root.findall("./dashboards/dashboard")
        for z in db.iter("zone")
        if z.get("name")
    }
    used_marks, used_n = Counter(), 0
    for ws in root.findall("./worksheets/worksheet"):
        mk = {m.get("class") for m in ws.iter("mark") if m.get("class")}
        for m in mk or {"(none)"}:
            marks[m] += 1
            if ws.get("name") in used:
                used_marks[m] += 1
        used_n += ws.get("name") in used
    calcs = lod = tcalc = param = 0
    formulas: set[str] = set()
    for d in dss:
        for col in d.findall("column"):
            calc = col.find("calculation")
            if calc is None or not calc.get("formula"):
                continue
            f = calc.get("formula")
            formulas.add(re.sub(r"\s+", " ", f))
            calcs += 1
            lod += bool(LOD.search(f))
            tcalc += bool(TABLE_CALC.search(f))
            param += bool(PARAM.search(f))
    uniq = [f for f in formulas]
    return {
        "datasources": len(dss),
        "published_datasources": published,
        "connection_classes": dict(classes),
        "worksheets": len(root.findall("./worksheets/worksheet")),
        "worksheets_on_dashboards": used_n,
        "dashboards": len(root.findall("./dashboards/dashboard")),
        "custom_sql_relations": custom_sql,
        "tables": sorted(tables),
        "mark_types": dict(marks.most_common()),
        "mark_types_on_dashboards": dict(used_marks.most_common()),
        "calculated_fields": calcs,
        "distinct_formulas": len(uniq),
        "distinct_lod": sum(bool(LOD.search(f)) for f in uniq),
        "distinct_table_calc": sum(bool(TABLE_CALC.search(f)) for f in uniq),
        "distinct_using_parameters": sum(bool(PARAM.search(f)) for f in uniq),
        "calcs_lod": lod,
        "calcs_table_calc": tcalc,
        "calcs_using_parameters": param,
        "parameters_defined": sum(
            1 for d in params_ds for c in d.findall("column") if c.get("param-domain-type")
        ),
        "credentials_present": creds,
    }


def bq_check(tables: set[tuple], survey: dict) -> dict:
    from app.gbq import get_client
    from google.api_core.exceptions import Forbidden, NotFound

    c = get_client()
    ds_meta = {(d["project"], d["dataset"]): d for d in survey["datasets"]}
    out = {}
    for proj, ds, tb in sorted(tables):
        key = ".".join((proj, ds, tb))
        rec: dict = {"project": proj, "dataset": ds}
        if "?" in key:
            rec["status"] = "unresolved (project/dataset not in workbook XML)"
            out[key] = rec
            continue
        try:
            t = c.get_table(f"{proj}.{ds}.{tb}")
            rec.update(
                status="visible",
                type=t.table_type,
                rows=t.num_rows,
                bytes=t.num_bytes,
                modified=str(t.modified),
            )
            ids = [
                f.name
                for f in t.schema
                if re.search(r"(client|account|advertiser|brand)", f.name, re.I)
            ]
            rec["id_like_columns"] = ids[:6]
            if t.table_type == "VIEW" and t.view_query:
                refs = Counter(
                    ".".join(m[:2])
                    for m in re.findall(r"`?([\w-]+)\.([\w-]+)\.([\w-]+)`?", t.view_query)
                )
                rec["view_reads_from"] = dict(refs)  # project.dataset only, never the SQL
        except NotFound:
            rec["status"] = "NOT FOUND or not visible to service account"
        except Forbidden:
            rec["status"] = "FORBIDDEN (service account cannot see it)"
        except Exception as e:  # noqa: BLE001
            rec["status"] = f"error {type(e).__name__}"
        try:
            dsm = c.get_dataset(f"{proj}.{ds}")
            rec["dataset_location"], rec["dataset_labels"] = dsm.location, dsm.labels or {}
        except Exception as e:  # noqa: BLE001
            rec["dataset_error"] = type(e).__name__
        d = ds_meta.get((proj, ds))
        rec["dataset_tables_in_survey"] = len(d["tables"]) if d else None
        rec["scope"] = (
            "shared raw dataset in radar-377104" if proj == "radar-377104" else "per-client project"
        )
        out[key] = rec
    return out


def analyze() -> None:
    survey = json.loads((ROOT / ".local" / "bq_survey" / "survey.json").read_text())
    wbs = pick()
    results, all_tables = [], set()
    for w in wbs:
        xml = load_xml(w)
        if xml is None:
            print(f"{w['client']} {w['workbook id'][:8]}: no download")
            continue
        a = parse(xml)
        all_tables |= {tuple(t) for t in a["tables"]}
        results.append(
            {
                "client": w["client"],
                "name": w["workbook name"],
                "id": w["workbook id"],
                "views": w["view count"],
                **a,
            }
        )
    bq = bq_check(all_tables, survey)
    (PILOT / "analysis.json").write_text(
        json.dumps({"workbooks": results, "bigquery": bq}, indent=1)
    )
    ds_count = Counter(ds["dataset"] for ds in survey["datasets"])  # noqa: F841
    for r in results:
        print(f"\n--- {r['client']}: {r['name']} ({r['views']} views) ---")
        print(
            f"  data sources: {r['datasources']} ({r['published_datasources']} published, "
            f"contents not in file); connection classes: {r['connection_classes']}"
        )
        print(
            f"  worksheets {r['worksheets']} ({r['worksheets_on_dashboards']} placed on dashboards), dashboards {r['dashboards']}; "
            f"custom SQL: {'yes' if r['custom_sql_relations'] else 'no'} ({r['custom_sql_relations']})"
        )
        print(f"  marks, all sheets: {r['mark_types']}")
        print(f"  marks, sheets on dashboards: {r['mark_types_on_dashboards']}")
        print(
            f"  calculated fields {r['calculated_fields']} (distinct formulas {r['distinct_formulas']}): "
            f"LOD {r['calcs_lod']} ({r['distinct_lod']} distinct), "
            f"table calcs {r['calcs_table_calc']} ({r['distinct_table_calc']}), "
            f"using parameters {r['calcs_using_parameters']} ({r['distinct_using_parameters']}); "
            f"parameters defined {r['parameters_defined']}"
        )
        if r["credentials_present"]:
            print("  NOTE: credentials are present in the file (not printed)")
        print(f"  BigQuery tables referenced: {len(r['tables'])}")
        for t in r["tables"]:
            print("    " + ".".join(t))
    print("\n=== BIGQUERY METADATA CHECK ===")
    for k, v in bq.items():
        print(
            f"{k}: {v['status']}"
            + (
                f" | {v['type']} rows={v['rows']} bytes={v['bytes']} modified={v['modified'][:19]}"
                if v.get("type")
                else ""
            )
            + f" | {v.get('scope')}"
            + (f" | id-like cols {v['id_like_columns']}" if v.get("id_like_columns") else "")
            + (f" | view reads {v['view_reads_from']}" if v.get("view_reads_from") else "")
        )


if __name__ == "__main__":
    {"download": download, "analyze": analyze}[sys.argv[1]]()
