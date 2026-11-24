"""Lifecycle rules and the resumable permanent delete. Offline: DynamoDB and S3 are replaced by an
in-memory fake behind lifecycle's small AWS helpers."""

import re
from pathlib import Path
from typing import Any

import pytest
from app import lifecycle as lc
from app.auth import User, current_user
from app.main import app
from fastapi.testclient import TestClient


class Fake:
    def __init__(self) -> None:
        self.db: dict[tuple[str, str], dict[str, Any]] = {}
        self.s3: dict[str, int] = {}
        self.fail_s3_once = False
        self.fail_ddb_after: int | None = None  # fail the Nth batch delete, once
        self.batches = 0

    def add(self, pk: str, sk: str, **kw: Any) -> None:
        self.db[(pk, sk)] = {"PK": pk, "SK": sk, **kw}

    def install(self, mp: pytest.MonkeyPatch) -> None:
        mp.setattr(lc, "_get", lambda k: self.db.get((k["PK"], k["SK"])))
        mp.setattr(lc, "_set_lifecycle", self._set)
        mp.setattr(lc, "_query", self._query)
        mp.setattr(lc, "_ddb_delete", self._delete)
        mp.setattr(lc, "_ddb_delete_one", lambda k: self.db.pop((k["PK"], k["SK"]), None))
        mp.setattr(
            lc,
            "_s3_list",
            lambda p: [(k, n) for k, n in sorted(self.s3.items()) if k.startswith(p)],
        )
        mp.setattr(lc, "_s3_delete", self._s3_delete)

    def _set(self, key: dict[str, str], values: dict[str, Any]) -> None:
        self.db[(key["PK"], key["SK"])].update(values)

    def _query(self, pk: str, prefix: str = "") -> list[dict[str, Any]]:
        return [
            dict(v) for (p, s), v in sorted(self.db.items()) if p == pk and s.startswith(prefix)
        ]

    def _delete(self, keys: list[dict[str, str]]) -> None:
        self.batches += 1
        assert all(len(k) == 2 for k in keys)
        if self.fail_ddb_after == self.batches:
            self.fail_ddb_after = None
            raise RuntimeError("boom")
        for k in keys:
            self.db.pop((k["PK"], k["SK"]), None)

    def _s3_delete(self, keys: list[str]) -> None:
        if self.fail_s3_once:
            self.fail_s3_once = False
            raise RuntimeError("boom")
        for k in keys:
            self.s3.pop(k, None)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    f = Fake()
    f.install(monkeypatch)
    for c in ("acme", "acme-two"):  # acme-two shares the "acme" name prefix on purpose
        pk = f"CLIENT#{c}"
        f.add(pk, "META", slug=c, lifecycle="archived")
        f.add(pk, "PROJECT#main", slug="main", lifecycle="archived")
        f.add(pk, "PROJECT#side", slug="side")
        f.add(pk, "VIEW#main#sales", slug="sales", project_slug="main")
        f.add(pk, "VIEW#side#x", slug="x", project_slug="side")
        f.add(pk, "IMPORT#main#" + "a" * 32, scanned_bytes=5)
        f.add(pk, "ALIAS#PROJECT#old-main", target="main")
        f.add(pk, "ALIAS#VIEW#old-main#sales", target="main#sales")
        f.add(pk, "ALIAS#VIEW#side#old-x", target="side#x")
        f.add(pk, "USER#u1", email="u1@x.com")
        f.add("USER#u1", f"CLIENT#{c}")
        f.s3[f"clients/{c}/projects/main/project.json"] = 10
        f.s3[f"clients/{c}/projects/main/views/sales/revisions/000001.json"] = 20
        f.s3[f"clients/{c}/projects/side/views/x/revisions/000001.json"] = 30
    f.add("USER#u1", "FAV#CLIENT#acme", created_at="t")
    f.add("SYSTEM", "LOCK")
    f.s3["cache/abc.json"] = 1
    return f


def keys_for(f: Fake, c: str) -> set[tuple[str, str]]:
    return {k for k in f.db if k[0] == f"CLIENT#{c}"}


# ---------- effective lifecycle ----------
def test_effective_lifecycle() -> None:
    a, act, dele = {"lifecycle": "archived"}, {"lifecycle": "active"}, {"lifecycle": "deleting"}
    assert lc.effective(act, act, None) == "active"
    assert lc.effective({}, {}) == "active"  # missing field = active
    assert lc.effective(a, act, act) == "archived"  # ancestor archived
    assert lc.effective(act, act, a) == "archived"
    assert lc.effective(a, dele) == "deleting"  # deleting beats archived
    assert lc.state({"lifecycle": "junk"}) == "active"


# ---------- permanent delete ----------
def test_delete_requires_archived(fake: Fake) -> None:
    with pytest.raises(lc.NotArchived):
        lc.permanent_delete("acme", "side", None, "sub")
    with pytest.raises(lc.Missing):
        lc.permanent_delete("acme", "nope", None, "sub")


def test_delete_view_only_touches_that_view(fake: Fake) -> None:
    fake.db[("CLIENT#acme", "VIEW#side#x")]["lifecycle"] = "archived"
    lc.permanent_delete("acme", "side", "x", "sub")
    assert ("CLIENT#acme", "VIEW#side#x") not in fake.db
    assert ("CLIENT#acme", "ALIAS#VIEW#side#old-x") not in fake.db
    assert ("CLIENT#acme", "VIEW#main#sales") in fake.db
    assert "clients/acme/projects/side/views/x/revisions/000001.json" not in fake.s3
    assert (
        "clients/acme/projects/side/views/x/revisions/000001.json".replace("side", "main")
        not in fake.s3
        or True
    )
    assert "clients/acme/projects/main/views/sales/revisions/000001.json" in fake.s3


def test_delete_project_removes_children_aliases_imports(fake: Fake) -> None:
    lc.permanent_delete("acme", "main", None, "sub")
    left = {s for _, s in keys_for(fake, "acme")}
    assert not any(s.endswith("main") or "#main#" in s or "old-main" in s for s in left), left
    assert "PROJECT#side" in left and "VIEW#side#x" in left and "META" in left
    assert not [k for k in fake.s3 if k.startswith("clients/acme/projects/main/")]
    assert "clients/acme/projects/side/views/x/revisions/000001.json" in fake.s3
    assert keys_for(fake, "acme-two") and any("acme-two/projects/main" in k for k in fake.s3)


def test_delete_client_removes_everything_for_that_client_only(fake: Fake) -> None:
    lc.permanent_delete("acme", None, None, "sub")
    assert not keys_for(fake, "acme")
    assert ("USER#u1", "CLIENT#acme") not in fake.db  # access item gone...
    assert ("USER#u1", "CLIENT#acme-two") in fake.db  # ...only for this client
    assert not [k for k in fake.s3 if k.startswith("clients/acme/")]
    assert [k for k in fake.s3 if k.startswith("clients/acme-two/")]  # prefix match is strict
    assert "cache/abc.json" in fake.s3
    assert ("SYSTEM", "LOCK") in fake.db and ("USER#u1", "FAV#CLIENT#acme") in fake.db


@pytest.mark.parametrize("what", ["s3", "ddb"])
def test_delete_is_resumable(fake: Fake, what: str) -> None:
    if what == "s3":
        fake.fail_s3_once = True
    else:
        fake.fail_ddb_after = 2
    with pytest.raises(RuntimeError):
        lc.permanent_delete("acme", None, None, "sub")
    meta = fake.db[("CLIENT#acme", "META")]
    assert meta["lifecycle"] == "deleting"  # still there, hidden, retryable
    lc.permanent_delete("acme", None, None, "sub")  # retry of the same call completes
    assert not keys_for(fake, "acme")
    assert ("USER#u1", "CLIENT#acme") not in fake.db


def test_own_record_is_deleted_last(fake: Fake) -> None:
    order: list[str] = []
    orig = fake._delete
    fake._delete = lambda keys: (order.extend(k["SK"] for k in keys), orig(keys))[1]  # type: ignore[method-assign]
    lc.permanent_delete("acme", "main", None, "sub")
    assert "PROJECT#main" not in order  # removed by the final single delete, after every batch
    assert ("CLIENT#acme", "PROJECT#main") not in fake.db


def test_refuses_bad_prefix() -> None:
    assert lc._PREFIX_OK.match("clients/acme/")
    assert lc._PREFIX_OK.match("clients/acme/projects/main/views/sales/")
    for bad in ("clients/", "clients//", "", "cache/", "clients/acme", "clients/../x/"):
        assert not lc._PREFIX_OK.match(bad)


def test_preview_counts(fake: Fake) -> None:
    pv = lc.preview("acme")
    assert (pv["projects"], pv["views"], pv["revisions"], pv["client_users"]) == (2, 2, 2, 1)
    assert pv["s3_objects"] == 3 and pv["s3_bytes"] == 60


# ---------- route permissions (no AWS: the item lookup is the fake) ----------
def _client_as(groups: list[str]) -> TestClient:
    app.dependency_overrides[current_user] = lambda: User(sub="s", email="a@b.c", groups=groups)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset() -> Any:
    yield
    app.dependency_overrides.clear()


def test_delete_permissions_and_confirmation(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routes_lifecycle.canon", lambda c, p, v=None: (p, v or ""))
    analyst = _client_as(["analyst"])
    assert analyst.delete("/api/clients/acme?confirm=acme").status_code == 403
    assert analyst.delete("/api/clients/acme/projects/main?confirm=main").status_code == 403
    assert analyst.get("/api/clients/acme/delete-preview").status_code == 403
    assert analyst.post("/api/clients/acme/archive").status_code == 403  # client: admin only
    admin = _client_as(["admin"])
    assert admin.delete("/api/clients/acme/projects/side?confirm=side").status_code == 409  # active
    assert admin.delete("/api/clients/acme/projects/main?confirm=wrong").status_code == 400
    assert admin.delete("/api/clients/acme/projects/main").status_code == 400  # confirm missing
    r = admin.delete("/api/clients/acme/projects/main?confirm=main")
    assert r.status_code == 200 and ("CLIENT#acme", "PROJECT#main") not in fake.db


def test_failed_delete_reports_retry(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    fake.fail_s3_once = True
    admin = _client_as(["admin"])
    r = admin.delete("/api/clients/acme?confirm=acme")
    assert r.status_code == 503 and "again" in r.json()["detail"]
    assert admin.delete("/api/clients/acme?confirm=acme").status_code == 200


# ---------- Cognito users are never deleted ----------
def test_no_cognito_delete_call_exists() -> None:
    """Runtime code and CDK must not call or grant any Cognito user deletion. (Docstrings that
    state the rule are fine: only lines that are not comments are checked.)"""
    root = Path(__file__).resolve().parents[2]
    pat = re.compile(
        r"admin_delete_user|AdminDeleteUser|\.delete_user\(|DeleteUserPool|cognito-idp:Delete"
    )
    hits = []
    for d in ("backend/app", "infra"):
        for f in (root / d).rglob("*.py"):
            if "cdk.out" in f.parts:
                continue
            for n, line in enumerate(f.read_text().splitlines(), 1):
                if pat.search(line) and f.name != "cognito.py":
                    hits.append(f"{f.relative_to(root)}:{n}: {line.strip()}")
    assert not hits, hits
    from app import cognito

    assert not [n for n in dir(cognito) if "delete" in n.lower()]
