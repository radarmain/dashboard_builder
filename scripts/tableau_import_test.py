"""Run the Tableau import (STRUCTURE mode: dry runs only, zero bytes scanned) for ONE workbook against
a test project and print a readable report. Nothing is saved or published (the spec lives in memory).

Usage (local API running via scripts/start_local.sh):
  .venv/bin/python scripts/tableau_import_test.py                 # sign in via getpass prompts
  RDB_TOKEN=... .venv/bin/python scripts/tableau_import_test.py   # reuse a token
  .venv/bin/python scripts/tableau_import_test.py --in-process    # no HTTP/Cognito: same routes via TestClient
Options: --client rdb-test --project tableau-import-test --sheet NAME (only that sheet)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
os.environ.setdefault("AWS_PROFILE", "concrete")
sys.path.insert(0, str(ROOT / "backend"))

WORKBOOK_ID = "ccd288ca-4b6a-4c25-bb7b-55701b8be397"
WORKBOOK_NAME = "Storage Mart - Occupancy"
BQ_PROJECT = "storagemart-463212"
TABLE = "storagemart-463212.client_data.r_storagemart_occupancy"
API = os.environ.get("RDB_API", "http://localhost:8000")


def make_http(a: argparse.Namespace):
    if a.in_process:
        from app.auth import User, require_builder
        from app.main import app
        from fastapi.testclient import TestClient

        app.dependency_overrides[require_builder] = lambda: User(
            sub="import-cli", email="import-cli@local", groups=["analyst"]
        )
        return TestClient(app)
    token = os.environ.get("RDB_TOKEN")
    if not token:
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "get_token.py")],
            stdout=subprocess.PIPE,
            text=True,
        )
        token = r.stdout.strip()
        if r.returncode != 0 or not token:
            sys.exit("could not get a token")
    return httpx.Client(
        base_url=a.base_url, headers={"Authorization": f"Bearer {token}"}, timeout=300
    )


def ok(r, what: str):
    if r.status_code >= 300:
        sys.exit(f"{what} failed: HTTP {r.status_code} {r.text[:400]}")
    return r.json()


def ensure_project(http, c: str, p: str) -> None:
    if http.get(f"/api/clients/{c}").status_code != 200:
        sys.exit(f"client {c!r} does not exist")
    if http.get(f"/api/clients/{c}/projects/{p}").status_code == 404:
        ok(
            http.post(
                f"/api/clients/{c}/projects", json={"name": "Tableau import test", "slug": p}
            ),
            "create project",
        )
        print(f"created project {c}/{p}")
    cfg = ok(
        http.put(
            f"/api/clients/{c}/projects/{p}/settings",
            json={"bq_project": BQ_PROJECT, "tables": [TABLE]},
        ),
        "settings",
    )
    print(f"project tables: {cfg['tables']}")
    ok(
        http.put(
            f"/api/clients/{c}/projects/{p}/tableau-link",
            json={"workbook_id": WORKBOOK_ID, "workbook_name": WORKBOOK_NAME},
        ),
        "link",
    )


def show_plan(plan: dict) -> None:
    print("\n=== PLAN (sanitized) ===")
    for d in plan["datasources"]:
        print(
            f"datasource {d['name']!r}: {d['mode']}, {d.get('connection_classes')}, tables={d['tables']}"
        )
        for f in d.get("filters", []):
            print(f"   data source filter: {f['field']} [{f['type']}] {f.get('default')}")
        for j in d.get("joins", []):
            print(f"   join: {j}")
    for c in plan["calculated_fields"]:
        print(f"calc [{c['kind']}] {c['name']} = {c['formula']}")
    for p in plan["parameters"]:
        print(f"parameter {p['name']} default={p.get('default')}")
    for d in plan["dashboards"]:
        print(
            f"dashboard {d['name']!r}: {[s['name'] for s in d['sheets']]} layout={d.get('layout')}"
        )
    for s in plan["worksheets"]:
        flt = [f"{f['field']}: {f.get('default')}" for f in s["filters"]]
        print(
            f"sheet {s['name']!r}: {s['chart_type']} [{s['status']}] {s['status_reason']}\n"
            f"   rows={[f['field'] for f in s['rows']]} cols={[f['field'] for f in s['columns']]}\n"
            f"   filters={flt} grid={s.get('grid')} dual_axis={s['dual_axis']}"
        )


def apply_ops(spec: dict, ops: list[dict]) -> None:
    for op in ops:
        if op["op"] == "add_element":
            spec["elements"].append(op["element"])
            spec["layout"].append({"element_id": op["element"]["id"], **op["layout"]})
        elif op["op"] == "update_element":
            spec["elements"] = [
                op["element"] if e["id"] == op["element"]["id"] else e for e in spec["elements"]
            ]
            if op.get("layout"):
                for item in spec["layout"]:
                    if item["element_id"] == op["element"]["id"]:
                        item.update(op["layout"])
        else:
            spec["elements"] = [e for e in spec["elements"] if e["id"] != op["id"]]
            spec["layout"] = [i for i in spec["layout"] if i["element_id"] != op["id"]]


def run_sheet(http, base: str, import_id: str, name: str, spec: dict) -> dict | None:
    print(f"\n--- {name} ---")
    entry = None
    t0 = time.monotonic()
    body = {"import_id": import_id, "current_spec": spec, "view_name": spec["view"]["name"]}
    with http.stream("POST", f"{base}/tableau-import/sheets/{name}/run", json=body) as r:
        if r.status_code != 200:
            r.read()
            print(f"  HTTP {r.status_code}: {r.text[:300]}")
            return None
        in_text = False
        last_status = ""
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            t = ev["type"]
            if t == "text":
                if not in_text:
                    print("  AI: ", end="")
                    in_text = True
                print(ev["delta"], end="", flush=True)
                continue
            if in_text:
                print()
                in_text = False
            if t == "status" and ev["text"] != last_status:
                last_status = ev["text"]
                print(f"  [{ev['text']}] {time.monotonic() - t0:.0f}s")
            elif t == "changes":
                apply_ops(spec, ev["operations"])
                for op in ev["operations"]:
                    e = op.get("element", {})
                    print(f"  + {op['op']} {e.get('id')} {e.get('type')} layout={op.get('layout')}")
            elif t == "error":
                print(f"  ERROR: {ev['message']}")
            elif t == "report":
                entry = ev["entry"]
    return entry


def show_entry(e: dict) -> None:
    print(
        f"\n  RESULT {e['sheet']!r}: status={e['status']} (plan: {e['plan_status']}, mode: {e.get('mode')}) "
        f"element={e['element_id']}"
    )
    if e.get("status_reason"):
        print(f"  reason: {e['status_reason']}")
    if e.get("sql"):
        print("  SQL:\n    " + "\n    ".join(e["sql"].strip().splitlines()))
    if e.get("dry_run_bytes") is not None:
        print(
            f"  dry-run estimate: {e['dry_run_bytes']} bytes ({e['dry_run_bytes'] / 1e9:.2f} GB); "
            f"source_is_view={e.get('source_is_view')} base_tables={e.get('base_tables')}"
        )
    if e.get("result_columns"):
        print(
            "  result columns: "
            + ", ".join(f"{c['name']} {c['type']}" for c in e["result_columns"])
        )
    if e.get("config"):
        print(f"  chart config: {json.dumps(e['config'])}")
    for k in ("filters_baked", "notes", "warnings"):
        for x in e.get(k) or []:
            print(f"  {k}: {x}")
    print(f"  scanned this step: {e['bytes_scanned']} bytes")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default="rdb-test")
    ap.add_argument("--project", default="tableau-import-test")
    ap.add_argument("--sheet")
    ap.add_argument("--base-url", default=API)
    ap.add_argument("--in-process", action="store_true")
    a = ap.parse_args()
    http = make_http(a)
    base = f"/api/clients/{a.client}/projects/{a.project}"
    ensure_project(http, a.client, a.project)

    t0 = time.monotonic()
    pr = ok(http.get(f"{base}/tableau-import/plan"), "plan")
    plan, import_id = pr["plan"], pr["import_id"]
    print(f"plan downloaded and parsed in {time.monotonic() - t0:.1f}s; import_id={import_id}")
    show_plan(plan)

    name = f"Imported: {WORKBOOK_NAME}"
    spec = {
        "schema_version": 1,
        "view": {"name": name, "slug": "imported-storage-mart-occupancy"},
        "layout": [],
        "elements": [],
    }
    sheets = sorted(
        plan["worksheets"],
        key=lambda s: ((s.get("grid") or {}).get("y", 99), (s.get("grid") or {}).get("x", 0)),
    )
    total = 0
    for s in sheets:
        if a.sheet and s["name"] != a.sheet:
            continue
        if s["status"] == "unsupported":
            print(f"\n--- {s['name']} --- SKIPPED (unsupported: {s['status_reason']})")
            continue
        e = run_sheet(http, base, import_id, s["name"], spec)
        if e:
            show_entry(e)
            total += e["bytes_scanned"]

    print("\n=== FINAL (in memory only; nothing saved) ===")
    for e in spec["elements"]:
        item = next(i for i in spec["layout"] if i["element_id"] == e["id"])
        print(
            f"{e['id']} {e['type']} {e['title']!r} cache_ttl={e['cache_ttl_seconds']} layout={ {k: item[k] for k in 'xywh'} }"
        )
    print(f"\nTOTAL bytes scanned this run: {total} (structure mode must be 0)")
    rep = http.get(f"{base}/tableau-import/{import_id}/report")
    print(
        f"report stored: HTTP {rep.status_code}, sheets={list(rep.json()['sheets']) if rep.status_code == 200 else None}"
    )


if __name__ == "__main__":
    main()
