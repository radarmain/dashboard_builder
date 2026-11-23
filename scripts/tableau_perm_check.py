"""READ-ONLY Tableau permission re-check for the top-level project '.RADaR - Direct Clients'.

Run: AWS_PROFILE=concrete .venv/bin/python scripts/tableau_perm_check.py
Uses the locked client (per-call lock, GET + read-only GraphQL only). Details go to
.local/tableau_inventory/ (gitignored); stdout has counts only (no client/workbook/table/field names).
"""

import hashlib
import io
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.tableau_client import TableauError, session  # noqa: E402

OUT = ROOT / ".local" / "tableau_inventory"
SAMPLES = OUT / "samples"
TOP = ".RADaR - Direct Clients"
INDEX_Q = (
    "{ workbooksConnection(first: 100, after: %s) { nodes { luid name projectName projectLuid } "
    "pageInfo { hasNextPage endCursor } } }"
)


def tree_ids(projects: list[dict], root_id: str) -> set[str]:
    s, grew = {root_id}, True
    while grew:
        grew = False
        for p in projects:
            if p["parent_id"] in s and p["id"] not in s:
                s.add(p["id"])
                grew = True
    return s


def depth_of(pid: str, by_id: dict, root: str) -> int:
    d = 0
    while pid != root:
        pid = by_id[pid]["parent_id"]
        d += 1
    return d


def meta_index(s) -> list[dict]:
    out, after = [], "null"
    while True:
        d = s.graphql(INDEX_Q % after)
        conn = (d.get("data") or {}).get("workbooksConnection") or {}
        out += conn.get("nodes") or []
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return out
        after = json.dumps(info["endCursor"])


def analyze_twb(xml_bytes: bytes) -> dict:
    root = ET.fromstring(xml_bytes)
    classes = Counter(c.get("class") for c in root.iter("connection") if c.get("class"))
    sheets = root.findall("./worksheets/worksheet")
    dashes = root.findall("./dashboards/dashboard")
    tables, sqls = set(), set()
    for r in root.iter("relation"):
        if r.get("type") == "table" and r.get("table"):
            tables.add(r.get("table"))
        elif r.get("type") == "text" and (r.text or "").strip():
            sqls.add(hashlib.md5(r.text.strip().encode()).hexdigest())
    live = extract = 0
    bq_ds = 0
    for ds in root.findall("./datasources/datasource"):
        if ds.get("name") == "Parameters":
            continue
        cls = {c.get("class") for c in ds.iter("connection")}
        if "bigquery" not in cls:
            continue
        bq_ds += 1
        ex = ds.find("extract")
        if ex is not None and ex.get("enabled", "true") != "false" or {"hyper", "dataengine"} & cls:
            extract += 1
        else:
            live += 1
    return {
        "connection_classes": dict(classes),
        "sheets": len(sheets),
        "dashboards": len(dashes),
        "distinct_tables": len(tables),
        "distinct_custom_sql": len(sqls),
        "bigquery_datasources": bq_ds,
        "bigquery_live": live,
        "bigquery_extract": extract,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    SAMPLES.mkdir(parents=True, exist_ok=True)
    old = json.loads((OUT / "inventory.json").read_text())
    old_by_id = {p["id"]: p for p in old["projects"]}
    root_old = next(p for p in old["projects"] if p["name"] == TOP)
    old_tree = tree_ids(old["projects"], root_old["id"])
    old_names = {old_by_id[i]["name"] for i in old_tree}
    old_rest = [w for w in old["rest_workbooks"] if w["project_id"] in old_tree]
    old_meta_name = [m for m in old["metadata_index"] if m["project"] in old_names]
    old_null = sum(
        ds.get("hidden_upstream", 0)
        for w in old_rest
        for ds in (old["workbook_details"].get(w["id"]) or {}).get("datasources", [])
    )
    old_depth = Counter(depth_of(w["project_id"], old_by_id, root_old["id"]) for w in old_rest)

    with session(per_call=True) as s:
        projects = s.projects()
        by_id = {p["id"]: p for p in projects}
        root = next(p for p in projects if p["name"] == TOP and not p["parent_id"])
        tree = tree_ids(projects, root["id"])
        names = {by_id[i]["name"] for i in tree}
        print(
            f"projects visible now: {len(projects)} (before {len(old['projects'])}); "
            f"in tree: {len(tree)} (before {len(old_tree)})",
            flush=True,
        )
        raw = s._paged("/workbooks", "workbooks", "workbook")
        rest = [w for w in raw if (w.get("project") or {}).get("id") in tree]
        rest_depth = Counter(depth_of(w["project"]["id"], by_id, root["id"]) for w in rest)
        try:
            idx = meta_index(s)
            meta_by_luid = [m for m in idx if m.get("projectLuid") in tree]
            meta_by_name = [m for m in idx if m.get("projectName") in names]
        except TableauError as e:
            idx, meta_by_luid, meta_by_name = [], [], []
            print("metadata index failed:", str(e)[:150])
        print("fetching metadata detail for REST-visible workbooks in tree...", flush=True)
        details = s.workbook_details([w["id"] for w in rest], workers=1)
        ok = {i: d for i, d in details.items() if isinstance(d, dict)}
        new_null = sum(ds.get("hidden_upstream", 0) for d in ok.values() for ds in d["datasources"])
        new_ds = sum(len(d["datasources"]) for d in ok.values())
        (OUT / "permcheck_after.json").write_text(
            json.dumps(
                {
                    "rest_in_tree": rest,
                    "meta_by_luid": meta_by_luid,
                    "details": ok,
                    "detail_errors": {i: d for i, d in details.items() if isinstance(d, str)},
                },
                indent=1,
            )
        )

        # ---- smallest workbook ----
        def size(w: dict) -> int:
            try:
                return int(w.get("size"))
            except (TypeError, ValueError):
                return 10**9

        has_size = sum(1 for w in rest if w.get("size") is not None)
        smallest = min(rest, key=size) if rest else None
        dl: dict = {}
        if smallest:
            path = s._site_path(f"/workbooks/{smallest['id']}/content")
            try:
                r = s._get(path, {"includeExtract": "False"})
                body = r.content
                is_zip = body[:2] == b"PK"
                ext = "twbx" if is_zip else "twb"
                f = SAMPLES / f"sample_{smallest['id'][:8]}.{ext}"
                f.write_bytes(body)
                dl = {
                    "ok": True,
                    "ext": ext,
                    "bytes": len(body),
                    "file": str(f.relative_to(ROOT)),
                    "rest_size_mb": smallest.get("size"),
                }
            except TableauError as e:
                dl = {"ok": False, "error": str(e)[:300]}

    # ---------- report ----------
    print("\n=== 1. VISIBILITY (project '.RADaR - Direct Clients' + nested) ===")
    print(f"{'':34}{'before':>8}{'now':>8}")
    print(f"{'REST workbooks':34}{len(old_rest):8}{len(rest):8}")
    print(f"{'Metadata API workbooks (by name)':34}{len(old_meta_name):8}{len(meta_by_name):8}")
    print(f"{'Metadata API workbooks (projectLuid)':34}{'n/a':>8}{len(meta_by_luid):8}")
    print(f"{'Null-name upstream tables':34}{old_null:8}{new_null:8}")
    print(f"  (workbooks with metadata detail now: {len(ok)}/{len(rest)}; data sources: {new_ds})")
    print("REST workbooks by nesting depth (0 = top-level project):")
    for d in sorted(set(old_depth) | set(rest_depth)):
        print(f"  depth {d}: before {old_depth.get(d, 0):4}  now {rest_depth.get(d, 0):4}")
    print(f"REST workbooks with a size attribute: {has_size}/{len(rest)}")

    print("\n=== 2. DOWNLOAD (smallest visible workbook, no extract) ===")
    if not smallest:
        print("no workbooks visible; nothing to download")
        return
    print(f"chosen REST size: {smallest.get('size')} MB (units as reported by Tableau)")
    if not dl.get("ok"):
        print("FAILED:", dl.get("error"))
        return
    print(f"worked: yes  type: .{dl['ext']}  size: {dl['bytes']} bytes  saved: {dl['file']}")

    print("\n=== 3. WORKBOOK XML (counts only) ===")
    body = (ROOT / dl["file"]).read_bytes()
    if dl["ext"] == "twbx":
        z = zipfile.ZipFile(io.BytesIO(body))
        members = z.namelist()
        twb = next(n for n in members if n.endswith(".twb"))
        xml = z.read(twb)
        print(
            f"archive members: {len(members)} "
            f"(extract files: {sum(1 for n in members if n.endswith(('.hyper', '.tde')))})"
        )
    else:
        xml = body
    a = analyze_twb(xml)
    (OUT / "samples" / "sample_analysis.json").write_text(json.dumps(a, indent=1))
    print("connection classes:", a["connection_classes"])
    print(f"worksheets: {a['sheets']}  dashboards: {a['dashboards']}")
    print(
        f"distinct tables: {a['distinct_tables']}  distinct custom SQL queries: {a['distinct_custom_sql']}"
    )
    print(
        f"BigQuery data sources: {a['bigquery_datasources']} "
        f"(live: {a['bigquery_live']}, extract-based: {a['bigquery_extract']})"
    )


if __name__ == "__main__":
    main()
