"""READ-ONLY Tableau Cloud client (plain REST + Metadata API GraphQL over httpx).

Safety:
- `_request` is the ONLY network call. It allows GET, plus POST for exactly three things:
  sign-in, sign-out, and read-only GraphQL (queries only; "mutation" is refused).
  There is no code path for publish/update/delete/refresh/permission calls.
- A personal access token supports ONE active session. Everything runs inside `session()`:
  a process-wide lock + a short DynamoDB lock item (so Lambda instances / local scripts don't
  collide), sign in, work, sign out. A sign-in conflict or 401 is retried once.
- 401 / auth rejection records the `tableau_auth` system alert; a later sign-in clears it.
- The token (secret and session token) is redacted from every error that leaves this module.
"""

import contextlib
import json
import re
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from app import alerts
from app.tableau_config import TableauConfig, load_config, redact, register_secret

LOCK_SK = "LOCK#tableau"
LOCK_TTL_S = 300  # 5 minutes; a crashed holder can block others for at most this long
_UUID = re.compile(r"^[0-9a-fA-F-]{8,64}$")


class TableauError(Exception):
    """Message is already redacted; safe to show to builder users."""


class TableauNotConfigured(TableauError):
    pass


class TableauAuthError(TableauError):
    pass


class TableauBusy(TableauError):
    pass


# ---------- cross-process lock (DynamoDB item with TTL; best effort) ----------
@contextmanager
def _ddb_lock(ttl_s: int = LOCK_TTL_S, wait_s: int = 25) -> Iterator[None]:
    owner = uuid.uuid4().hex
    table = None
    try:
        from app.aws import ddb_table

        table = ddb_table()
        deadline = time.time() + wait_s
        while True:
            now = int(time.time())
            try:
                table.put_item(
                    Item={"PK": "SYSTEM", "SK": LOCK_SK, "owner": owner, "expires_at": now + ttl_s},
                    ConditionExpression="attribute_not_exists(PK) OR expires_at < :n",
                    ExpressionAttributeValues={":n": now},
                )
                break
            except Exception as e:  # noqa: BLE001
                if getattr(e, "response", {}).get("Error", {}).get("Code") != (
                    "ConditionalCheckFailedException"
                ):
                    table = None  # DynamoDB unavailable: fall back to the process lock only
                    break
                if time.time() > deadline:
                    raise TableauBusy(
                        "Tableau is busy (another session is active); retry shortly"
                    ) from None
                time.sleep(1)
    except TableauBusy:
        raise
    except Exception:  # noqa: BLE001
        table = None
    try:
        yield
    finally:
        if table is not None:
            with contextlib.suppress(Exception):
                table.delete_item(
                    Key={"PK": "SYSTEM", "SK": LOCK_SK},
                    ConditionExpression="#o = :o",
                    ExpressionAttributeNames={"#o": "owner"},
                    ExpressionAttributeValues={":o": owner},
                )


def _best_effort(fn: Any) -> None:
    with contextlib.suppress(Exception):
        fn()


# ---------- the session ----------
class Session:
    def __init__(self, cfg: TableauConfig, per_call: bool = False) -> None:
        self.cfg = cfg
        self.per_call = per_call  # True: take the lock around EACH http call (long scripts)
        self.http = httpx.Client(timeout=90, headers={"Accept": "application/json"})
        self.api = "3.22"
        self.token: str | None = None
        self.site_id: str | None = None

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if not self.per_call or getattr(_depth, "held", False):
            yield
            return
        with _PROC_LOCK, _ddb_lock(LOCK_TTL_S, wait_s=180):
            _depth.held = True
            try:
                yield
            finally:
                _depth.held = False

    # -- the single choke point --
    def _request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        params: dict | None = None,
        auth: bool = True,
    ) -> httpx.Response:
        is_auth = url.endswith("/auth/signin") or url.endswith("/auth/signout")
        is_gql = url.endswith("/api/metadata/graphql")
        if method == "POST" and not (is_auth or is_gql):
            raise TableauError("write calls are not allowed")
        if method not in ("GET", "POST"):
            raise TableauError("write calls are not allowed")
        if is_gql and "mutation" in str(json).lower():
            raise TableauError("write calls are not allowed")
        headers = {"X-Tableau-Auth": self.token} if (auth and self.token) else {}
        try:
            with self._locked():
                return self.http.request(method, url, json=json, params=params, headers=headers)
        except httpx.HTTPError as e:
            raise TableauError(
                redact(f"Tableau request failed: {type(e).__name__}", self.cfg)
            ) from None

    def _fail(self, r: httpx.Response, what: str) -> TableauError:
        msg = redact(f"{what}: HTTP {r.status_code} {r.text[:200]}", self.cfg)
        return TableauAuthError(msg) if r.status_code == 401 else TableauError(msg)

    # -- sign in / out --
    def sign_in(self) -> None:
        base = self.cfg.server_url
        r = self._request("GET", f"{base}/api/2.4/serverinfo", auth=False)
        if r.status_code == 200:
            self.api = r.json()["serverInfo"].get("restApiVersion", self.api)
        body = {
            "credentials": {
                "personalAccessTokenName": self.cfg.token_name,
                "personalAccessTokenSecret": self.cfg.token_secret.get_secret_value(),
                "site": {"contentUrl": self.cfg.site},
            }
        }
        r = self._request("POST", f"{base}/api/{self.api}/auth/signin", json=body, auth=False)
        if r.status_code != 200:
            raise self._fail(r, "Tableau sign-in failed")
        c = r.json()["credentials"]
        self.token, self.site_id = c["token"], c["site"]["id"]
        register_secret(self.token)

    def sign_out(self) -> None:
        if not self.token:
            return
        with contextlib.suppress(Exception):
            self._request("POST", f"{self.cfg.server_url}/api/{self.api}/auth/signout")
        self.token = None
        self.http.close()

    # -- GET helpers (re-sign-in once on 401) --
    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        url = f"{self.cfg.server_url}/api/{self.api}{path}"
        r = self._request("GET", url, params=params)
        if r.status_code == 401:
            self.sign_in()
            r = self._request("GET", url, params=params)
        if r.status_code != 200:
            raise self._fail(r, f"Tableau GET {re.sub(r'[0-9a-f-]{36}', '{id}', path)}")
        return r

    def _site_path(self, tail: str) -> str:
        return f"/sites/{self.site_id}{tail}"

    def _paged(self, tail: str, key: str, item: str, params: dict | None = None) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            p = {"pageSize": 1000, "pageNumber": page, **(params or {})}
            d = self._get(self._site_path(tail), p).json()
            out += d.get(key, {}).get(item, []) or []
            pg = d.get("pagination", {})
            if page * int(pg.get("pageSize", 1000)) >= int(pg.get("totalAvailable", 0)):
                return out
            page += 1

    def graphql(self, query: str) -> dict[str, Any]:
        url = f"{self.cfg.server_url}/api/metadata/graphql"
        r = self._request("POST", url, json={"query": query})
        if r.status_code == 401:
            self.sign_in()
            r = self._request("POST", url, json={"query": query})
        if r.status_code != 200:
            raise self._fail(r, "Tableau metadata query failed")
        d = r.json()
        if d.get("errors") and not d.get("data"):
            msg = "; ".join(e.get("message", "?") for e in d["errors"][:3])
            raise TableauError(redact(f"Metadata API error: {msg}", self.cfg))
        return d

    # -- READ methods --
    def server_info(self) -> dict[str, Any]:
        r = self._request("GET", f"{self.cfg.server_url}/api/2.4/serverinfo", auth=False)
        info = r.json()["serverInfo"] if r.status_code == 200 else {}
        return {
            "product_version": info.get("productVersion", {}).get("value"),
            "rest_api_version": info.get("restApiVersion"),
        }

    def site_info(self) -> dict[str, Any]:
        try:
            s = self._get(f"/sites/{self.site_id}").json()["site"]
        except TableauError:  # non-admin accounts can't read the site record
            return {"id": self.site_id, "name": None, "content_url": self.cfg.site}
        return {"id": s.get("id"), "name": s.get("name"), "content_url": s.get("contentUrl")}

    def projects(self) -> list[dict[str, Any]]:
        return [
            {
                "id": p["id"],
                "name": p["name"],
                "parent_id": p.get("parentProjectId"),
                "description": p.get("description"),
            }
            for p in self._paged("/projects", "projects", "project")
        ]

    def workbooks(self, project: str | None = None) -> list[dict[str, Any]]:
        params = {"filter": f"projectName:eq:{project}"} if project else None
        return [
            {
                "id": w["id"],
                "name": w["name"],
                "project": (w.get("project") or {}).get("name"),
                "project_id": (w.get("project") or {}).get("id"),
                "updated_at": w.get("updatedAt"),
                "content_url": w.get("contentUrl"),
            }
            for w in self._paged("/workbooks", "workbooks", "workbook", params)
        ]

    def datasources(self) -> list[dict[str, Any]]:
        return [
            {"id": d["id"], "name": d["name"], "project": (d.get("project") or {}).get("name")}
            for d in self._paged("/datasources", "datasources", "datasource")
        ]

    def workbook_connections(self, workbook_id: str) -> list[dict[str, Any]]:
        """REST connection list: type + owning data source name (server address/user omitted).
        Unlike Metadata lineage, this is visible even when upstream tables are permission-hidden."""
        _check_id(workbook_id)
        r = self._get(self._site_path(f"/workbooks/{workbook_id}/connections")).json()
        return [
            {"type": c.get("type"), "datasource": (c.get("datasource") or {}).get("name")}
            for c in r.get("connections", {}).get("connection", []) or []
        ]

    def extract_refresh_tasks(self) -> list[dict[str, Any]]:
        """Extract refresh schedules (tasks). READ-ONLY; non-admins may get 403."""
        d = self._get(self._site_path("/tasks/extractRefreshes")).json()
        out = []
        for t in d.get("tasks", {}).get("task", []) or []:
            e = t.get("extractRefresh") or {}
            out.append(
                {
                    "workbook_id": (e.get("workbook") or {}).get("id"),
                    "datasource_id": (e.get("datasource") or {}).get("id"),
                    "failed_count": int(e.get("consecutiveFailedCount") or 0),
                    "next_run_at": (e.get("schedule") or {}).get("nextRunAt") or e.get("nextRunAt"),
                }
            )
        return out

    def refresh_jobs(self, max_items: int = 2000) -> list[dict[str, Any]]:
        """Recent extract-refresh background jobs (status + timestamps only)."""
        out: list[dict[str, Any]] = []
        page = 1
        while len(out) < max_items:
            params = {"filter": "jobType:eq:refresh_extracts", "pageSize": 100, "pageNumber": page}
            d = self._get(self._site_path("/jobs"), params).json()
            jobs = d.get("backgroundJobs", {}).get("backgroundJob", []) or []
            out += [
                {
                    "status": j.get("status"),
                    "ended_at": j.get("endedAt"),
                    "created_at": j.get("createdAt"),
                }
                for j in jobs
            ]
            total = int(d.get("pagination", {}).get("totalAvailable", 0))
            if not jobs or page * 100 >= total:
                break
            page += 1
        return out

    def workbook_views(self, workbook_id: str) -> list[dict[str, Any]]:
        _check_id(workbook_id)
        r = self._get(self._site_path(f"/workbooks/{workbook_id}/views")).json()
        return [
            {"id": v["id"], "name": v["name"], "content_url": v.get("contentUrl")}
            for v in r.get("views", {}).get("view", []) or []
        ]

    def view_csv(self, view_id: str) -> str:
        """The data behind one view, as CSV text (for reconciliation)."""
        _check_id(view_id)
        return self._get(self._site_path(f"/views/{view_id}/data")).text

    def workbook_detail(self, workbook_id: str) -> dict[str, Any]:
        """Sheets, dashboards, data sources (live/extract), upstream tables, fields + formulas."""
        _check_id(workbook_id)
        d = self.graphql(WORKBOOK_QUERY % workbook_id)
        wbs = (d.get("data") or {}).get("workbooks") or []
        if not wbs:
            raise TableauError("workbook not found in the Metadata API (it may not be indexed yet)")
        detail = _shape_workbook(wbs[0])
        try:
            conns = self.workbook_connections(workbook_id)
        except TableauError:
            conns = []
        for ds in detail["datasources"]:
            ds["connection_types"] = sorted(
                {c["type"] for c in conns if c["datasource"] == ds["name"] and c["type"]}
            )
        if d.get("errors"):
            detail["partial"] = True  # e.g. Metadata API node limit exceeded
        return detail

    def metadata_workbook_index(self) -> list[dict[str, Any]]:
        """Lightweight list of every workbook the Metadata API returns (luid, name, project)."""
        out: list[dict[str, Any]] = []
        after = "null"
        while True:
            d = self.graphql(INDEX_QUERY % after)
            conn = (d.get("data") or {}).get("workbooksConnection") or {}
            out += [
                {"id": n.get("luid"), "name": n.get("name"), "project": n.get("projectName")}
                for n in conn.get("nodes") or []
            ]
            info = conn.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return out
            after = json.dumps(info["endCursor"])

    def workbook_details(
        self, ids: list[str], workers: int = 1, on_done: Any = None
    ) -> dict[str, dict[str, Any] | str]:
        """Detail for many workbooks. Threads share THIS session's token (one sign-in)."""
        from concurrent.futures import ThreadPoolExecutor

        def one(i: str) -> tuple[str, dict[str, Any] | str]:
            try:
                return i, self.workbook_detail(i)
            except TableauError as e:
                return i, str(e)

        out: dict[str, dict[str, Any] | str] = {}
        with ThreadPoolExecutor(workers) as ex:
            for n, (i, r) in enumerate(ex.map(one, ids), 1):
                out[i] = r
                if on_done:
                    on_done(n, len(ids))
        return out


def _check_id(v: str) -> None:
    if not _UUID.match(v):
        raise TableauError("invalid id")


# ---------- Metadata API ----------
_DS_BODY = """
  name hasExtracts extractLastRefreshTime
  upstreamTables { name schema fullName connectionType database { name connectionType } }
  upstreamDatabases { name connectionType }
  fields { name __typename ... on CalculatedField { formula dataType role } }
"""

_WB_BODY = (
    """luid name projectName
  sheets { name }
  dashboards { name sheets { name } }
  upstreamDatasources { luid """
    + _DS_BODY
    + """ }
  embeddedDatasources { """
    + _DS_BODY
    + """ }"""
)
WORKBOOK_QUERY = '{ workbooks(filter: {luid: "%s"}) { ' + _WB_BODY + " } }"
INDEX_QUERY = (
    "{ workbooksConnection(first: 100, after: %s) { nodes { luid name projectName } "
    "pageInfo { hasNextPage endCursor } } }"
)


def _shape_ds(ds: dict[str, Any], published: bool) -> dict[str, Any]:
    raw_tables = ds.get("upstreamTables") or []
    tables = [
        {
            "name": t.get("name"),
            "schema": t.get("schema"),
            "full_name": t.get("fullName"),
            "connection_type": t.get("connectionType")
            or (t.get("database") or {}).get("connectionType"),
            "database": (t.get("database") or {}).get("name"),
        }
        for t in raw_tables
        if t.get("name") or t.get("fullName")
    ]
    fields = []
    for f in ds.get("fields") or []:
        row: dict[str, Any] = {"name": f.get("name"), "type": f.get("__typename")}
        if f.get("formula") is not None:
            row.update(formula=f["formula"], data_type=f.get("dataType"), role=f.get("role"))
        fields.append(row)
    return {
        "name": ds.get("name"),
        "id": ds.get("luid"),
        "extract_last_refresh": ds.get("extractLastRefreshTime"),
        "published": published,
        "connection": "extract" if ds.get("hasExtracts") else "live",
        "databases": [
            {"name": d.get("name"), "connection_type": d.get("connectionType")}
            for d in ds.get("upstreamDatabases") or []
            if d.get("name") or d.get("connectionType")
        ],
        # null upstream nodes = lineage the account isn't permitted to see (or no lineage)
        "hidden_upstream": len(raw_tables) - len(tables),
        "tables": tables,
        "fields": fields,
    }


def _shape_workbook(w: dict[str, Any]) -> dict[str, Any]:
    ds = [_shape_ds(d, True) for d in w.get("upstreamDatasources") or []]
    ds += [_shape_ds(d, False) for d in w.get("embeddedDatasources") or []]
    return {
        "id": w.get("luid"),
        "name": w.get("name"),
        "project": w.get("projectName"),
        "sheets": [s.get("name") for s in w.get("sheets") or []],
        "dashboards": [
            {"name": d.get("name"), "sheets": [s.get("name") for s in d.get("sheets") or []]}
            for d in w.get("dashboards") or []
        ],
        "datasources": ds,
    }


# ---------- the guarded entry point ----------
_PROC_LOCK = threading.RLock()
_depth = threading.local()


@contextmanager
def session(per_call: bool = False) -> Iterator[Session]:
    """One serialized, signed-in operation. Re-entrant within a thread.

    per_call=False (API): lock held for the whole operation (sign in, work, sign out).
    per_call=True (long scripts): the lock is taken and released around each HTTP call, so the
    deployed API can interleave; if the API signs in meanwhile, our next call gets a 401 and
    re-signs in (the single-session PAT rule).
    """
    cfg = load_config()
    if cfg is None:
        raise TableauNotConfigured("Tableau is not configured")
    if getattr(_depth, "s", None) is not None:
        yield _depth.s
        return
    with contextlib.ExitStack() as st:
        if not per_call:
            st.enter_context(_PROC_LOCK)
            st.enter_context(_ddb_lock(LOCK_TTL_S))
        s = Session(cfg, per_call=per_call)
        try:
            for attempt in (1, 2):  # retry a sign-in conflict / 401 once, then surface it
                try:
                    s.sign_in()
                    break
                except TableauError as e:
                    if attempt == 2:
                        if isinstance(e, TableauAuthError):
                            _best_effort(alerts.record_auth_failure)
                        raise
                    time.sleep(2)
            _best_effort(alerts.clear_auth_failure)
            _depth.s = s
            try:
                yield s
            except TableauAuthError:
                _best_effort(alerts.record_auth_failure)
                raise
        finally:
            _depth.s = None
            s.sign_out()


# ---------- 5-minute cache for the API ----------
CACHE_TTL_S = 300
_cache: dict[str, tuple[float, Any]] = {}


def cached(key: str, fn: Any) -> Any:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def clear_cache() -> None:
    _cache.clear()
