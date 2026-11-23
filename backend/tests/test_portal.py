"""Portal security basics, offline: store + BigQuery are faked, auth is overridden."""

import json
from typing import Any

import pytest
from app import renames, routes_portal, store
from app.auth import User, current_user
from app.main import app
from fastapi.testclient import TestClient

SPEC = {
    "schema_version": 1,
    "view": {"name": "Sales", "slug": "sales"},
    "revision": 7,
    "updated_at": "t",
    "updated_by": "someone@example.com",
    "layout": [{"element_id": "a", "x": 0, "y": 0, "w": 12, "h": 4}],
    "elements": [
        {
            "id": "a",
            "type": "chart",
            "title": "Revenue",
            "chart": {"kind": "bar", "x": "m", "y": ["r"], "series": None, "stacked": False},
            "sql": "SELECT m, r FROM `p.d.secret_table`",
            "format": {},
            "cache_ttl_seconds": 60,
        },
        {"id": "b", "type": "kpi", "title": "N", "sql": "SELECT 1 AS n", "format": {}},
        {"id": "t", "type": "text", "title": "Note"},
    ],
}


def _view(slug: str, published: bool, desc: str | None = None) -> dict[str, Any]:
    v: dict[str, Any] = {
        "PK": "x",
        "SK": "y",
        "client_slug": "acme",
        "project_slug": "main",
        "slug": slug,
        "name": slug.title(),
        "draft_revision": 7,
        "rev_counter": 7,
    }
    if desc:
        v["description"] = desc
    if published:
        v["published_revision"] = 7
        v["published_summary"] = store.summarize_spec(SPEC, "2026-01-01T00:00:00Z")
    return v


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    db: dict[str, Any] = {
        "views": {
            ("acme", "main", "sales"): _view("sales", True),
            ("acme", "main", "wip"): _view("wip", False),
        },
        "access": {"client-sub": ["acme"]},
        "ran": [],
    }
    clients = {
        "acme": {"slug": "acme", "name": "Acme"},
        "other": {"slug": "other", "name": "Other"},
    }
    projects = {"main": {"slug": "main", "name": "Main", "client_slug": "acme"}}
    s = monkeypatch.setattr
    s(renames, "_get_item", lambda key: None)  # no aliases
    s(store, "get_client", lambda c: clients.get(c))
    s(store, "get_project", lambda c, p: projects.get(p) if c == "acme" else None)
    s(store, "list_projects", lambda c: list(projects.values()) if c == "acme" else [])
    s(
        store,
        "list_views",
        lambda c, p: [v for (cc, pp, _), v in db["views"].items() if (cc, pp) == (c, p)],
    )
    s(store, "get_view", lambda c, p, v: db["views"].get((c, p, v)))
    s(store, "has_access", lambda sub, c: c in db["access"].get(sub, []))
    s(store, "list_access", lambda sub: db["access"].get(sub, []))
    s(store, "s3_get_json", lambda key: {"revision": 7, "published_at": "2026-01-01T00:00:00Z"})
    s(store, "read_revision", lambda c, p, v, n: json.loads(json.dumps(SPEC)))
    s(store, "get_project_config", lambda c, p: object())

    def fake_run(cfg: Any, sql: str, **kw: Any) -> dict[str, Any]:
        db["ran"].append(sql)
        return {
            "columns": ["n"],
            "rows": [[1]],
            "truncated": False,
            "bytes_processed": 99,
            "cache_hit": True,
        }

    s(routes_portal, "run_query", fake_run)

    def fake_meta(c: str, p: str, v: str, name: str | None, desc: str | None) -> dict[str, Any]:
        item = db["views"][(c, p, v)]
        if name:
            item["name"] = name
        if desc is not None:
            item["description"] = desc
        return item

    s(store, "update_view_meta", fake_meta)
    return db


def as_user(sub: str, groups: list[str]) -> TestClient:
    app.dependency_overrides[current_user] = lambda: User(
        sub=sub, email=f"{sub}@x.io", groups=groups
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear() -> Any:
    yield
    app.dependency_overrides.clear()


def test_client_sees_only_published_cards(fake: dict[str, Any]) -> None:
    r = as_user("client-sub", ["client"]).get("/api/portal/clients/acme")
    assert r.status_code == 200
    views = r.json()["projects"][0]["views"]
    assert [v["slug"] for v in views] == ["sales"]
    assert views[0]["counts"] == {"chart": 1, "table": 0, "kpi": 1, "text": 1}
    assert views[0]["chart_kinds"] == ["bar"]


def test_unpublished_and_unassigned_are_404(fake: dict[str, Any]) -> None:
    c = as_user("client-sub", ["client"])
    assert c.get("/api/portal/clients/acme/projects/main/views/wip").status_code == 404
    assert c.get("/api/portal/clients/acme/projects/main/views/wip/data/a").status_code == 404
    assert c.get("/api/portal/clients/other").status_code == 404
    assert as_user("nobody", ["client"]).get("/api/portal/clients/acme").status_code == 404


def test_portal_payloads_leak_nothing(fake: dict[str, Any]) -> None:
    c = as_user("client-sub", ["client"])
    bodies = [
        c.get("/api/portal/clients/acme").text,
        c.get("/api/portal/clients/acme/projects/main").text,
        c.get("/api/portal/clients/acme/projects/main/views/sales").text,
        c.get("/api/portal/clients/acme/projects/main/views/sales/data/a").text,
    ]
    for b in bodies:
        for banned in (
            "secret_table",
            "sql",
            "revision",
            "bytes_processed",
            "cache_hit",
            "someone@",
        ):
            assert banned not in b, banned
    data = c.get("/api/portal/clients/acme/projects/main/views/sales/data/a?draft=true").json()
    assert set(data) == {"columns", "rows", "truncated"}
    assert fake["ran"]  # the saved SQL ran; ?draft is ignored


def test_description_patch_shows_on_card(fake: dict[str, Any]) -> None:
    url = "/api/clients/acme/projects/main/views/sales"
    admin = as_user("admin-sub", ["admin"])  # NB: overrides are app-global; one user at a time
    assert admin.patch(url, json={"description": "Monthly sales"}).status_code == 200
    assert admin.patch(url, json={"description": "x" * 301}).status_code == 422
    client = as_user("client-sub", ["client"])
    card = client.get("/api/portal/clients/acme").json()
    assert card["projects"][0]["views"][0]["description"] == "Monthly sales"
    assert client.patch(url, json={"name": "Hax"}).status_code == 403


def test_builders_can_use_portal(fake: dict[str, Any]) -> None:
    for groups in (["admin"], ["analyst"]):
        c = as_user("b", groups)
        assert c.get("/api/portal/me").json() == []
        assert c.get("/api/portal/clients/acme").status_code == 200
        assert c.get("/api/portal/clients/acme/projects/main/views/sales").status_code == 200
        assert c.get("/api/portal/clients/acme/projects/main/views/wip").status_code == 404
    assert as_user("client-sub", ["client"]).get("/api/portal/me").json() == [
        {"slug": "acme", "name": "Acme"}
    ]
