"""READ-ONLY Tableau inventory. Run: AWS_PROFILE=concrete .venv/bin/python scripts/tableau_inventory.py

Writes a detailed dump to .local/tableau_inventory/ (gitignored: client/field names) and prints a
summary only. Never prints data values. Options: --limit N (only first N workbooks), --workers N.
"""

import argparse
import csv
import io
import json
import re
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.tableau_client import TableauError, session  # noqa: E402
from app.tableau_config import load_config  # noqa: E402

OUT = ROOT / ".local" / "tableau_inventory"
LOD = re.compile(r"\{\s*(FIXED|INCLUDE|EXCLUDE)\b", re.I)
TABLE_CALC = re.compile(
    r"\b(WINDOW_[A-Z]+|RUNNING_[A-Z]+|LOOKUP|INDEX|RANK[A-Z_]*|TOTAL)\s*\(", re.I
)
PARAM = re.compile(r"\[Parameters\]", re.I)
FILE_TYPES = {"excel-direct", "textscan", "csv", "text", "google-sheets", "googlesheets", "pdf"}


def ds_category(ds: dict) -> str:
    types = {t["connection_type"] for t in ds["tables"] if t["connection_type"]}
    types |= {d["connection_type"] for d in ds["databases"] if d["connection_type"]}
    types |= set(ds.get("connection_types") or [])  # REST connections (not permission-hidden)
    types = {x.lower() for x in types}
    if "bigquery" in types:
        kind = "bigquery"
    elif not types:
        kind = "hidden-or-no-upstream"
    elif types <= FILE_TYPES:
        kind = "file"
    else:
        kind = "other-db"
    return f"{kind}/{ds['connection']}"


def pct(n: int, d: int) -> str:
    return f"{n} ({100 * n // d}%)" if d else str(n)


def _dt(v: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None
    except ValueError:
        return None


def _why(err: str) -> str:
    return "not permitted for this account (HTTP 403)" if "403" in err else f"failed ({err[:80]})"


def extract_health(
    ok: dict, tasks: list, tasks_err: str | None, jobs: list, jobs_err: str | None
) -> None:
    """Counts only (no client/workbook names)."""
    now = datetime.now(UTC)
    wb_ext: dict[str, list[dict]] = {}  # workbook id -> its BigQuery extract data sources
    for wid, d in ok.items():
        ex = [ds for ds in d["datasources"] if ds_category(ds) == "bigquery/extract"]
        if ex:
            wb_ext[wid] = ex
    all_ds = [ds for exs in wb_ext.values() for ds in exs]
    print("\n=== EXTRACT HEALTH (BigQuery extracts; counts only) ===")
    print(f"BigQuery extract data sources: {len(all_ds)} in {len(wb_ext)} workbooks")

    if tasks_err:
        print(f"Extract refresh tasks: {_why(tasks_err)}")
    else:
        by_wb = {t["workbook_id"] for t in tasks if t["workbook_id"]}
        by_ds = {t["datasource_id"] for t in tasks if t["datasource_id"]}
        sched = {
            w for w, exs in wb_ext.items() if w in by_wb or any(x.get("id") in by_ds for x in exs)
        }
        failing = [t for t in tasks if t["failed_count"] > 0]
        overdue = [t for t in tasks if (n := _dt(t["next_run_at"])) and n < now - timedelta(days=1)]
        print(f"Refresh tasks visible to this account: {len(tasks)}")
        print(f"  with failures (consecutive failed > 0): {len(failing)}")
        print(f"  overdue (next run >1 day in the past):  {len(overdue)}")
        print(
            f"Workbooks with BigQuery extracts and NO visible refresh schedule: {len(wb_ext) - len(sched)}"
            f" of {len(wb_ext)}"
        )
        if len(tasks) < len(wb_ext) // 4:
            print(
                "  CAUTION: very few tasks are visible; this account probably can't see schedules owned"
                " by others, so 'no schedule' is an upper bound, not a fact."
            )

    ages = [(now - r) for ds in all_ds if (r := _dt(ds.get("extract_last_refresh")))]
    unknown = len(all_ds) - len(ages)
    print(
        f"Last refreshed (from Metadata API, {len(ages)} of {len(all_ds)} extracts have a timestamp):"
    )
    for days in (7, 30, 90, 365):
        print(f"  older than {days:3} days: {sum(1 for a in ages if a > timedelta(days=days))}")
    print(f"  no timestamp: {unknown}")
    oldest = sorted(now - a for a in ages)[:5]
    print(
        "  oldest last-refreshed dates:", ", ".join(d.date().isoformat() for d in oldest) or "n/a"
    )

    if jobs_err:
        print(f"Recent refresh job outcomes: {_why(jobs_err)}")
    else:
        c = Counter((j["status"] or "unknown") for j in jobs)
        print(f"Recent refresh jobs visible: {len(jobs)}  by status: {dict(c)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    a = ap.parse_args()
    cfg = load_config()
    if not cfg:
        sys.exit("Tableau is not configured (.env or RDB_TABLEAU_SECRET_NAME)")
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    with session(per_call=True) as s:
        info, site = s.server_info(), s.site_info()
        projects, rest_wbs, rest_ds = s.projects(), s.workbooks(), s.datasources()
        try:
            index = s.metadata_workbook_index()
        except TableauError as e:
            index = []
            print("Metadata index failed:", e)
        todo = rest_wbs[: a.limit] if a.limit else rest_wbs
        print(f"fetching metadata for {len(todo)} workbooks...", flush=True)
        details = s.workbook_details(
            [w["id"] for w in todo],
            workers=a.workers,
            on_done=lambda n, t: print(f"  {n}/{t}", end="\r", flush=True) if n % 25 == 0 else None,
        )
        # extract refresh schedules + recent refresh jobs (read-only; may be permission-limited)
        tasks, tasks_err, jobs, jobs_err = [], None, [], None
        try:
            tasks = s.extract_refresh_tasks()
        except TableauError as e:
            tasks_err = str(e)
        try:
            jobs = s.refresh_jobs()
        except TableauError as e:
            jobs_err = str(e)
        # one sample view download: column names + row count only
        sample = None
        for w in todo[:40]:
            try:
                views = s.workbook_views(w["id"])
            except TableauError:
                continue
            for v in views[:2]:
                try:
                    rows = list(csv.reader(io.StringIO(s.view_csv(v["id"]))))
                except TableauError as e:
                    sample = {"workbook": w["name"], "view": v["name"], "error": str(e)[:150]}
                    continue
                if rows:
                    sample = {
                        "workbook": w["name"],
                        "view": v["name"],
                        "columns": rows[0],
                        "rows": len(rows) - 1,
                    }
                    break
            if sample and "columns" in sample:
                break

    ok = {i: d for i, d in details.items() if isinstance(d, dict)}
    failed = {i: d for i, d in details.items() if isinstance(d, str)}
    (OUT / "inventory.json").write_text(
        json.dumps(
            {
                "server": cfg.host,
                "site": cfg.site,
                "version": info,
                "projects": projects,
                "rest_workbooks": rest_wbs,
                "rest_datasources": rest_ds,
                "metadata_index": index,
                "workbook_details": ok,
                "detail_errors": failed,
            },
            indent=1,
        )
    )

    # ---------- aggregate ----------
    cat, ctypes, bq_tables, rest_types = Counter(), Counter(), Counter(), Counter()
    hidden_tables = hidden_ds = 0
    lod_f = tc_f = par_f = calc_f = 0
    lod_w, tc_w, par_w = set(), set(), set()
    rows_out = []
    pub_names: set[str] = set()
    for i, d in ok.items():
        for ds in d["datasources"]:
            cat[ds_category(ds)] += 1
            for ct in ds.get("connection_types") or []:
                rest_types[ct] += 1
            if ds.get("hidden_upstream"):
                hidden_tables += ds["hidden_upstream"]
                hidden_ds += 1
            if ds["published"]:
                pub_names.add(ds["name"])
            for t in ds["tables"]:
                if t["connection_type"]:
                    ctypes[t["connection_type"]] += 1
                if (t["connection_type"] or "").lower() == "bigquery":
                    bq_tables[f"{t['database']}.{t['schema']}.{t['name']}"] += 1
            for f in ds["fields"]:
                fm = f.get("formula")
                if "Param" in (f.get("type") or ""):
                    par_f += 1
                    par_w.add(i)
                if not fm:
                    continue
                calc_f += 1
                if LOD.search(fm):
                    lod_f += 1
                    lod_w.add(i)
                if TABLE_CALC.search(fm):
                    tc_f += 1
                    tc_w.add(i)
                if PARAM.search(fm):
                    par_f += 1
                    par_w.add(i)
        rows_out.append((d["name"], len(d["sheets"]), len(d["dashboards"]), len(d["datasources"])))
    (OUT / "per_workbook_counts.tsv").write_text(
        "workbook\tsheets\tdashboards\tdatasources\n"
        + "\n".join("\t".join(map(str, r)) for r in rows_out)
    )

    ds_total = sum(cat.values())
    print("\n=== TABLEAU INVENTORY (summary; full dump in .local/tableau_inventory/) ===")
    print(
        f"Server: {cfg.host}  (Tableau Cloud: {'yes' if cfg.host.endswith('online.tableau.com') else 'NO'})"
        f"  version {info.get('product_version')}, REST API {info.get('rest_api_version')}"
    )
    print(
        f"Site: contentUrl={site['content_url']}  name={site['name'] or '(not readable by this account)'}"
    )
    print(
        f"Projects visible: {len(projects)}   Workbooks visible (REST): {len(rest_wbs)}"
        f"   Published data sources (REST): {len(rest_ds)}"
    )
    print(
        f"Workbooks in Metadata API index: {len(index)}   detail fetched OK: {len(ok)}/{len(todo)}"
        f"   failed/not indexed: {len(failed)}   partial (node limit): "
        f"{sum(1 for d in ok.values() if d.get('partial'))}"
    )
    if rows_out:
        for label, k in (("sheets", 1), ("dashboards", 2), ("data sources", 3)):
            v = [r[k] for r in rows_out]
            print(
                f"  per workbook {label}: min {min(v)} / median {statistics.median(v)} / max {max(v)} / total {sum(v)}"
            )
        print("  top 15 workbooks by sheet count (name, sheets, dashboards, data sources):")
        for r in sorted(rows_out, key=lambda r: -r[1])[:15]:
            print(f"    {r[0][:50]:50} {r[1]:4} {r[2]:4} {r[3]:4}")
    print(
        f"\nData source instances (per workbook, incl. published ones reused): {ds_total}; "
        f"unique published used: {len(pub_names)}"
    )
    for k, n in sorted(cat.items(), key=lambda kv: -kv[1]):
        print(f"  {k:28} {pct(n, ds_total)}")
    print("  connection types (Metadata upstream tables):", dict(ctypes.most_common(8)))
    print("  connection types (REST, per data source):", dict(rest_types.most_common(10)))
    print(
        f"  upstream tables hidden from this account (null names): {hidden_tables} "
        f"across {hidden_ds} data sources"
    )
    print(f"\nCalculated fields (instances): {calc_f}")
    print(f"  LOD expressions: {lod_f} fields in {len(lod_w)} workbooks")
    print(f"  table calcs:     {tc_f} fields in {len(tc_w)} workbooks")
    print(f"  parameters:      {par_f} refs/fields in {len(par_w)} workbooks")
    print("\nMark/chart types: NOT exposed by the Metadata API (would need workbook XML download).")
    print(f"\nUpstream BigQuery tables resolvable via Metadata API: {len(bq_tables)} distinct")
    for t, n in bq_tables.most_common(15):
        print(f"  {n:4}  {t}")
    print("\nAccess observed for the reporting account:")
    print(
        f"  site record readable: {'yes' if site['name'] else 'no (403: not a site/content admin)'}"
    )
    print(
        f"  REST workbooks {len(rest_wbs)} vs Metadata API {len(index)}"
        + ("  -> Metadata sees MORE than REST lists" if len(index) > len(rest_wbs) else "")
    )
    rest_ids = {w["id"] for w in rest_wbs}
    extra = [i for i in index if i["id"] not in rest_ids]
    print(
        f"  Metadata-only workbooks (not in REST list): {len(extra)}"
        f"  (of which name hidden/null: {sum(1 for i in extra if not i['name'])})"
    )
    print(
        f"  top-level projects: {sum(1 for p in projects if not p['parent_id'])} of {len(projects)}"
    )
    if sample:
        if "columns" in sample:
            print(f"\nSample view CSV: {len(sample['columns'])} columns, {sample['rows']} rows")
            print("  columns:", sample["columns"][:30])
        else:
            print("\nSample view CSV failed:", sample.get("error"))
    else:
        print("\nSample view CSV: no view found in first workbooks")
    extract_health(ok, tasks, tasks_err, jobs, jobs_err)
    print(f"\nDone in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
