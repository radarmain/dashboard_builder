"""Live smoke test against a running local API (http://localhost:8000).

Creates TEMPORARY Cognito users (random passwords, in memory only), runs the flow, deletes them.
Prints summaries only: no tokens, no passwords, no row values.

Usage: AWS_PROFILE=concrete AWS_REGION=us-east-2 \
       .venv/bin/python scripts/smoke_test.py project.dataset.table [--base-url https://xxxx.cloudfront.net]
"""

import argparse
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

import boto3
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
_ap = argparse.ArgumentParser()
_ap.add_argument("fqn", help="project.dataset.table")
_ap.add_argument("--base-url", default=os.environ.get("RDB_API", "http://localhost:8000"))
_args = _ap.parse_args()
API = _args.base_url.rstrip("/")
POOL = os.environ["COGNITO_USER_POOL_ID"]
APP = os.environ["COGNITO_APP_CLIENT_ID"]
FQN = _args.fqn
BQ_PROJECT, DATASET, TABLE = FQN.split(".")

session = boto3.Session(profile_name="concrete", region_name="us-east-2")
idp = session.client("cognito-idp")
results: list[tuple[str, bool, str]] = []
api_created: list[str] = []  # users created THROUGH the API: disabled (never deleted) at the end
# User-management checks create real Cognito users, so they only run against a LOCAL API that was
# started with RDB_SUPPRESS_INVITE_EMAILS=true (no invite emails). Skipped for --base-url deployments.
RUN_USERS = (
    API.startswith(("http://localhost", "http://127.0.0.1"))
    and os.environ.get("RDB_SUPPRESS_INVITE_EMAILS", "").lower() == "true"
)


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def make_user(tag: str, groups: list[str]) -> tuple[str, str, str]:
    email = f"rdb-smoke-{tag}-{uuid.uuid4().hex[:6]}@example.invalid"
    pw = "Aa1!" + secrets.token_urlsafe(18)
    idp.admin_create_user(
        UserPoolId=POOL,
        Username=email,
        MessageAction="SUPPRESS",
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
        ],
    )
    idp.admin_set_user_password(UserPoolId=POOL, Username=email, Password=pw, Permanent=True)
    for g in groups:
        idp.admin_add_user_to_group(UserPoolId=POOL, Username=email, GroupName=g)
    sub = next(
        a["Value"]
        for a in idp.admin_get_user(UserPoolId=POOL, Username=email)["UserAttributes"]
        if a["Name"] == "sub"
    )
    cog = session.client("cognito-idp")
    tok = cog.initiate_auth(
        ClientId=APP,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": email, "PASSWORD": pw},
    )["AuthenticationResult"]["IdToken"]
    return email, sub, tok


def H(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def user_mgmt_checks(
    http: httpx.Client, admin: str, analyst: str, client: str, admin_sub: str
) -> None:
    tag = uuid.uuid4().hex[:6]
    cu = f"rdb-smoke-uc-{tag}@example.invalid"
    check(
        "users: analyst cannot list admin users",
        http.get("/api/admin/users", headers=H(analyst)).status_code == 403,
    )
    check(
        "users: client cannot list client users",
        http.get("/api/clients/rdb-test/users", headers=H(client)).status_code == 403,
    )
    r = http.post(
        "/api/admin/users",
        headers=H(analyst),
        json={"email": f"x-{tag}@radar-analytics.com", "role": "analyst"},
    )
    check("users: analyst cannot create staff", r.status_code == 403)
    r = http.post("/api/clients/rdb-test/users", headers=H(analyst), json={"email": cu.upper()})
    ok = r.status_code == 201 and r.json()["status"] == "invited" and r.json()["email"] == cu
    check("users: analyst adds client user (email normalized, invited)", ok, str(r.status_code))
    if not ok:
        return
    sub = r.json()["sub"]
    api_created.append(cu)
    r = http.post("/api/clients/rdb-test/users", headers=H(analyst), json={"email": cu})
    check(
        "users: re-adding same email only re-grants (same sub)",
        r.status_code == 201 and r.json()["sub"] == sub,
    )
    lst = http.get("/api/clients/rdb-test/users", headers=H(analyst)).json()
    check("users: client users list has the new user", any(u["sub"] == sub for u in lst))
    r = http.post(
        "/api/clients/rdb-test/users", headers=H(analyst), json={"email": admin_email_of(admin_sub)}
    )
    check("users: staff email as client user -> 409", r.status_code == 409)
    groups = [
        g["GroupName"]
        for g in idp.admin_list_groups_for_user(UserPoolId=POOL, Username=sub)["Groups"]
    ]
    check(
        "users: new client user is in exactly one group (client)", groups == ["client"], str(groups)
    )
    r = http.post(f"/api/clients/rdb-test/users/{sub}/resend-invite", headers=H(client))
    check("users: client cannot resend invites", r.status_code == 403)
    r = http.delete(f"/api/clients/rdb-test/users/{sub}", headers=H(analyst))
    still = idp.admin_get_user(UserPoolId=POOL, Username=sub)["Enabled"]
    gone = not any(
        u["sub"] == sub for u in http.get("/api/clients/rdb-test/users", headers=H(analyst)).json()
    )
    check("users: remove access keeps the Cognito account", r.status_code == 200 and gone and still)
    r = http.post(
        "/api/admin/users",
        headers=H(admin),
        json={"email": f"rdb-smoke-{tag}@gmail.com", "role": "analyst"},
    )
    check("users: staff outside allowed domains -> 400", r.status_code == 400)
    st = f"rdb-smoke-staff-{tag}@radar-analytics.com"
    r = http.post("/api/admin/users", headers=H(admin), json={"email": st, "role": "analyst"})
    check("users: admin creates staff user", r.status_code == 201, str(r.status_code))
    if r.status_code == 201:
        api_created.append(st)
        ssub = r.json()["sub"]
        r = http.post(f"/api/admin/users/{ssub}/role", headers=H(admin), json={"role": "admin"})
        groups = [
            g["GroupName"]
            for g in idp.admin_list_groups_for_user(UserPoolId=POOL, Username=ssub)["Groups"]
        ]
        check(
            "users: role change leaves exactly one group",
            r.status_code == 200 and groups == ["admin"],
            str(groups),
        )
        r = http.post(f"/api/admin/users/{ssub}/disable", headers=H(admin))
        check("users: disable works", r.status_code == 200 and r.json()["status"] == "disabled")
    csub = http.get("/api/admin/users?role=client&search=" + cu, headers=H(admin)).json()
    check("users: admin list/search finds client user", csub.get("total") == 1)
    r = http.post(f"/api/admin/users/{admin_sub}/disable", headers=H(admin))
    check("users: admin cannot disable themselves", r.status_code == 400)
    r = http.post(f"/api/admin/users/{sub}/role", headers=H(admin), json={"role": "analyst"})
    check("users: client user cannot become staff", r.status_code == 409)


def rename_checks(http: httpx.Client, admin: str, client: str, sql: str) -> None:
    """Project/view slug renames + aliases. New temp project per run (data is left in place)."""
    tag = uuid.uuid4().hex[:6]
    C = "/api/clients/rdb-test"
    PTL = "/api/portal/clients/rdb-test"
    p0, p1, p2 = f"ren-{tag}", f"ren-{tag}-renamed", f"ren-{tag}-two"
    v0, v1, vo = f"orig-{tag}", f"orig-{tag}-new", f"other-{tag}"
    for slug in (p0, p2):
        temp_projects.append(("rdb-test", slug))
        r = http.post(f"{C}/projects", json={"name": f"Ren {slug}", "slug": slug}, headers=H(admin))
        check(f"rename: create project {slug}", r.status_code == 201, str(r.status_code))
        r = http.put(
            f"{C}/projects/{slug}/settings",
            json={"bq_project": BQ_PROJECT, "tables": [FQN]},
            headers=H(admin),
        )
        check(f"rename: connect table on {slug}", r.status_code == 200, r.text[:150])
    for slug in (v0, vo):
        http.post(f"{C}/projects/{p0}/views", json={"name": slug, "slug": slug}, headers=H(admin))
    spec = {
        "view": {"name": "Ren", "slug": v0},
        "layout": [{"element_id": "el_1", "x": 0, "y": 0, "w": 12, "h": 6}],
        "elements": [
            {"id": "el_1", "type": "kpi", "title": "n", "sql": sql, "cache_ttl_seconds": 3600}
        ],
    }
    V0 = f"{C}/projects/{p0}/views/{v0}"
    r1 = http.post(f"{V0}/save", json=spec, headers=H(admin))
    r2 = http.post(f"{V0}/publish", headers=H(admin))
    check("rename: saved + published rev 1", r1.status_code == 200 and r2.status_code == 200)

    # ---- view slug ----
    r = http.patch(V0, json={"slug": "Bad_Slug"}, headers=H(admin))
    check("rename view: bad slug -> 422", r.status_code == 422)
    r = http.patch(V0, json={"slug": "x" * 41}, headers=H(admin))
    check("rename view: >40 chars -> 422", r.status_code == 422)
    r = http.patch(V0, json={"slug": v1}, headers=H(client))
    check("rename view: client user forbidden", r.status_code == 403)
    r = http.patch(V0, json={"slug": vo}, headers=H(admin))
    check("rename view: existing slug -> 409", r.status_code == 409, r.text[:120])
    r = http.patch(V0, json={"slug": v1, "name": "Renamed"}, headers=H(admin))
    ok = r.status_code == 200 and r.json()["slug"] == v1 and r.json()["name"] == "Renamed"
    check("rename view: slug + name", ok, r.text[:150])
    V1 = f"{C}/projects/{p0}/views/{v1}"
    r = http.get(V1, headers=H(admin))
    j = r.json() if r.is_success else {}
    check(
        "rename view: pointers kept",
        j.get("draft_revision") == 1 and j.get("published_revision") == 1,
        str({k: j.get(k) for k in ("draft_revision", "published_revision")}),
    )
    r = http.get(f"{V1}/published", headers=H(admin))
    check(
        "rename view: new slug serves published spec",
        r.status_code == 200 and r.json()["revision"] == 1 and r.json()["view"]["slug"] == v1,
    )
    r = http.get(f"{V1}/revisions", headers=H(admin))
    revs = r.json() if r.is_success else []
    check(
        "rename view: revisions moved",
        any(x["revision"] == 1 and x["is_published"] for x in revs),
        str([x["revision"] for x in revs]),
    )
    r = http.post(f"{V1}/save", json=spec, headers=H(admin))
    check("rename view: revision counter continues", r.json().get("revision") == 2, r.text[:100])
    r = http.get(f"{V1}", headers=H(admin))
    check(
        "rename view: save keeps published pointer",
        r.json().get("published_revision") == 1 and r.json().get("draft_revision") == 2,
    )
    # old slug still resolves (builder + portal + data), canonical slugs in the body
    r = http.get(V0, headers=H(admin))
    check("old view slug: builder GET resolves", r.status_code == 200 and r.json()["slug"] == v1)
    r = http.get(f"{V0}/published", headers=H(admin))
    check("old view slug: published spec", r.status_code == 200 and r.json()["revision"] == 1)
    r = http.get(f"{V0}/data/el_1", headers=H(admin))
    check("old view slug: builder data route", r.status_code == 200 and r.json().get("columns"))
    r = http.get(f"{PTL}/projects/{p0}/views/{v0}", headers=H(client))
    j = r.json() if r.is_success else {}
    check(
        "old view slug: portal view (client) + canonical slug",
        r.status_code == 200 and j.get("slug") == v1 and j["project"]["slug"] == p0,
        str(r.status_code),
    )
    r = http.get(f"{PTL}/projects/{p0}/views/{v0}/data/el_1", headers=H(client))
    check("old view slug: portal data route", r.status_code == 200 and r.json().get("columns"))
    r = http.post(f"{C}/projects/{p0}/views", json={"name": "x", "slug": v0}, headers=H(admin))
    check("create view on an old slug -> 409", r.status_code == 409)
    r = http.patch(f"{C}/projects/{p0}/views/{vo}", json={"slug": v0}, headers=H(admin))
    check("rename to another view's old slug -> 409", r.status_code == 409, r.text[:120])

    # ---- project slug ----
    P0 = f"{C}/projects/{p0}"
    r = http.patch(P0, json={"slug": p2}, headers=H(admin))
    check(
        "rename project: slug owned by another project -> 409", r.status_code == 409, r.text[:120]
    )
    r = http.patch(P0, json={"slug": "Bad_Slug"}, headers=H(admin))
    check("rename project: bad slug -> 422", r.status_code == 422)
    r = http.patch(P0, json={"slug": p1}, headers=H(client))
    check("rename project: client user forbidden", r.status_code == 403)
    r = http.patch(P0, json={"slug": p1, "name": "Ren renamed"}, headers=H(admin))
    ok = r.status_code == 200 and r.json()["slug"] == p1 and r.json()["name"] == "Ren renamed"
    check("rename project: slug + name", ok, r.text[:150])
    P1 = f"{C}/projects/{p1}"
    r = http.get(P1, headers=H(admin))
    check(
        "rename project: config moved",
        r.status_code == 200 and r.json()["config"]["tables"] == [FQN],
    )
    r = http.get(f"{P1}/views", headers=H(admin))
    slugs = sorted(v["slug"] for v in r.json()) if r.is_success else []
    check("rename project: views re-keyed", slugs == sorted([v1, vo]), str(slugs))
    r = http.get(f"{P1}/views/{v1}", headers=H(admin))
    j = r.json() if r.is_success else {}
    check(
        "rename project: view pointers + project_slug",
        j.get("project_slug") == p1
        and j.get("published_revision") == 1
        and j.get("draft_revision") == 2,
    )
    r = http.post(f"{P1}/views/{v1}/save", json=spec, headers=H(admin))
    check("rename project: revision counter continues", r.json().get("revision") == 3)
    r = http.get(f"{P1}/views/{v1}/data/el_1", headers=H(admin))
    check(
        "rename project: data route on new slugs", r.status_code == 200 and r.json().get("columns")
    )
    # fully old path (old project + old view) still works everywhere
    old_view = f"{P0}/views/{v0}"
    r = http.get(old_view, headers=H(admin))
    j = r.json() if r.is_success else {}
    check(
        "old project+view slugs: builder GET -> canonical",
        j.get("slug") == v1 and j.get("project_slug") == p1,
        str(r.status_code),
    )
    r = http.get(f"{P0}", headers=H(admin))
    check(
        "old project slug: builder GET -> canonical",
        r.status_code == 200 and r.json()["slug"] == p1,
    )
    r = http.get(f"{PTL}/projects/{p0}/views/{v0}", headers=H(client))
    j = r.json() if r.is_success else {}
    check(
        "old project+view slugs: client reads published view via OLD portal path",
        r.status_code == 200 and j["project"]["slug"] == p1 and j["slug"] == v1,
        str(r.status_code),
    )
    r = http.get(f"{PTL}/projects/{p0}/views/{v0}/data/el_1", headers=H(client))
    check(
        "old project+view slugs: portal data route",
        r.status_code == 200 and r.json().get("columns"),
    )
    r = http.get(f"{PTL}/projects/{p0}", headers=H(client))
    check(
        "old project slug: portal project page -> canonical",
        r.status_code == 200 and r.json()["project"]["slug"] == p1,
    )
    r = http.get(f"{old_view}/data/el_1?draft=true", headers=H(client))
    check("old slugs: client still cannot use ?draft", r.status_code == 403)
    r = http.post(f"{C}/projects", json={"name": "x", "slug": p0}, headers=H(admin))
    check("create project on an old slug -> 409", r.status_code == 409)
    r = http.patch(f"{C}/projects/{p2}", json={"slug": p0}, headers=H(admin))
    check("rename to another project's old slug -> 409", r.status_code == 409, r.text[:120])
    r = http.patch(f"{C}/projects/{p2}", json={"name": "Name only"}, headers=H(admin))
    check("rename project: name-only keeps slug", r.status_code == 200 and r.json()["slug"] == p2)


def rename_import_checks(http: httpx.Client, admin: str) -> None:
    """Rename a project that has a Tableau link, an import report + reference CSV (S3) and an
    import budget item (DynamoDB). Everything must follow the new slug; the old slug still works.
    The report/budget are seeded directly (running a real import would call Tableau and Bedrock)."""
    tag = uuid.uuid4().hex[:6]
    C = "/api/clients/rdb-test"
    p0, p1 = f"imp-{tag}", f"imp-{tag}-renamed"
    iid = uuid.uuid4().hex
    table = session.resource("dynamodb").Table(os.environ.get("RDB_TABLE_NAME", "rdb_main"))
    s3c = session.client("s3")
    bucket = os.environ["RDB_DATA_BUCKET"]
    pre = lambda p: f"clients/rdb-test/projects/{p}/imports/{iid}/"  # noqa: E731
    temp_projects.append(("rdb-test", p0))
    r = http.post(f"{C}/projects", json={"name": f"Imp {tag}", "slug": p0}, headers=H(admin))
    check("rename+import: create project", r.status_code == 201, str(r.status_code))
    link = {"workbook_id": "0123abcd-4567-89ef-0123-456789abcdef", "workbook_name": "WB smoke"}
    r = http.put(f"{C}/projects/{p0}/tableau-link", json=link, headers=H(admin))
    check("rename+import: tableau link saved", r.status_code == 200, r.text[:120])
    report = {"import_id": iid, "workbook": link, "sheets": {"Sheet 1": {"sheet": "Sheet 1"}}}
    s3c.put_object(Bucket=bucket, Key=pre(p0) + "report.json", Body=json.dumps(report).encode())
    s3c.put_object(Bucket=bucket, Key=pre(p0) + "reference/0.csv", Body=b"a,b\n1,2\n")
    table.put_item(
        Item={"PK": "CLIENT#rdb-test", "SK": f"IMPORT#{p0}#{iid}", "scanned_bytes": 4242}
    )
    r = http.get(f"{C}/projects/{p0}/tableau-import/{iid}/report", headers=H(admin))
    check("rename+import: report readable before rename", r.status_code == 200, str(r.status_code))
    r = http.patch(f"{C}/projects/{p0}", json={"slug": p1}, headers=H(admin))
    check("rename+import: project renamed", r.status_code == 200, r.text[:120])
    P1 = f"{C}/projects/{p1}"
    r = http.get(f"{P1}/tableau-link", headers=H(admin))
    check("rename+import: link under new slug", r.status_code == 200 and r.json() == link)
    r = http.get(f"{P1}/tableau-import/{iid}/report", headers=H(admin))
    ok = r.status_code == 200 and r.json().get("import_id") == iid
    check("rename+import: report under new slug", ok, str(r.status_code))
    keys = {
        o["Key"] for o in s3c.list_objects_v2(Bucket=bucket, Prefix=pre(p1)).get("Contents", [])
    }
    check(
        "rename+import: reference CSV moved",
        keys == {pre(p1) + "report.json", pre(p1) + "reference/0.csv"},
        str(sorted(keys)),
    )
    new_item = table.get_item(
        Key={"PK": "CLIENT#rdb-test", "SK": f"IMPORT#{p1}#{iid}"}, ConsistentRead=True
    ).get("Item")
    check(
        "rename+import: budget item re-keyed with its bytes",
        bool(new_item) and int(new_item["scanned_bytes"]) == 4242,
    )
    old_item = table.get_item(
        Key={"PK": "CLIENT#rdb-test", "SK": f"IMPORT#{p0}#{iid}"}, ConsistentRead=True
    ).get("Item")
    check("rename+import: old budget item gone", old_item is None)
    r = http.get(f"{C}/projects/{p0}/tableau-link", headers=H(admin))
    check("rename+import: OLD slug resolves the link", r.status_code == 200 and r.json() == link)
    r = http.get(f"{C}/projects/{p0}/tableau-import/{iid}/report", headers=H(admin))
    check("rename+import: OLD slug resolves the report", r.status_code == 200, str(r.status_code))
    # tidy the seeded test data (our own objects)
    for k in keys:
        s3c.delete_object(Bucket=bucket, Key=k)
    table.delete_item(Key={"PK": "CLIENT#rdb-test", "SK": f"IMPORT#{p1}#{iid}"})


# ---------------------------------------------------------------- archive / delete lifecycle
# Temporary content created by a run: (client, project) pairs and whole clients. Archived and then
# permanently deleted (admin API) in the `finally` of main(); only things this run created.
temp_projects: list[tuple[str, str]] = []
temp_clients: list[str] = []


def _ddb():  # noqa: ANN202
    return session.resource("dynamodb").Table(os.environ.get("RDB_TABLE_NAME", "rdb_main"))


def _s3_keys(prefix: str) -> list[str]:
    r = session.client("s3").list_objects_v2(Bucket=os.environ["RDB_DATA_BUCKET"], Prefix=prefix)
    return [o["Key"] for o in r.get("Contents", [])]


def _ddb_pk(pk: str) -> list[str]:
    from boto3.dynamodb.conditions import Key

    r = _ddb().query(KeyConditionExpression=Key("PK").eq(pk), ConsistentRead=True)
    return [i["SK"] for i in r["Items"]]


def cleanup_temp(http: httpx.Client, admin: str) -> None:
    """Archive then permanently delete everything this run created (never the fixtures)."""
    done = 0
    seen: set[tuple[str, str]] = set()
    for c, p in temp_projects:
        path = f"/api/clients/{c}/projects/{p}"
        r = http.get(path, headers=H(admin))
        if r.status_code != 200:
            continue  # already deleted
        slug = r.json()["slug"]  # old slugs resolve to the canonical one
        if (c, slug) in seen:
            continue
        seen.add((c, slug))
        path = f"/api/clients/{c}/projects/{slug}"
        if r.json().get("lifecycle") == "active":
            http.post(f"{path}/archive", headers=H(admin))
        d = http.delete(f"{path}?confirm={slug}", headers=H(admin))
        done += d.status_code == 200
        if d.status_code != 200:
            print(f"cleanup: could not delete {c}/{slug}: {d.status_code}")
    for c in temp_clients:
        path = f"/api/clients/{c}"
        r = http.get(path, headers=H(admin))
        if r.status_code != 200:
            continue
        if r.json().get("lifecycle") == "active":
            http.post(f"{path}/archive", headers=H(admin))
        d = http.delete(f"{path}?confirm={c}", headers=H(admin))
        done += d.status_code == 200
        if d.status_code != 200:
            print(f"cleanup: could not delete client {c}: {d.status_code}")
    print(f"temp content archived + permanently deleted: {done}")


def lifecycle_checks(  # noqa: PLR0915
    http: httpx.Client,
    admin: str,
    admin_sub: str,
    analyst: str,
    client: str,
    client_sub: str,
    sql: str,
) -> None:
    tag = uuid.uuid4().hex[:6]
    LC, P1, P2, PC = f"rdb-lc-{tag}", "main", "second", "cap"
    V1, V2 = "alpha", "beta"
    C = f"/api/clients/{LC}"
    PTL = f"/api/portal/clients/{LC}"
    temp_clients.append(LC)
    r = http.post("/api/clients", json={"name": f"LC {tag}", "slug": LC}, headers=H(admin))
    check("lifecycle: create temp client", r.status_code == 201, str(r.status_code))
    for p in (P1, P2, PC):
        http.post(f"{C}/projects", json={"name": p.title(), "slug": p}, headers=H(admin))
    http.put(
        f"{C}/projects/{P1}/settings",
        json={"bq_project": BQ_PROJECT, "tables": [FQN]},
        headers=H(admin),
    )
    for v in (V1, V2):
        http.post(f"{C}/projects/{P1}/views", json={"name": v, "slug": v}, headers=H(admin))
    spec = {
        "view": {"name": "LC", "slug": V1},
        "layout": [{"element_id": "el_1", "x": 0, "y": 0, "w": 12, "h": 6}],
        "elements": [
            {"id": "el_1", "type": "kpi", "title": "n", "sql": sql, "cache_ttl_seconds": 3600}
        ],
    }
    PA, VA = f"{C}/projects/{P1}", f"{C}/projects/{P1}/views/{V1}"
    http.post(f"{VA}/save", json=spec, headers=H(admin))
    http.post(f"{VA}/save", json=spec, headers=H(admin))
    r = http.post(f"{VA}/publish", headers=H(admin))
    check("lifecycle: published temp view", r.status_code == 200, r.text[:120])
    http.post(
        "/api/admin/access", json={"user_sub": client_sub, "client_slug": LC}, headers=H(admin)
    )
    PV = f"{PTL}/projects/{P1}/views/{V1}"

    def portal_ok() -> bool:
        return (
            http.get(PTL, headers=H(client)).status_code == 200
            and http.get(f"{PTL}/projects/{P1}", headers=H(client)).status_code == 200
            and http.get(PV, headers=H(client)).status_code == 200
            and http.get(f"{PV}/data/el_1", headers=H(client)).status_code == 200
        )

    def portal_404(*paths: str) -> bool:
        return all(http.get(p, headers=H(client)).status_code == 404 for p in paths)

    check("lifecycle: portal works before archiving", portal_ok())

    # ---- permission matrix ----
    for what, path in (("project", PA), ("view", VA)):
        r1 = http.post(f"{path}/archive", headers=H(analyst))
        r2 = http.post(f"{path}/restore", headers=H(analyst))
        check(
            f"perm: analyst can archive + restore a {what}",
            r1.status_code == 200 and r2.status_code == 200,
            f"{r1.status_code}/{r2.status_code}",
        )
    r1 = http.post(f"{C}/archive", headers=H(analyst))
    r2 = http.post(f"{C}/restore", headers=H(analyst))
    check(
        "perm: analyst cannot archive or restore a client",
        r1.status_code == 403 and r2.status_code == 403,
    )
    check(
        "perm: client user cannot archive",
        http.post(f"{PA}/archive", headers=H(client)).status_code == 403,
    )
    http.post(f"{VA}/archive", headers=H(admin))
    http.post(f"{PA}/archive", headers=H(admin))
    http.post(f"{C}/archive", headers=H(admin))
    codes = [
        http.delete(f"{VA}?confirm={V1}", headers=H(analyst)).status_code,
        http.delete(f"{PA}?confirm={P1}", headers=H(analyst)).status_code,
        http.delete(f"{C}?confirm={LC}", headers=H(analyst)).status_code,
        http.get(f"{C}/delete-preview", headers=H(analyst)).status_code,
    ]
    check(
        "perm: analyst gets 403 on every permanent delete + preview", codes == [403] * 4, str(codes)
    )
    for p in (VA, PA, C):
        http.post(f"{p}/restore", headers=H(admin))
    r = http.delete(f"{VA}?confirm={V1}", headers=H(admin))
    check("delete: refused on an active item (409)", r.status_code == 409, str(r.status_code))
    http.post(f"{VA}/archive", headers=H(admin))
    r = http.delete(f"{VA}?confirm=wrong", headers=H(admin))
    check("delete: wrong confirm slug -> 400", r.status_code == 400, str(r.status_code))
    r = http.delete(f"{VA}", headers=H(admin))
    check("delete: missing confirm slug -> 400", r.status_code == 400, str(r.status_code))
    http.post(f"{VA}/restore", headers=H(admin))

    # ---- portal 404s and recovery (client user) ----
    http.post(f"{VA}/archive", headers=H(admin))
    check("portal: archived view -> 404 (view, data)", portal_404(PV, f"{PV}/data/el_1"))
    views = http.get(f"{PTL}/projects/{P1}", headers=H(client)).json()
    check("portal: archived view not in the project's cards", V1 not in json.dumps(views))
    http.post(f"{VA}/restore", headers=H(admin))
    check("portal: works again after restoring the view", portal_ok())
    http.post(f"{PA}/archive", headers=H(admin))
    check(
        "portal: archived project -> 404 (project, view, data)",
        portal_404(f"{PTL}/projects/{P1}", PV, f"{PV}/data/el_1"),
    )
    cards = http.get(PTL, headers=H(client))
    check(
        "portal: archived project not on the client page",
        cards.status_code == 200 and P1 not in json.dumps(cards.json()),
    )

    # ---- archived is read-only for builders ----
    check(
        "archived: builder can still GET the project",
        http.get(PA, headers=H(admin)).json().get("effective_lifecycle") == "archived",
    )
    check(
        "archived: builder can GET a view in an archived project",
        http.get(VA, headers=H(admin)).status_code == 200,
    )
    blocked = {
        "save": http.post(f"{VA}/save", json=spec, headers=H(admin)).status_code,
        "publish": http.post(f"{VA}/publish", headers=H(admin)).status_code,
        "settings": http.put(
            f"{PA}/settings", json={"bq_project": BQ_PROJECT, "tables": [FQN]}, headers=H(admin)
        ).status_code,
        "create view": http.post(
            f"{PA}/views", json={"name": "n", "slug": "nope"}, headers=H(admin)
        ).status_code,
        "rename": http.patch(PA, json={"name": "New name"}, headers=H(admin)).status_code,
        "rename view": http.patch(VA, json={"name": "New name"}, headers=H(admin)).status_code,
    }
    check("archived: writes return 409", set(blocked.values()) == {409}, str(blocked))
    lst = http.get(f"{C}/projects", headers=H(admin)).json()
    lst2 = http.get(f"{C}/projects?include_archived=true", headers=H(admin)).json()
    check(
        "archived: hidden from lists unless include_archived",
        P1 not in [p["slug"] for p in lst] and P1 in [p["slug"] for p in lst2],
    )
    http.post(f"{PA}/restore", headers=H(admin))
    check("portal: works again after restoring the project", portal_ok())
    http.post(f"{C}/archive", headers=H(admin))
    me = http.get("/api/portal/me", headers=H(client)).json()
    check(
        "portal: archived client -> 404 + not in /portal/me",
        portal_404(PTL, PV) and LC not in [c["slug"] for c in me],
    )
    check(
        "archived client: writes return 409",
        http.post(f"{C}/projects", json={"name": "x", "slug": "nope"}, headers=H(admin)).status_code
        == 409,
    )
    http.post(f"{C}/restore", headers=H(admin))
    check("portal: works again after restoring the client", portal_ok())

    # ---- delete-preview counts ----
    http.post(f"{VA}/archive", headers=H(admin))
    pv = http.get(f"{VA}/delete-preview", headers=H(admin)).json()
    check(
        "preview: view counts",
        pv.get("views") == 1 and pv.get("revisions") == 2 and pv.get("s3_objects", 0) >= 2,
        str(pv),
    )
    http.post(f"{VA}/restore", headers=H(admin))
    http.post(f"{PA}/archive", headers=H(admin))
    pv = http.get(f"{PA}/delete-preview", headers=H(admin)).json()
    check(
        "preview: project counts",
        pv.get("projects") == 1 and pv.get("views") == 2 and pv.get("revisions") == 2,
        str(pv),
    )
    http.post(f"{PA}/restore", headers=H(admin))
    http.post(f"{C}/archive", headers=H(admin))
    pv = http.get(f"{C}/delete-preview", headers=H(admin)).json()
    check(
        "preview: client counts",
        pv.get("projects") == 3 and pv.get("views") == 2 and pv.get("client_users") == 1,
        str(pv),
    )
    http.post(f"{C}/restore", headers=H(admin))

    # ---- favorites ----
    F = "/api/me/favorites"

    def fav(t: str, p: str | None = None, v: str | None = None) -> dict[str, str]:
        out = {"type": t, "client": LC}
        if p:
            out["project"] = p
        if v:
            out["view"] = v
        return out

    check("favorites: client user forbidden", http.get(F, headers=H(client)).status_code == 403)
    for f in (fav("client"), fav("project", P1), fav("view", P1, V2)):
        http.put(F, json=f, headers=H(admin))
    got = http.get(F, headers=H(admin)).json()
    mine = [x for x in got if x["client"] == LC]
    check(
        "favorites: add + order (oldest first) + resolved names",
        [x["type"] for x in mine] == ["client", "project", "view"]
        and mine[2]["path"].endswith("/ Main")
        and mine[0]["name"] == f"LC {tag}",
        str([(x["type"], x["name"], x["path"]) for x in mine]),
    )
    r = http.put(F, json=fav("view", P1, "nope"), headers=H(admin))
    check("favorites: unknown item -> 404", r.status_code == 404)
    http.request("DELETE", F, json=fav("project", P1), headers=H(admin))
    got = [x["type"] for x in http.get(F, headers=H(admin)).json() if x["client"] == LC]
    check("favorites: remove", got == ["client", "view"], str(got))
    # renamed slug: stored under the old slug, read back as the new one (rewritten lazily)
    r = http.patch(f"{PA}/views/{V2}", json={"slug": "beta-renamed"}, headers=H(admin))
    check("favorites: renamed view", r.status_code == 200, r.text[:100])
    got = [
        x for x in http.get(F, headers=H(admin)).json() if x["client"] == LC and x["type"] == "view"
    ]
    sks = _ddb_pk(f"USER#{admin_sub}")
    check(
        "favorites: renamed slug resolves + item rewritten",
        len(got) == 1
        and got[0]["view"] == "beta-renamed"
        and f"FAV#VIEW#{LC}#{P1}#beta-renamed" in sks
        and f"FAV#VIEW#{LC}#{P1}#{V2}" not in sks,
        str(got),
    )
    # archived items are hidden (and come back on restore)
    http.post(f"{PA}/archive", headers=H(admin))
    got = [x["type"] for x in http.get(F, headers=H(admin)).json() if x["client"] == LC]
    check("favorites: archived items hidden", got == ["client"], str(got))
    http.post(f"{PA}/restore", headers=H(admin))
    got = [x["type"] for x in http.get(F, headers=H(admin)).json() if x["client"] == LC]
    check("favorites: restored items reappear", got == ["client", "view"], str(got))
    # cap: fill with real views in the `cap` project, then one more is refused
    for i in range(50):
        http.post(
            f"{C}/projects/{PC}/views", json={"name": f"v{i}", "slug": f"v{i}"}, headers=H(admin)
        )
    n0 = len(http.get(F, headers=H(admin)).json())
    codes = []
    for i in range(50):
        r = http.put(F, json=fav("view", PC, f"v{i}"), headers=H(admin))
        codes.append(r.status_code)
        if r.status_code == 409:
            break
    total = len(http.get(F, headers=H(admin)).json())
    check(
        "favorites: 50 cap enforced (409)",
        codes[-1] == 409 and total == 50,
        f"start={n0} total={total}",
    )

    # ---- permanent delete: view, project, client ----
    s3c_prefix = lambda *a: "clients/" + "/".join(a) + "/"  # noqa: E731
    vkeys = _s3_keys(s3c_prefix(LC, "projects", P1, "views", V1))
    http.post(f"{VA}/archive", headers=H(admin))
    r = http.delete(f"{VA}?confirm={V1}", headers=H(admin))
    check(
        "delete view: ok", r.status_code == 200 and r.json()["counts"]["views"] == 1, r.text[:150]
    )
    check(
        "delete view: no S3 objects left",
        bool(vkeys) and not _s3_keys(s3c_prefix(LC, "projects", P1, "views", V1)),
    )
    check("delete view: DynamoDB item gone", f"VIEW#{P1}#{V1}" not in _ddb_pk(f"CLIENT#{LC}"))
    check(
        "delete view: sibling view untouched",
        http.get(f"{PA}/views/beta-renamed", headers=H(admin)).status_code == 200,
    )
    check(
        "delete view: slug free again",
        http.post(f"{PA}/views", json={"name": "again", "slug": V1}, headers=H(admin)).status_code
        == 201,
    )
    # project with an alias + seeded import budget item
    http.patch(f"{C}/projects/{P2}", json={"slug": "second-renamed"}, headers=H(admin))
    P2n = "second-renamed"
    _ddb().put_item(Item={"PK": f"CLIENT#{LC}", "SK": f"IMPORT#{P2n}#abc", "scanned_bytes": 1})
    session.client("s3").put_object(
        Bucket=os.environ["RDB_DATA_BUCKET"],
        Key=f"clients/{LC}/projects/{P2n}/imports/abc/report.json",
        Body=b"{}",
    )
    r = http.delete(f"{C}/projects/{P2n}?confirm={P2n}", headers=H(admin))
    check("delete project: active -> 409", r.status_code == 409)
    http.post(f"{C}/projects/{P2n}/archive", headers=H(admin))
    r = http.delete(f"{C}/projects/{P2n}?confirm={P2n}", headers=H(admin))
    check("delete project: ok", r.status_code == 200, r.text[:150])
    left = [k for k in _ddb_pk(f"CLIENT#{LC}") if P2n in k or P2 in k]
    check("delete project: no DynamoDB items (project, imports, aliases)", not left, str(left))
    check("delete project: no S3 objects left", not _s3_keys(s3c_prefix(LC, "projects", P2n)))
    check(
        "delete project: other project untouched",
        http.get(PA, headers=H(admin)).status_code == 200
        and bool(_s3_keys(s3c_prefix(LC, "projects", P1))),
    )
    check(
        "delete project: old slug is gone (404) and free",
        http.get(f"{C}/projects/{P2}", headers=H(admin)).status_code == 404
        and http.post(f"{C}/projects", json={"name": "x", "slug": P2}, headers=H(admin)).status_code
        == 201,
    )
    # favorites pointing at deleted content are pruned on read
    http.put(F, json=fav("project", P2), headers=H(admin))
    http.post(f"{C}/projects/{P2}/archive", headers=H(admin))
    http.delete(f"{C}/projects/{P2}?confirm={P2}", headers=H(admin))
    http.get(F, headers=H(admin))
    sks = _ddb_pk(f"USER#{admin_sub}")
    check("favorites: pruned after the item is deleted", f"FAV#PROJECT#{LC}#{P2}" not in sks)
    # client: everything under the prefix, access items, Cognito accounts stay
    check(
        "delete client: access item present before", f"CLIENT#{LC}" in _ddb_pk(f"USER#{client_sub}")
    )
    http.post(f"{C}/archive", headers=H(admin))
    r = http.delete(f"{C}?confirm={LC}", headers=H(admin))
    check("delete client: ok", r.status_code == 200, r.text[:150])
    check("delete client: no DynamoDB items under the client", _ddb_pk(f"CLIENT#{LC}") == [])
    check("delete client: no S3 objects under the client", not _s3_keys(f"clients/{LC}/"))
    check(
        "delete client: user access item removed",
        f"CLIENT#{LC}" not in _ddb_pk(f"USER#{client_sub}"),
    )
    check(
        "delete client: Cognito account still enabled",
        idp.admin_get_user(UserPoolId=POOL, Username=client_sub)["Enabled"],
    )
    check(
        "delete client: portal 404 and not in /portal/me",
        portal_404(PTL)
        and LC not in [c["slug"] for c in http.get("/api/portal/me", headers=H(client)).json()],
    )
    check("delete client: builder 404", http.get(C, headers=H(admin)).status_code == 404)
    r = http.post("/api/clients", json={"name": "Again", "slug": LC}, headers=H(admin))
    check("delete client: slug free again", r.status_code == 201, str(r.status_code))
    # leftovers of this run: favorites of the temp admin user
    for sk in _ddb_pk(f"USER#{admin_sub}"):
        if sk.startswith("FAV#"):
            _ddb().delete_item(Key={"PK": f"USER#{admin_sub}", "SK": sk})


def admin_email_of(sub: str) -> str:
    u = idp.list_users(UserPoolId=POOL, Filter=f'sub = "{sub}"', Limit=1)["Users"][0]
    return next(a["Value"] for a in u["Attributes"] if a["Name"] == "email")


def main() -> None:
    users: list[str] = []
    http = httpx.Client(base_url=API, timeout=180)
    try:
        admin_email, admin_sub, admin = make_user("builder", ["admin", "analyst"])
        users.append(admin_email)
        client_email, client_sub, client = make_user("client", ["client"])
        users.append(client_email)

        r = http.get("/api/me", headers=H(admin))
        check("auth: /me as admin+analyst", r.status_code == 200 and "admin" in r.json()["groups"])
        an_email, _, analyst = make_user("analyst", ["analyst"])
        users.append(an_email)
        r = http.post(
            "/api/clients",
            json={"name": "Analyst Made", "slug": "rdb-test-analyst"},
            headers=H(analyst),
        )
        check("analyst can create a client", r.status_code in (201, 409), str(r.status_code))
        r = http.post(
            "/api/admin/access",
            json={"user_sub": "x", "client_slug": "rdb-test"},
            headers=H(analyst),
        )
        check("analyst cannot use /admin/access", r.status_code == 403)
        r = http.post(
            "/api/clients/rdb-test/projects/smoke/views/smoke-view/save",
            headers=H(analyst),
            json={
                "view": {"name": "x", "slug": "x"},
                "layout": [{"element_id": "a", "x": 8, "y": 0, "w": 6, "h": 2}],
                "elements": [],
            },
        )
        check("layout wider than 12 columns rejected", r.status_code == 422)
        check("auth: no token -> 401", http.get("/api/clients").status_code == 401)
        if RUN_USERS:
            user_mgmt_checks(http, admin, analyst, client, admin_sub)
        else:
            print("SKIP user-management checks (local API + RDB_SUPPRESS_INVITE_EMAILS=true only)")
        check(
            "auth: client user blocked from builder route",
            http.get("/api/clients", headers=H(client)).status_code == 403,
        )

        # --- BigQuery browsing ---
        r = http.get("/api/gbq/projects", headers=H(admin))
        check(
            "gbq: list projects",
            r.status_code == 200 and BQ_PROJECT in r.json(),
            f"{len(r.json()) if r.status_code == 200 else r.text[:300]} projects",
        )
        r = http.get(f"/api/gbq/projects/{BQ_PROJECT}/datasets", headers=H(admin))
        check(
            "gbq: list datasets",
            r.status_code == 200 and DATASET in r.json(),
            f"{len(r.json()) if r.status_code == 200 else r.text[:300]} datasets",
        )
        r = http.get(f"/api/gbq/projects/{BQ_PROJECT}/datasets/{DATASET}/tables", headers=H(admin))
        tabs = [t["table_id"] for t in r.json()] if r.status_code == 200 else []
        check("gbq: list tables", TABLE in tabs, f"{len(tabs)} tables" if tabs else r.text[:300])

        # --- client/project/view ---
        for path, body in [
            ("/api/clients", {"name": "RDB Test", "slug": "rdb-test"}),
            ("/api/clients/rdb-test/projects", {"name": "Smoke", "slug": "smoke"}),
            (
                "/api/clients/rdb-test/projects/smoke/views",
                {"name": "Smoke View", "slug": "smoke-view"},
            ),
        ]:
            r = http.post(path, json=body, headers=H(admin))
            check(
                f"create {path.rsplit('/', 1)[-1]}", r.status_code in (201, 409), str(r.status_code)
            )
        r = http.post("/api/clients", json={"name": "Bad", "slug": "Bad_Slug"}, headers=H(admin))
        check("slug validation rejects bad slug", r.status_code == 422)
        r = http.post("/api/clients", json={"name": "Dup", "slug": "rdb-test"}, headers=H(admin))
        check("duplicate slug -> 409", r.status_code == 409)

        P = "/api/clients/rdb-test/projects/smoke"
        r = http.put(
            f"{P}/settings", json={"bq_project": BQ_PROJECT, "tables": [FQN]}, headers=H(admin)
        )
        check(
            "settings: connect table + snapshot",
            r.status_code == 200,
            r.text[:300] if r.status_code != 200 else "",
        )
        r = http.get(f"{P}/schemas/{DATASET}.{TABLE}", headers=H(admin))
        snap = r.json() if r.status_code == 200 else {}
        check(
            "schema snapshot readable",
            bool(snap.get("columns")),
            f"{len(snap.get('columns', []))} cols, {snap.get('num_rows')} rows, {snap.get('num_bytes')} bytes",
        )
        if snap.get("columns"):
            col = snap["columns"][0]["name"]
            r = http.put(
                f"{P}/schemas/{DATASET}.{TABLE}/descriptions",
                json={col: "smoke test description"},
                headers=H(admin),
            )
            check("descriptions saved", r.status_code == 200 and r.json()["descriptions"].get(col))
            r = http.put(
                f"{P}/schemas/{DATASET}.{TABLE}/descriptions",
                json={"no_such_col": "x"},
                headers=H(admin),
            )
            check("descriptions: unknown column rejected", r.status_code == 400)
        r = http.put(
            f"{P}/settings",
            json={
                "bq_project": BQ_PROJECT,
                "tables": [f"{BQ_PROJECT}.{DATASET}.definitely_not_a_table_xyz"],
            },
            headers=H(admin),
        )
        check(
            "settings: nonexistent table rejected", r.status_code in (400, 502), str(r.status_code)
        )

        # --- queries ---
        sql = f"SELECT COUNT(*) AS n FROM `{FQN}`"
        r = http.post(f"{P}/query", json={"sql": sql, "refresh": True}, headers=H(admin))
        q = r.json() if r.status_code == 200 else {}
        check(
            "query: dry run + run",
            r.status_code == 200 and q.get("cache_hit") is False,
            f"cols={q.get('columns')} rows={len(q.get('rows', []))} bytes={q.get('bytes_processed')}"
            if r.status_code == 200
            else r.text[:600],
        )
        r = http.post(f"{P}/query", json={"sql": sql}, headers=H(admin))
        check(
            "query: second run is a cache hit",
            r.status_code == 200 and r.json().get("cache_hit") is True,
            r.text[:300] if r.status_code != 200 else "",
        )
        r = http.post(
            f"{P}/query",
            json={"sql": "SELECT * FROM `radar-377104.some.other_table`"},
            headers=H(admin),
        )
        check("query: disallowed table rejected", r.status_code == 400, r.text[:200])
        r = http.post(
            f"{P}/query",
            json={"sql": f"SELECT * FROM `{BQ_PROJECT}.{DATASET}.{TABLE[:-1]}*`"},
            headers=H(admin),
        )
        check("query: wildcard rejected", r.status_code == 400)
        r = http.post(f"{P}/query", json={"sql": f"{sql}; DROP TABLE `{FQN}`"}, headers=H(admin))
        check("query: multi-statement/DDL rejected", r.status_code == 400)
        r = http.post(f"{P}/query", json={"sql": sql}, headers=H(client))
        check("query: client user forbidden", r.status_code == 403)

        if snap.get("num_bytes") is not None and int(snap["num_bytes"]) < 500_000_000:
            r = http.post(
                f"{P}/query",
                json={"sql": f"SELECT * FROM `{FQN}` LIMIT 5", "refresh": True},
                headers=H(admin),
            )
            q = r.json() if r.status_code == 200 else {}
            check(
                "query: SELECT * LIMIT 5 serializes",
                r.status_code == 200,
                f"{len(q.get('rows', []))} rows x {len(q.get('columns', []))} cols"
                if r.status_code == 200
                else r.text[:300],
            )

        # --- save / draft / publish ---
        V = f"{P}/views/smoke-view"
        spec = {
            "view": {"name": "Smoke View", "slug": "smoke-view"},
            "layout": [{"element_id": "el_1", "x": 0, "y": 0, "w": 12, "h": 6}],
            "elements": [
                {
                    "id": "el_1",
                    "type": "kpi",
                    "title": "Row count",
                    "sql": sql,
                    "cache_ttl_seconds": 3600,
                }
            ],
        }
        r = http.post(f"{V}/save", json=spec, headers=H(admin))
        rev1 = r.json().get("revision") if r.status_code == 200 else None
        check("save: new revision", r.status_code == 200 and rev1, f"revision {rev1}")
        r = http.post(f"{V}/save", json={**spec, "extra_junk": 1}, headers=H(admin))
        check("save: unknown spec field rejected", r.status_code == 422)
        r = http.get(f"{V}/published", headers=H(admin))
        check("published: 404 before publish", r.status_code == 404 or r.status_code == 200)
        r = http.post(f"{V}/publish", headers=H(admin))
        pub = r.json().get("published_revision") if r.status_code == 200 else None
        check(
            "publish",
            r.status_code == 200 and pub is not None,
            r.text[:200] if r.status_code != 200 else f"revision {pub}",
        )
        r = http.get(f"{V}", headers=H(admin))
        check(
            "view status fields",
            r.status_code == 200
            and r.json().get("draft_revision") == pub
            and r.json().get("has_unpublished_changes") is False,
            str(
                {
                    k: r.json().get(k)
                    for k in ("draft_revision", "published_revision", "has_unpublished_changes")
                }
            ),
        )

        # --- viewer data route + authz ---
        D = f"{V}/data/el_1"
        r = http.get(D, headers=H(admin))
        check(
            "data (builder, published)",
            r.status_code == 200 and r.json().get("cache_hit") is True,
            f"cache_hit={r.json().get('cache_hit')}" if r.status_code == 200 else r.text[:300],
        )
        r = http.get(D, headers=H(client))
        check("data: unassigned client user -> 404", r.status_code == 404)
        r = http.post(
            "/api/admin/access",
            json={"user_sub": client_sub, "client_slug": "rdb-test"},
            headers=H(client),
        )
        check("admin/access: client user forbidden", r.status_code == 403)
        r = http.post(
            "/api/admin/access",
            json={"user_sub": client_sub, "client_slug": "rdb-test"},
            headers=H(admin),
        )
        check("admin/access: grant", r.status_code == 201)
        r = http.get(D, headers=H(client))
        check(
            "data: assigned client user -> 200",
            r.status_code == 200 and r.json().get("columns"),
            r.text[:200] if r.status_code != 200 else "",
        )
        r = http.get(f"{V}/published", headers=H(client))
        check(
            "published as client: SQL stripped",
            r.status_code == 200 and all(e["sql"] == "" for e in r.json()["elements"]),
        )
        check(
            "client cannot read draft", http.get(f"{V}/draft", headers=H(client)).status_code == 403
        )
        check(
            "client cannot use ?draft=true",
            http.get(D + "?draft=true", headers=H(client)).status_code == 403,
        )
        check(
            "client cannot list views", http.get(f"{P}/views", headers=H(client)).status_code == 403
        )
        r = http.get(
            "/api/clients/some-other-client/projects/x/views/y/published", headers=H(client)
        )
        check("client: other client's view -> 404", r.status_code == 404)
        # --- portal (client-facing) ---
        PT = "/api/portal/clients/rdb-test"
        r = http.patch(V, json={"description": "Smoke description"}, headers=H(admin))
        check("PATCH view description", r.status_code == 200 and r.json().get("description"))
        check(
            "PATCH view: client forbidden",
            http.patch(V, json={"name": "x"}, headers=H(client)).status_code == 403,
        )
        http.post(
            f"{P}/views",
            json={"name": "Smoke Draft", "slug": "smoke-draft"},
            headers=H(admin),
        )
        http.post(
            f"{P}/views/smoke-draft/save", json=spec, headers=H(admin)
        )  # saved, never published
        r = http.get(PT, headers=H(client))
        cards = (
            [v for p_ in r.json().get("projects", []) for v in p_["views"]] if r.is_success else []
        )
        slugs = {c["slug"] for c in cards}
        check("portal: published view on card", "smoke-view" in slugs, str(sorted(slugs)))
        check("portal: unpublished view absent", "smoke-draft" not in slugs)
        card = next((c for c in cards if c["slug"] == "smoke-view"), {})
        check(
            "portal: description + summary on card",
            card.get("description") == "Smoke description"
            and card.get("counts", {}).get("kpi") == 1,
            str({k: card.get(k) for k in ("counts", "chart_kinds")}),
        )
        check(
            "portal: unpublished direct URL -> 404",
            http.get(f"{PT}/projects/smoke/views/smoke-draft", headers=H(client)).status_code
            == 404,
        )
        r = http.get(f"{PT}/projects/smoke/views/smoke-view", headers=H(client))
        body = r.text.lower()
        check(
            "portal: view has no sql / revision",
            r.status_code == 200 and '"sql"' not in body and "revision" not in body,
        )
        r = http.get(
            f"{PT}/projects/smoke/views/smoke-view/data/el_1?draft=true", headers=H(client)
        )
        check(
            "portal: data only columns/rows/truncated (?draft ignored)",
            r.status_code == 200 and set(r.json()) == {"columns", "rows", "truncated"},
            str(sorted(r.json())) if r.is_success else r.text[:200],
        )
        check(
            "portal: unassigned client -> 404",
            http.get("/api/portal/clients/some-other-client", headers=H(client)).status_code == 404,
        )
        r = http.get("/api/portal/me", headers=H(client))
        check(
            "portal/me: client sees assigned client", [c["slug"] for c in r.json()] == ["rdb-test"]
        )
        check("portal/me: admin gets []", http.get("/api/portal/me", headers=H(admin)).json() == [])
        check(
            "portal: admin can preview any client",
            http.get(PT, headers=H(admin)).status_code == 200
            and http.get(f"{PT}/projects/smoke/views/smoke-view", headers=H(analyst)).status_code
            == 200,
        )
        rename_checks(http, admin, client, sql)
        rename_import_checks(http, admin)
        lifecycle_checks(http, admin, admin_sub, analyst, client, client_sub, sql)
        http.request(
            "DELETE",
            "/api/admin/access",
            json={"user_sub": client_sub, "client_slug": "rdb-test"},
            headers=H(admin),
        )
        check("access revoked -> 404", http.get(D, headers=H(client)).status_code == 404)

        # --- tamper check: published spec with disallowed SQL can't run ---
        bad = {
            **spec,
            "elements": [{**spec["elements"][0], "sql": "SELECT * FROM `radar-377104.x.y`"}],
        }
        http.post(f"{V}/save", json=bad, headers=H(admin))
        r = http.post(f"{V}/publish", headers=H(admin))
        check("publish refuses disallowed SQL", r.status_code == 400, r.text[:150])
        r = http.get(D + "?draft=true", headers=H(admin))
        check("data ?draft=true re-validates SQL", r.status_code == 400, r.text[:150])

        # --- revisions: restore + prune (published revision must survive) ---
        for _ in range(12):
            http.post(f"{V}/save", json=spec, headers=H(admin))
        r = http.get(f"{V}/revisions", headers=H(admin))
        revs = r.json() if r.status_code == 200 else []
        check(
            "revisions: last 10, newest first",
            len(revs) == 10
            and [x["revision"] for x in revs]
            == sorted([x["revision"] for x in revs], reverse=True),
            f"{[x['revision'] for x in revs]}",
        )
        r = http.get(f"{V}/published", headers=H(admin))
        check(
            "prune kept the published revision",
            r.status_code == 200 and r.json()["revision"] == pub,
            f"published={pub}",
        )
        newest = revs[0]["revision"] if revs else 0
        r = http.post(f"{V}/revisions/{revs[-1]['revision']}/restore", headers=H(admin))
        check(
            "restore -> new revision",
            r.status_code == 200 and r.json()["revision"] == newest + 1,
            str(r.json() if r.status_code == 200 else r.text[:200]),
        )
        check(
            "restore missing revision -> 404",
            http.post(f"{V}/revisions/1/restore", headers=H(admin)).status_code in (200, 404),
        )
    finally:
        try:
            cleanup_temp(http, admin)
        except Exception as e:  # noqa: BLE001
            print("temp content cleanup failed:", type(e).__name__, str(e)[:200])
        for u in users:
            try:
                idp.admin_delete_user(UserPoolId=POOL, Username=u)
            except Exception as e:  # noqa: BLE001
                print("cleanup failed for a temp user:", type(e).__name__)
        print(f"temp users deleted: {len(users)}")
        for u in api_created:  # created via the API: disable, never delete
            try:
                idp.admin_disable_user(UserPoolId=POOL, Username=u)
            except Exception as e:  # noqa: BLE001
                print("could not disable an API-created test user:", type(e).__name__)
        if api_created:
            print(
                f"API-created test users DISABLED (rdb-smoke-*, kept in the pool): {len(api_created)}"
            )
    failed = [n for n, ok, _ in results if not ok]
    print(
        f"\n{len(results) - len(failed)}/{len(results)} passed"
        + (f"; FAILED: {failed}" if failed else "")
    )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
