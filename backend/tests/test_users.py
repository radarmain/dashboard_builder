"""User-management rules, offline: Cognito and the access store are faked, auth is overridden."""

# ruff: noqa: E501

from typing import Any

import pytest
from app import cognito, routes_core, routes_users, store, users
from app.auth import User, current_user
from app.cognito import CUser
from app.main import app
from fastapi.testclient import TestClient


class World:
    """Fake Cognito pool + DynamoDB access/mirror items."""

    def __init__(self) -> None:
        self.users: dict[str, CUser] = {}
        self.access: set[tuple[str, str]] = set()  # USER#sub -> CLIENT#slug
        self.mirror: dict[tuple[str, str], str] = {}  # CLIENT#slug -> USER#sub (email)
        self.invites: list[str] = []
        self.n = 0

    def add(self, email: str, group: str, enabled: bool = True, status: str = "CONFIRMED") -> str:
        self.n += 1
        sub = f"sub{self.n}"
        self.users[sub] = CUser(sub, email, None, enabled, status, "2026-01-01T00:00:00Z", [group])
        return sub


@pytest.fixture
def w(monkeypatch: pytest.MonkeyPatch) -> World:
    world = World()
    s = monkeypatch.setattr
    by_email = lambda e: next((u for u in world.users.values() if u.email == e), None)  # noqa: E731

    def create(email: str, name: str | None, group: str) -> CUser:
        sub = world.add(email, group, status="FORCE_CHANGE_PASSWORD")
        return world.users[sub]

    s(cognito, "find_by_email", by_email)
    s(cognito, "get_by_sub", lambda sub, with_groups=True: world.users.get(sub))
    s(cognito, "create", create)
    s(cognito, "add_to_group", lambda sub, g: world.users[sub].groups.append(g))
    s(cognito, "remove_from_group", lambda sub, g: world.users[sub].groups.remove(g))
    s(cognito, "set_enabled", lambda sub, en: setattr(world.users[sub], "enabled", en))
    s(cognito, "resend_invite", lambda email: world.invites.append(email))
    s(
        cognito,
        "list_group",
        lambda g: [u for u in world.users.values() if g in u.groups],
    )

    def grant(sub: str, c: str, email: str, by: str) -> bool:
        if (sub, c) in world.access:
            return False
        world.access.add((sub, c))
        world.mirror[(c, sub)] = email
        return True

    def revoke(sub: str, c: str) -> None:
        world.access.discard((sub, c))
        world.mirror.pop((c, sub), None)

    s(store, "grant_access", grant)
    s(store, "revoke_access", revoke)
    s(store, "has_access", lambda sub, c: (sub, c) in world.access)
    s(store, "list_access", lambda sub: [c for (u, c) in world.access if u == sub])
    s(
        store,
        "list_client_users",
        lambda c: [
            {"sub": sub, "email": e, "added_by": "x", "added_at": "t"}
            for (cc, sub), e in world.mirror.items()
            if cc == c
        ],
    )
    clients = {"acme": {"slug": "acme"}, "other": {"slug": "other"}}
    s(store, "get_client", lambda c: clients.get(c))
    s(routes_core.store, "get_client", lambda c: clients.get(c))
    s(routes_users.store, "get_client", lambda c: clients.get(c))
    return world


@pytest.fixture(autouse=True)
def _clear() -> Any:
    yield
    app.dependency_overrides.clear()


def as_user(sub: str, groups: list[str]) -> TestClient:
    app.dependency_overrides[current_user] = lambda: User(sub=sub, email="a@b.c", groups=groups)
    return TestClient(app)


def test_roles_and_forbidden(w: World) -> None:
    c = as_user("an", ["analyst"])
    assert c.get("/api/admin/users").status_code == 403
    assert (
        c.post(
            "/api/admin/users", json={"email": "x@radar-analytics.com", "role": "analyst"}
        ).status_code
        == 403
    )
    cl = as_user("cl", ["client"])
    for method, url in [("get", "/api/clients/acme/users"), ("get", "/api/admin/users")]:
        assert getattr(cl, method)(url).status_code == 403
    assert cl.post("/api/clients/acme/users", json={"email": "a@b.co"}).status_code == 403


def test_analyst_adds_client_user_and_existing_only_grants(w: World) -> None:
    c = as_user("an", ["analyst"])
    r = c.post("/api/clients/acme/users", json={"email": "  New@Client.COM "})
    assert r.status_code == 201 and r.json()["email"] == "new@client.com"
    assert r.json()["status"] == "invited"
    sub = r.json()["sub"]
    assert w.users[sub].groups == ["client"] and (sub, "acme") in w.access
    # same user on another client: access only, no second account, no other-client info
    n = len(w.users)
    r = c.post("/api/clients/other/users", json={"email": "new@client.com"})
    assert r.status_code == 201 and len(w.users) == n and (sub, "other") in w.access
    assert "clients" not in r.json()
    # staff email -> 409
    w.add("boss@radar-analytics.com", "admin")
    assert (
        c.post("/api/clients/acme/users", json={"email": "boss@radar-analytics.com"}).status_code
        == 409
    )
    assert c.post("/api/clients/acme/users", json={"email": "not-an-email"}).status_code == 400


def test_analyst_sees_only_that_clients_users(w: World) -> None:
    a = w.add("a@x.com", "client")
    b = w.add("b@x.com", "client")
    users.store.grant_access(a, "acme", "a@x.com", "t")
    users.store.grant_access(a, "other", "a@x.com", "t")
    users.store.grant_access(b, "other", "b@x.com", "t")
    body = as_user("an", ["analyst"]).get("/api/clients/acme/users").json()
    assert [u["email"] for u in body] == ["a@x.com"]
    assert "clients" not in body[0]


def test_remove_access_removes_both_items_keeps_account(w: World) -> None:
    sub = w.add("a@x.com", "client")
    users.store.grant_access(sub, "acme", "a@x.com", "t")
    users.store.grant_access(sub, "other", "a@x.com", "t")
    r = as_user("an", ["analyst"]).delete(f"/api/clients/acme/users/{sub}")
    assert r.status_code == 200
    assert ("acme", sub) not in w.mirror and (sub, "acme") not in w.access
    assert (sub, "other") in w.access and sub in w.users and w.users[sub].enabled


def test_resend_only_when_invited(w: World) -> None:
    inv = w.add("i@x.com", "client", status="FORCE_CHANGE_PASSWORD")
    act = w.add("a@x.com", "client")
    for s in (inv, act):
        users.store.grant_access(s, "acme", "e", "t")
    c = as_user("an", ["analyst"])
    assert c.post(f"/api/clients/acme/users/{inv}/resend-invite").status_code == 200
    assert c.post(f"/api/clients/acme/users/{act}/resend-invite").status_code == 409
    assert w.invites == ["i@x.com"]


def test_staff_domain_guard_and_create(w: World) -> None:
    c = as_user("ad", ["admin"])
    r = c.post("/api/admin/users", json={"email": "x@gmail.com", "role": "analyst"})
    assert r.status_code == 400 and "radar-analytics.com" in r.json()["detail"]
    assert (
        c.post(
            "/api/admin/users", json={"email": "x@radar-analytics.com", "role": "client"}
        ).status_code
        == 400
    )
    r = c.post("/api/admin/users", json={"email": "X@Radar-Analytics.com", "role": "analyst"})
    assert r.status_code == 201 and r.json()["role"] == "analyst"
    assert w.users[r.json()["sub"]].groups == ["analyst"]
    assert (
        c.post(
            "/api/admin/users", json={"email": "x@radar-analytics.com", "role": "admin"}
        ).status_code
        == 409
    )


def test_self_and_last_admin_guards(w: World) -> None:
    me = w.add("me@radar-analytics.com", "admin")
    other = w.add("o@radar-analytics.com", "admin")
    c = as_user(me, ["admin"])
    assert c.post(f"/api/admin/users/{me}/disable").status_code == 400
    assert c.post(f"/api/admin/users/{me}/role", json={"role": "analyst"}).status_code == 400
    assert (
        c.post(f"/api/admin/users/{other}/role", json={"role": "analyst"}).status_code == 200
    )  # 2 admins
    assert w.users[other].groups == ["analyst"]  # exactly one group after the switch
    # `me` is now the only admin; another admin session may not demote or disable them
    third = w.add("t@radar-analytics.com", "analyst")
    c2 = as_user(third, ["admin"])
    assert c2.post(f"/api/admin/users/{me}/disable").status_code == 409
    assert c2.post(f"/api/admin/users/{me}/role", json={"role": "analyst"}).status_code == 409


def test_no_client_staff_conversion(w: World) -> None:
    cl = w.add("c@x.com", "client")
    st = w.add("s@radar-analytics.com", "analyst")
    c = as_user("ad", ["admin"])
    assert c.post(f"/api/admin/users/{cl}/role", json={"role": "analyst"}).status_code == 409
    assert c.post(f"/api/admin/users/{st}/role", json={"role": "client"}).status_code == 400
    # legacy /admin/access refuses non-client users
    r = c.post("/api/admin/access", json={"user_sub": st, "client_slug": "acme"})
    assert r.status_code == 409
    # a staff account can't be turned into a client user via the per-client route either
    assert (
        as_user("an", ["analyst"])
        .post("/api/clients/acme/users", json={"email": "s@radar-analytics.com"})
        .status_code
        == 409
    )


def test_disable_enable_client_user_and_admin_list(w: World) -> None:
    cl = w.add("c@x.com", "client")
    users.store.grant_access(cl, "acme", "c@x.com", "t")
    c = as_user("ad", ["admin"])
    assert c.post(f"/api/admin/users/{cl}/disable").json()["status"] == "disabled"
    assert c.post(f"/api/admin/users/{cl}/enable").json()["status"] == "active"
    rows = c.get("/api/admin/users?role=client").json()["users"]
    assert rows[0]["clients"] == ["acme"] and rows[0]["role"] == "client"
    assert c.get("/api/admin/users?role=nope").status_code == 400


def test_no_delete_surface() -> None:
    assert not hasattr(cognito, "delete") and not hasattr(cognito, "admin_delete_user")
    methods = {(m, r.path) for r in app.routes if hasattr(r, "methods") for m in r.methods}
    assert not [p for m, p in methods if "users" in p and m == "DELETE" and "/admin/" in p]
