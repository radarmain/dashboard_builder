"""READ-ONLY BigQuery METADATA survey (no table scans). Run:
  AWS_PROFILE=concrete .venv/bin/python scripts/bq_survey.py

Uses only list/get metadata API calls (datasets, tables, schemas, labels, access entries) with the
existing credentials loader, plus DRY-RUN estimates (never executed) for shared multi-client
candidates. Full results go to .local/bq_survey/ (gitignored); only a summary is printed.
Individual user/group emails are never recorded: only entity types and service-account emails.
"""

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.gbq import get_client  # noqa: E402  (existing credentials loader; also loads .env)
from google.cloud import bigquery  # noqa: E402

PROJECTS = ["radar-377104", "st-georges-university-459914"]
OUT = ROOT / ".local" / "bq_survey"

ID_COL = re.compile(
    r"(client|account|workspace|advertiser|customer|brand|tenant|agency|profile)", re.I
)
MARKETING = {
    "date": re.compile(
        r"^(date|day|report_?date|stat_?date|segments?_date|date_start|week|month)$", re.I
    ),
    "platform": re.compile(r"(platform|source|channel|data_?source|network|publisher)", re.I),
    "campaign": re.compile(r"campaign", re.I),
    "spend": re.compile(r"(^|_)(spend|cost|amount_spent)($|_)", re.I),
    "impressions": re.compile(r"impression", re.I),
    "clicks": re.compile(r"click", re.I),
}
IMPROVADO = re.compile(r"improvado", re.I)


def flat(fields: list[bigquery.SchemaField], prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for f in fields:
        out.append((prefix + f.name, f.field_type))
        if f.fields:
            out += flat(f.fields, prefix + f.name + ".")
    return out


def pattern(name: str) -> str:
    p = re.sub(r"\d{8}", "YYYYMMDD", name)
    p = re.sub(r"\d{4}[_-]\d{2}[_-]\d{2}", "YYYY_MM_DD", p)
    return re.sub(r"\d+", "N", p)


def survey_dataset(c: bigquery.Client, project: str, ds_id: str) -> dict:
    ref = f"{project}.{ds_id}"
    d: dict = {"project": project, "dataset": ds_id, "tables": []}
    try:
        ds = c.get_dataset(ref)
        ents = ds.access_entries
        types = Counter(e.entity_type for e in ents)
        sas = sorted(
            {
                e.entity_id
                for e in ents
                if isinstance(e.entity_id, str) and e.entity_id.endswith("gserviceaccount.com")
            }
        )
        imp_types = sorted(
            {
                e.entity_type
                for e in ents
                if isinstance(e.entity_id, str) and IMPROVADO.search(e.entity_id)
            }
        )
        d.update(
            labels=ds.labels or {},
            location=ds.location,
            description=ds.description,
            created=str(ds.created),
            modified=str(ds.modified),
            access_types=dict(types),
            access_service_accounts=sas,
            access_improvado_types=imp_types,
        )
    except Exception as e:  # noqa: BLE001
        d["error"] = f"get_dataset: {type(e).__name__}"
    try:
        listed = list(c.list_tables(ref))
    except Exception as e:  # noqa: BLE001
        d["error"] = d.get("error", "") + f" list_tables: {type(e).__name__}"
        return d

    def one(t: bigquery.table.TableListItem) -> dict:
        try:
            tb = c.get_table(t.reference)
        except Exception as e:  # noqa: BLE001
            return {"table": t.table_id, "type": t.table_type, "error": type(e).__name__}
        cols = flat(tb.schema)
        return {
            "table": tb.table_id,
            "type": tb.table_type,
            "rows": tb.num_rows,
            "bytes": tb.num_bytes,
            "created": str(tb.created),
            "modified": str(tb.modified),
            "labels": tb.labels or {},
            "description": tb.description,
            "columns": cols,
            "partition": getattr(tb.time_partitioning, "field", None)
            if tb.time_partitioning
            else None,
            "clustering": tb.clustering_fields,
        }

    with ThreadPoolExecutor(12) as ex:
        d["tables"] = list(ex.map(one, listed))
    return d


def classify(t: dict, ds: dict) -> dict | None:
    cols = [n for n, _ in t.get("columns", [])]
    leaf = [n.split(".")[-1] for n in cols]
    hits = {k: any(rx.search(n) for n in leaf) for k, rx in MARKETING.items()}
    if hits["date"]:
        hits["date"] = any(MARKETING["date"].match(n) for n in leaf)
    score = sum(hits.values())
    text = " ".join(
        [
            t["table"],
            t.get("description") or "",
            json.dumps(t.get("labels", {})),
            json.dumps(ds.get("labels", {})),
            ds.get("description") or "",
        ]
    )
    imp = bool(IMPROVADO.search(text))
    if score < 3 and not imp:
        return None
    return {
        "ref": f"{ds['dataset']}.{t['table']}",
        "project": ds["project"],
        "score": score,
        "marketing_cols": [k for k, v in hits.items() if v],
        "improvado_mention": imp,
        "rows": t.get("rows"),
        "modified": t.get("modified"),
        "type": t["type"],
        "id_cols": [n for n in cols if ID_COL.search(n.split(".")[-1])],
        "sig": hashlib.md5(json.dumps(sorted(t["columns"])).encode()).hexdigest()[:8],
    }


def main() -> None:
    c = get_client()
    OUT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for p in PROJECTS:
        try:
            ids = sorted(d.dataset_id for d in c.list_datasets(p))
        except Exception as e:  # noqa: BLE001
            print(f"{p}: cannot list datasets ({type(e).__name__}: {str(e)[:120]})")
            continue
        print(f"{p}: {len(ids)} datasets; reading table metadata...", flush=True)
        for i, ds_id in enumerate(ids, 1):
            results.append(survey_dataset(c, p, ds_id))
            if i % 10 == 0:
                print(f"  {i}/{len(ids)}", flush=True)

    cands = []
    for ds in results:
        for t in ds["tables"]:
            if k := classify(t, ds):
                cands.append(k)
    # grouping: same schema signature across datasets vs many tables in one dataset vs single table
    by_sig = defaultdict(list)
    for k in cands:
        by_sig[k["sig"]].append(k)
    for k in cands:
        grp = by_sig[k["sig"]]
        dsets = {g["ref"].split(".")[0] for g in grp}
        k["same_schema_tables"] = len(grp)
        k["same_schema_datasets"] = len(dsets)
        if len(grp) == 1 and k["id_cols"]:
            k["shape"] = "single table with identifier column (possible shared multi-client table)"
        elif len(dsets) > 1:
            k["shape"] = "same schema repeated across datasets (per-dataset layout?)"
        elif len(grp) > 1:
            k["shape"] = "same schema repeated across tables in one dataset (per-table layout?)"
        else:
            k["shape"] = "single table, no identifier column"

    # dry-run estimates (NOT executed) for shared candidates: SELECT DISTINCT <id col>
    shared = sorted(
        (k for k in cands if k["shape"].startswith("single") and k["type"] == "TABLE"),
        key=lambda k: -(k["rows"] or 0),
    )
    for k in shared[:20]:
        k["dry_run"] = {}
        for col in k["id_cols"][:3]:
            if "." in col:
                continue
            try:
                job = c.query(
                    f"SELECT DISTINCT `{col}` FROM `{k['project']}.{k['ref']}`",
                    job_config=bigquery.QueryJobConfig(
                        dry_run=True,
                        use_query_cache=False,
                        labels={"app": "rdb", "purpose": "survey"},
                    ),
                )
                k["dry_run"][col] = job.total_bytes_processed
            except Exception as e:  # noqa: BLE001
                k["dry_run"][col] = f"error {type(e).__name__}"

    (OUT / "survey.json").write_text(
        json.dumps({"datasets": results, "candidates": cands}, indent=1, default=str)
    )

    # ---------- summary ----------
    print("\n=== BIGQUERY METADATA SURVEY (no data scanned) ===")
    for p in PROJECTS:
        dss = [d for d in results if d["project"] == p]
        ntab = sum(len(d["tables"]) for d in dss)
        print(f"\n{p}: {len(dss)} datasets, {ntab} tables/views")
        for d in sorted(dss, key=lambda d: -len(d["tables"]))[:40]:
            print(
                f"  {d['dataset'][:42]:42} tables={len(d['tables']):4} labels={d.get('labels') or '-'}"
                f" access={d.get('access_types')} SAs={len(d.get('access_service_accounts', []))}"
                + (f" ERR {d['error']}" if d.get("error") else "")
            )
        if len(dss) > 40:
            print(f"  ... {len(dss) - 40} more datasets (see .local/bq_survey/survey.json)")
    sas = Counter(sa for d in results for sa in d.get("access_service_accounts", []))
    print("\nService accounts on dataset access lists (entries = number of datasets):")
    for sa, n in sas.most_common(15):
        print(f"  {sa}: {n}")
    imp_ds = [d["dataset"] for d in results if d.get("access_improvado_types")]
    imp_lab = [
        f"{d['dataset']}.{t['table']}"
        for d in results
        for t in d["tables"]
        if IMPROVADO.search(json.dumps(t.get("labels", {})) + (t.get("description") or ""))
    ]
    imp_dlab = [d["dataset"] for d in results if IMPROVADO.search(json.dumps(d.get("labels", {})))]
    print(
        f"\nImprovado signals: datasets with 'improvado' in access entries: {len(imp_ds)} "
        f"{imp_ds[:5]}; dataset labels: {len(imp_dlab)}; table labels/descriptions: {len(imp_lab)}"
    )
    tabs = [t for d in results for t in d["tables"]]
    names = [t["table"] for t in tabs]
    print(
        f"\nTable naming patterns (digits->N, dates->YYYYMMDD), top 12 of {len(set(map(pattern, names)))}:"
    )
    for pat, n in Counter(map(pattern, names)).most_common(12):
        print(f"  {n:5}  {pat}")
    print("Most common first tokens:", Counter(n.split("_")[0] for n in names).most_common(10))
    print(
        f"\nMarketing-shaped candidate tables: {len(cands)} "
        f"(Improvado mention in name/labels/description: {sum(k['improvado_mention'] for k in cands)})"
    )
    print("  by shape:", dict(Counter(k["shape"] for k in cands)))
    print(
        "  candidates by dataset (top 15):",
        Counter(k["ref"].split(".")[0] for k in cands).most_common(15),
    )
    print("  top 15 candidates by rows (ref | rows | modified | identifier columns):")
    for k in sorted(cands, key=lambda k: -(k["rows"] or 0))[:15]:
        print(f"   {k['ref'][:60]:60} {k['rows']} {str(k['modified'])[:10]} {k['id_cols'][:4]}")
    print("\nDry-run estimates for SELECT DISTINCT <id col> on shared candidates (NOT executed):")
    for k in shared[:20]:
        if k.get("dry_run"):
            print(f"  {k['ref'][:60]:60} rows={k['rows']} {k['dry_run']}")
    print(f"\nFull results: {OUT / 'survey.json'}")


if __name__ == "__main__":
    main()
