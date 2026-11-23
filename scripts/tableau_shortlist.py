"""READ-ONLY canonical-workbook shortlist for three top-level Tableau projects.

Run: AWS_PROFILE=concrete .venv/bin/python scripts/tableau_shortlist.py

Uses the locked client (per-call lock; GET + read-only GraphQL only). Details/CSVs go to
.local/tableau_inventory/ (gitignored). stdout has counts only (no client/workbook names).

Client project = the folder directly below the top-level project (workbooks sitting directly in a
top-level project are grouped as "(top level)"). Environment sub-folders such as Production /
Development live INSIDE a client folder, so a client is ranked across all its sub-folders.
"""

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.tableau_client import TableauError, session  # noqa: E402

OUT = ROOT / ".local" / "tableau_inventory"
TOPS = [".RADaR - Direct Clients", "Coegi Agency", "True Media Agency"]
FLAG = re.compile(r"(?<![a-z])(test|dev|development|copy|old|internal view)(?![a-z])", re.I)
PATH_FLAG = re.compile(r"(?<![a-z])(test|testing|dev|development|old|archive)(?![a-z])", re.I)
TOP_N = 3
SIM = 0.92


def tree_ids(projects: list[dict], root_id: str) -> set[str]:
    s, grew = {root_id}, True
    while grew:
        grew = False
        for p in projects:
            if p["parent_id"] in s and p["id"] not in s:
                s.add(p["id"])
                grew = True
    return s


def path_of(pid: str, by_id: dict) -> list[str]:
    out = []
    while pid:
        out.append(by_id[pid]["name"])
        pid = by_id[pid]["parent_id"]
    return out[::-1]


def ds_types(detail: dict | None) -> str:
    if not detail:
        return "unknown"
    labels = set()
    for ds in detail["datasources"]:
        types = {t["connection_type"] for t in ds["tables"] if t["connection_type"]}
        types |= {d["connection_type"] for d in ds["databases"] if d["connection_type"]}
        types |= set(ds.get("connection_types") or [])
        types = {x.lower() for x in types}
        if "bigquery" in types:
            labels.add("BigQuery extract" if ds["connection"] == "extract" else "BigQuery live")
        elif types:
            labels.add("other")
        else:
            labels.add("unknown")
    return "; ".join(sorted(labels)) or "none"


def norm(name: str) -> str:
    n = re.sub(r"\(?\bcopy\b\)?|\bcopy of\b|[\s_\-]*\d+$", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", n)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    old = json.loads((OUT / "inventory.json").read_text())
    old_counts, old_projects = {}, {}
    for t in TOPS:
        root = next(p for p in old["projects"] if p["name"] == t and not p["parent_id"])
        tr = tree_ids(old["projects"], root["id"])
        old_projects[t] = len(tr)
        old_counts[t] = sum(1 for w in old["rest_workbooks"] if w["project_id"] in tr)
    # detail cache: earlier dumps, valid only if the workbook is unchanged
    cache: dict[str, dict] = dict(old["workbook_details"])
    old_upd = {w["id"]: w.get("updated_at") for w in old["rest_workbooks"]}
    extra = OUT / "permcheck_after.json"
    if extra.exists():
        cache.update(json.loads(extra.read_text()).get("details", {}))

    with session(per_call=True) as s:
        projects = s.projects()
        by_id = {p["id"]: p for p in projects}
        scope: dict[str, str] = {}  # project id -> top-level name
        n_projects = {}
        for t in TOPS:
            root = next(p for p in projects if p["name"] == t and not p["parent_id"])
            tr = tree_ids(projects, root["id"])
            n_projects[t] = len(tr)
            scope.update({i: t for i in tr})
        raw = s._paged("/workbooks", "workbooks", "workbook")
        wbs = [w for w in raw if (w.get("project") or {}).get("id") in scope]
        print(
            f"workbooks visible on site: {len(raw)}; in the three projects: {len(wbs)}", flush=True
        )

        # usage statistics: per-view totals, summed per workbook (REST, GET only)
        usage: dict[str, int] = defaultdict(int)
        views_seen = views_with_usage = nonzero = 0
        usage_err = None
        ids = {w["id"] for w in wbs}
        try:
            for v in s._paged("/views", "views", "view", {"includeUsageStatistics": "true"}):
                wid = (v.get("workbook") or {}).get("id")
                if wid not in ids:
                    continue
                views_seen += 1
                u = (v.get("usage") or {}).get("totalViewCount")
                if u is not None:
                    views_with_usage += 1
                    usage[wid] += int(u)
                    nonzero += int(u) > 0
        except TableauError as e:
            usage_err = str(e)[:200]
        usage_visible = views_with_usage > 0 and nonzero > 0

        # metadata detail (sheets, dashboards, data source types): cache, then fetch the rest
        details: dict[str, dict] = {}
        todo = []
        for w in wbs:
            c = cache.get(w["id"])
            if isinstance(c, dict) and old_upd.get(w["id"]) in (None, w.get("updatedAt")):
                details[w["id"]] = c
            else:
                todo.append(w["id"])
        print(f"detail from cache: {len(details)}; fetching: {len(todo)}", flush=True)
        errs = 0
        if todo:
            got = s.workbook_details(
                todo,
                workers=1,
                on_done=lambda n, t: print(f"  {n}/{t}", flush=True) if n % 25 == 0 else None,
            )
            for i, d in got.items():
                if isinstance(d, dict):
                    details[i] = d
                else:
                    errs += 1

    # ---------- build rows ----------
    rows = []
    for w in wbs:
        pid = w["project"]["id"]
        path = path_of(pid, by_id)
        top = scope[pid]
        sub = path[1:]
        d = details.get(w["id"])
        rows.append(
            {
                "top": top,
                "client": sub[0] if sub else "(top level)",
                "project_path": " / ".join(path),
                "sub_path": " / ".join(sub[1:]),
                "name": w["name"],
                "views": usage.get(w["id"], 0) if usage_visible else "",
                "date": (w.get("updatedAt") or "")[:10],
                "types": ds_types(d),
                "sheets": len(d["sheets"]) if d else "",
                "dashboards": len(d["dashboards"]) if d else "",
                "id": w["id"],
                "project_id": pid,
                "size": w.get("size"),
                "reasons": [],
            }
        )

    # ---------- duplicate / test flags ----------
    for r in rows:
        if FLAG.search(r["name"]):
            r["reasons"].append("name keyword")
        if r["sub_path"] and PATH_FLAG.search(r["sub_path"]):
            r["reasons"].append("dev/test folder")

    def score(r: dict) -> tuple:
        return (r["views"] if r["views"] != "" else 0, r["date"])

    by_proj = defaultdict(list)
    for r in rows:
        by_proj[r["project_id"]].append(r)
    for grp in by_proj.values():
        for i, a in enumerate(grp):
            for b in grp[i + 1 :]:
                na, nb = norm(a["name"]), norm(b["name"])
                if na and nb and SequenceMatcher(None, na, nb).ratio() >= SIM:
                    loser = a if score(a) < score(b) else b
                    loser["reasons"].append("near-identical name")
    for r in rows:
        r["dup"] = "yes" if r["reasons"] else "no"

    # ---------- shortlist: top 3 per client ----------
    clients = defaultdict(list)
    for r in rows:
        clients[(r["top"], r["client"])].append(r)
    short = []
    for key in sorted(clients):
        rs = sorted(clients[key], key=score, reverse=True)
        good = [r for r in rs if r["dup"] == "no"]
        short += (good or rs)[:TOP_N]

    cols = [
        "top-level project",
        "client project path",
        "workbook name",
        "view count",
        "last viewed or updated date",
        "data source types",
        "sheets",
        "dashboards",
        "likely duplicate",
        "workbook id",
    ]

    def line(r: dict) -> list:
        return [
            r["top"],
            r["client"],
            r["name"],
            r["views"],
            r["date"],
            r["types"],
            r["sheets"],
            r["dashboards"],
            r["dup"],
            r["id"],
        ]

    with (OUT / "shortlist.csv").open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(cols)
        wr.writerows(line(r) for r in short)
    with (OUT / "shortlist_all_workbooks.csv").open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["project path", *cols, "duplicate reasons", "rest size (MB)"])
        for r in sorted(rows, key=lambda r: (r["top"], r["project_path"], r["name"])):
            wr.writerow([r["project_path"], *line(r), "; ".join(r["reasons"]), r["size"]])

    # ---------- summary (counts only) ----------
    print("\n=== 1. VISIBLE WORKBOOKS (REST) before vs now ===")
    print(f"{'':28}{'before':>8}{'now':>8}   projects before/now")
    now_counts = Counter(r["top"] for r in rows)
    for t in TOPS:
        print(
            f"{'top-level #' + str(TOPS.index(t) + 1):28}{old_counts[t]:8}{now_counts[t]:8}"
            f"   {old_projects[t]}/{n_projects[t]}"
        )
    print("\n=== 2. USAGE / DATES ===")
    if usage_err:
        print(f"usage statistics request failed: {usage_err}")
    print(
        f"views in scope: {views_seen}; with a usage field: {views_with_usage}; "
        f"with non-zero views: {nonzero}"
    )
    if usage_visible:
        print("usage statistics visible: YES (view count = sum of view totals per workbook)")
    else:
        print(
            "usage statistics NOT visible to this account: ranking falls back to last-updated date;"
            " view count column is blank"
        )
    print("REST has no per-workbook 'last viewed'; the date column is the workbook's updatedAt")
    print(
        f"metadata detail missing for {errs + sum(1 for r in rows if r['sheets'] == '')} "
        f"workbooks (sheets/dashboards blank)"
    )
    print(
        "data source type mix (workbooks):", dict(Counter(r["types"] for r in rows).most_common(8))
    )
    print("\n=== 3-5. DUPLICATES, CLIENTS, SHORTLIST ===")
    print(
        f"flagged likely duplicate/test: {sum(r['dup'] == 'yes' for r in rows)} of {len(rows)}"
        f"  (reasons: {dict(Counter(x for r in rows for x in r['reasons']))})"
    )
    print(f"{'workbooks per top-level':28}", dict(now_counts))
    ncl = Counter(k[0] for k in clients)
    print(f"{'client project folders':28}", dict(ncl), "total", len(clients))
    print(
        f"clients where only flagged workbooks exist: "
        f"{sum(1 for k in clients if all(r['dup'] == 'yes' for r in clients[k]))}"
    )
    print(f"CSV rows (excluding header): {len(short)}")
    print(f"\nShortlist: {OUT / 'shortlist.csv'}")
    print(f"All workbooks: {OUT / 'shortlist_all_workbooks.csv'}")


if __name__ == "__main__":
    main()
