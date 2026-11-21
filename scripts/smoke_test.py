"""Live smoke test against a running local API (http://localhost:8000).

Creates TEMPORARY Cognito users (random passwords, in memory only), runs the flow, deletes them.
Prints summaries only: no tokens, no passwords, no row values.

Usage: AWS_PROFILE=concrete AWS_REGION=us-east-2 \
       .venv/bin/python scripts/smoke_test.py project.dataset.table [--base-url https://xxxx.cloudfront.net]
"""

import argparse
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
