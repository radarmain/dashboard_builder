"""List (default) or delete (--yes) leftover test projects in the test clients.

Dry run by default: prints every project in --clients that is NOT in --keep. With --yes it archives
and then permanently deletes exactly those projects, using the same code as the admin API
(app/lifecycle.py). It never touches a client that is not named in --clients, never deletes a
client, and never touches Cognito.

Usage: AWS_PROFILE=concrete AWS_REGION=us-east-2 \
       .venv/bin/python scripts/cleanup_test_data.py --keep smoke,tableau-import-test [--yes]
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
os.environ.setdefault("AWS_PROFILE", "concrete")
os.environ.setdefault("AWS_REGION", "us-east-2")
sys.path.insert(0, str(ROOT / "backend"))

from app import lifecycle, store  # noqa: E402
from app.aws import s3  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.users import audit  # noqa: E402

ACTOR = "script:cleanup_test_data"


def csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def has_import_report(c: str, p: str) -> bool:
    prefix = f"{store.project_prefix(c, p)}imports/"
    pager = s3().get_paginator("list_objects_v2")
    for page in pager.paginate(Bucket=get_settings().data_bucket, Prefix=prefix):
        if any(o["Key"].endswith("/report.json") for o in page.get("Contents", [])):
            return True
    return False


def describe(c: str, p: dict) -> dict:
    slug = p["slug"]
    return {
        "client": c,
        "slug": slug,
        "name": p.get("name", slug),
        "views": len(store.list_views(c, slug)),
        "created": str(p.get("created_at", ""))[:10],
        "import_report": has_import_report(c, slug),
        "tableau_link": bool(store.get_project_config(c, slug).tableau_link),
        "state": lifecycle.state(p),
    }


def delete_project(allowed: list[str], c: str, slug: str) -> None:
    if c not in allowed:  # hard guard: only clients named on the command line
        raise SystemExit(f"refusing: client {c!r} is not in --clients")
    p = store.get_project(c, slug)
    if not p:
        return
    if lifecycle.state(p) == lifecycle.ACTIVE:
        lifecycle.set_archived(c, slug, None, ACTOR, True)
        audit(ACTOR, "archive_project", lifecycle.path_of(c, slug), c)
    counts = lifecycle.permanent_delete(c, slug, None, ACTOR)
    print(f"  deleted {c}/{slug}: {counts}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--clients", default="rdb-test,rdb-test-analyst")
    ap.add_argument("--keep", default="", help="comma-separated project slugs to keep")
    ap.add_argument("--yes", action="store_true", help="actually archive + permanently delete")
    args = ap.parse_args()
    clients, keep = csv(args.clients), set(csv(args.keep))
    if not clients:
        raise SystemExit("--clients is empty")

    todo: list[dict] = []
    for c in clients:
        if not store.get_client(c):
            print(f"(client {c} does not exist)")
            continue
        for p in sorted(store.list_projects(c), key=lambda x: x["slug"]):
            if p["slug"] not in keep:
                todo.append(describe(c, p))
    kept = sorted(keep)
    print(f"clients: {', '.join(clients)}   keeping projects: {', '.join(kept) or '(none)'}")
    print(f"{'client':<16} {'slug':<28} {'views':>5}  {'created':<10} report link state    name")
    for d in todo:
        print(
            f"{d['client']:<16} {d['slug']:<28} {d['views']:>5}  {d['created']:<10} "
            f"{'yes' if d['import_report'] else '-':<6} {'yes' if d['tableau_link'] else '-':<4} "
            f"{d['state']:<8} {d['name']}"
        )
    print(f"{len(todo)} project(s) would be archived and permanently deleted.")
    if not args.yes:
        print("Dry run. Re-run with --yes to delete them.")
        return
    for d in todo:
        delete_project(clients, d["client"], d["slug"])
    print("done")


if __name__ == "__main__":
    main()
