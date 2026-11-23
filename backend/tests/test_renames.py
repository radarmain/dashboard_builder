"""Slug validation, alias resolution and the idempotent move logic. Offline: DynamoDB and S3 are
replaced by an in-memory fake (renames.py funnels all AWS access through a few helpers)."""

from typing import Any

import pytest
from app import renames, store
from app.models import ProjectPatch, ViewPatch
from pydantic import ValidationError

C = "acme"


class Fake:
    def __init__(self) -> None:
        self.db: dict[tuple[str, str], dict[str, Any]] = {}
        self.s3: dict[str, int] = {}
        self.fail_tx_at: int | None = None  # fail the Nth transaction (1-based), once
        self.tx_calls = 0
        self.corrupt_copy = False

    def get(self, key: dict[str, str]) -> dict[str, Any] | None:
        return self.db.get((key["PK"], key["SK"]))

    def query(self, pk: str, prefix: str) -> list[dict[str, Any]]:
        return [v for (p, s), v in sorted(self.db.items()) if p == pk and s.startswith(prefix)]

    def transact(self, ops: list[dict[str, Any]]) -> None:
        self.tx_calls += 1
        assert len(ops) <= renames.TX_LIMIT
        if self.fail_tx_at == self.tx_calls:
            self.fail_tx_at = None
            raise renames.RenameFailed("boom")
        keys = [
            (o["item"]["PK"], o["item"]["SK"])
            if o["op"] == "put"
            else (o["key"]["PK"], o["key"]["SK"])
            for o in ops
        ]
        assert len(keys) == len(set(keys)), "duplicate key in one transaction"
        for o in ops:  # check every condition first (all-or-nothing)
            if o["op"] == "put" and o.get("new") and self.get(o["item"]):
                raise renames.RenameFailed("exists")
            if o["op"] in ("delete", "mark"):
                cur = self.get(o["key"])
                if cur is None:
                    raise renames.RenameFailed("missing")
                if any(cur.get(k) != v for k, v in (o.get("expect") or {}).items()):
                    raise renames.RenameFailed("changed")
        for o in ops:
            if o["op"] == "put":
                self.db[(o["item"]["PK"], o["item"]["SK"])] = dict(o["item"])
            elif o["op"] == "delete":
                del self.db[(o["key"]["PK"], o["key"]["SK"])]
            else:
                self.db[(o["key"]["PK"], o["key"]["SK"])]["rename_in_progress"] = o["value"]

    def s3_list(self, prefix: str) -> dict[str, int]:
        return {k: v for k, v in self.s3.items() if k.startswith(prefix)}

    def s3_copy(self, src: str, dst: str) -> None:
        self.s3[dst] = self.s3[src] + (1 if self.corrupt_copy else 0)

    def s3_delete(self, keys: list[str]) -> None:
        for k in keys:
            self.s3.pop(k, None)

    def add_view(self, p: str, v: str, rev: int = 3) -> None:
        self.db[(f"CLIENT#{C}", f"VIEW#{p}#{v}")] = {
            "PK": f"CLIENT#{C}",
            "SK": f"VIEW#{p}#{v}",
            "client_slug": C,
            "project_slug": p,
            "slug": v,
            "name": v.title(),
            "rev_counter": rev,
            "draft_revision": rev,
            "published_revision": rev - 1,
            "published_summary": {"counts": {"chart": 1}},
            "updated_at": "2026-01-01T00:00:00Z",
        }
        pre = store.view_prefix(C, p, v)
        for n in range(1, rev + 1):
            self.s3[f"{pre}revisions/{n:06d}.json"] = 100 + n
        self.s3[pre + "draft.json"] = 15
        self.s3[pre + "published.json"] = 40

    def add_project(self, p: str) -> None:
        self.db[(f"CLIENT#{C}", f"PROJECT#{p}")] = {
            "PK": f"CLIENT#{C}",
            "SK": f"PROJECT#{p}",
            "client_slug": C,
            "slug": p,
            "name": p.title(),
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
        }
        self.s3[store.project_prefix(C, p) + "project.json"] = 50


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    f = Fake()
    monkeypatch.setattr(renames, "_get_item", f.get)
    monkeypatch.setattr(renames, "_query", f.query)
    monkeypatch.setattr(renames, "_transact", f.transact)
    monkeypatch.setattr(renames, "_s3_list", f.s3_list)
    monkeypatch.setattr(renames, "_s3_copy", f.s3_copy)
    monkeypatch.setattr(renames, "_s3_delete", f.s3_delete)
    return f


# ---------- slug validation ----------
@pytest.mark.parametrize("slug", ["a", "q3-report", "x" * 40, "v2"])
def test_good_slugs(slug: str) -> None:
    assert ViewPatch(slug=slug).slug == slug
    assert ProjectPatch(slug=slug).slug == slug


@pytest.mark.parametrize("slug", ["", "-a", "a-", "a--b", "A", "a_b", "a b", "x" * 41])
def test_bad_slugs(slug: str) -> None:
    with pytest.raises(ValidationError):
        ViewPatch(slug=slug)
    with pytest.raises(ValidationError):
        ProjectPatch(slug=slug)


def test_name_only_patch_has_no_slug() -> None:
    assert ViewPatch(name="x").slug is None and ProjectPatch(name="x").slug is None


# ---------- view rename ----------
def test_rename_view_moves_everything_and_keeps_old_link(fake: Fake) -> None:
    fake.add_project("main")
    fake.add_view("main", "sales")
    renames.rename_view(C, "main", "sales", "revenue", "sub-1")

    new = fake.get({"PK": f"CLIENT#{C}", "SK": "VIEW#main#revenue"})
    assert new and new["slug"] == "revenue" and new["rev_counter"] == 3
    assert new["draft_revision"] == 3 and new["published_revision"] == 2
    assert new["published_summary"] == {"counts": {"chart": 1}}
    assert fake.get({"PK": f"CLIENT#{C}", "SK": "VIEW#main#sales"}) is None
    pre = store.view_prefix(C, "main", "revenue")
    assert {k for k in fake.s3 if k.startswith(pre)} == {
        pre + f"revisions/{n:06d}.json" for n in (1, 2, 3)
    } | {pre + "draft.json", pre + "published.json"}
    assert not fake.s3_list(store.view_prefix(C, "main", "sales"))  # old objects removed
    assert renames.resolve(C, "main", "sales") == ("main", "revenue")
    assert renames.resolve(C, "main", "revenue") == ("main", "revenue")


def test_rename_view_failure_is_safe_to_retry(fake: Fake) -> None:
    fake.add_view("main", "sales")
    fake.fail_tx_at = 1
    with pytest.raises(renames.RenameFailed):
        renames.rename_view(C, "main", "sales", "revenue", "s")
    # old slug fully intact, nothing aliased
    assert fake.get({"PK": f"CLIENT#{C}", "SK": "VIEW#main#sales"})
    assert renames.resolve(C, "main", "sales") == ("main", "sales")
    assert fake.s3_list(store.view_prefix(C, "main", "sales"))
    renames.rename_view(C, "main", "sales", "revenue", "s")  # retry works
    assert renames.resolve(C, "main", "sales") == ("main", "revenue")


def test_copy_verification_failure_changes_nothing(fake: Fake) -> None:
    fake.add_view("main", "sales")
    fake.corrupt_copy = True
    with pytest.raises(renames.RenameFailed):
        renames.rename_view(C, "main", "sales", "revenue", "s")
    assert fake.get({"PK": f"CLIENT#{C}", "SK": "VIEW#main#sales"})
    assert fake.tx_calls == 0
    assert fake.s3_list(store.view_prefix(C, "main", "sales"))


def test_view_collisions_and_reclaim(fake: Fake) -> None:
    fake.add_view("main", "a")
    fake.add_view("main", "b")
    with pytest.raises(renames.SlugConflict):  # live view
        renames.rename_view(C, "main", "a", "b", "s")
    renames.rename_view(C, "main", "a", "a2", "s")  # alias a -> a2
    with pytest.raises(renames.SlugConflict):  # another view can't take a's old link
        renames.rename_view(C, "main", "b", "a", "s")
    renames.rename_view(C, "main", "a2", "a", "s")  # the same view may take it back
    assert fake.get({"PK": f"CLIENT#{C}", "SK": "ALIAS#VIEW#main#a"}) is None
    assert renames.resolve(C, "main", "a2") == ("main", "a")
    assert not renames.view_slug_free(C, "main", "a2")  # alias now blocks creation of a2


# ---------- project rename + chained aliases ----------
def test_rename_project_rekeys_views_and_chains_aliases(fake: Fake) -> None:
    fake.add_project("main")
    fake.add_view("main", "sales")
    fake.add_view("main", "ops")
    fake.db[(f"CLIENT#{C}", "IMPORT#main#abc")] = {"PK": f"CLIENT#{C}", "SK": "IMPORT#main#abc"}
    renames.rename_view(C, "main", "sales", "revenue", "s")
    renames.rename_project(C, "main", "reports", "s")

    assert fake.get({"PK": f"CLIENT#{C}", "SK": "PROJECT#main"}) is None
    proj = fake.get({"PK": f"CLIENT#{C}", "SK": "PROJECT#reports"})
    assert proj and proj["slug"] == "reports" and proj["name"] == "Main"
    v = fake.get({"PK": f"CLIENT#{C}", "SK": "VIEW#reports#revenue"})
    assert v and v["project_slug"] == "reports" and v["rev_counter"] == 3
    assert fake.get({"PK": f"CLIENT#{C}", "SK": "IMPORT#reports#abc"})
    assert fake.get({"PK": f"CLIENT#{C}", "SK": "VIEW#main#ops"}) is None
    assert not fake.s3_list(store.project_prefix(C, "main"))
    assert fake.s3_list(store.view_prefix(C, "reports", "ops"))
    # old project + old view slug chain to the canonical pair; a second rename keeps chaining
    assert renames.resolve(C, "main", "sales") == ("reports", "revenue")
    assert renames.resolve(C, "main", "ops") == ("reports", "ops")
    renames.rename_project(C, "reports", "final", "s")
    assert renames.resolve(C, "main", "sales") == ("final", "revenue")
    with pytest.raises(renames.SlugConflict):
        fake.add_project("other")
        renames.rename_project(C, "other", "main", "s")  # "main" is final's old link


def test_big_project_moves_in_batches_and_resumes(fake: Fake) -> None:
    fake.add_project("big")
    for i in range(60):  # 4 + 2*60 > 100 operations: needs batching
        fake.add_view("big", f"v{i:02d}", rev=1)
    fake.fail_tx_at = 3  # mark, batch 1 ok, batch 2 fails
    with pytest.raises(renames.RenameFailed, match="partly done"):
        renames.rename_project(C, "big", "huge", "s")
    old = fake.get({"PK": f"CLIENT#{C}", "SK": "PROJECT#big"})
    assert old and old["rename_in_progress"] == "huge"
    assert fake.get({"PK": f"CLIENT#{C}", "SK": "PROJECT#huge"}) is None
    with pytest.raises(renames.SlugConflict):  # a different target is refused until it finishes
        renames.rename_project(C, "big", "other", "s")

    renames.rename_project(C, "big", "huge", "s")  # same rename again: resumes
    assert fake.get({"PK": f"CLIENT#{C}", "SK": "PROJECT#big"}) is None
    new = fake.get({"PK": f"CLIENT#{C}", "SK": "PROJECT#huge"})
    assert new and "rename_in_progress" not in new
    assert len(fake.query(f"CLIENT#{C}", "VIEW#huge#")) == 60
    assert not fake.query(f"CLIENT#{C}", "VIEW#big#")
    assert renames.resolve(C, "big", "v07") == ("huge", "v07")


# ---------- Phase 9 data keyed by the project slug (Tableau link, imports, budget) ----------
def test_rename_project_moves_tableau_link_import_report_and_budget(fake: Fake) -> None:
    fake.add_project("main")
    pre = store.project_prefix(C, "main")
    keys = [  # project.json holds the Tableau link; imports/ holds plan, report, reference CSVs
        "imports/abc/plan.json",
        "imports/abc/report.json",
        "imports/abc/reference/index.json",
        "imports/abc/reference/0.csv",
        "schemas/ds.tbl.json",
    ]
    for i, k in enumerate(keys):
        fake.s3[pre + k] = 10 + i
    budget = {
        "PK": f"CLIENT#{C}",
        "SK": "IMPORT#main#abc",
        "scanned_bytes": 123456,
        "updated_at": "2026-01-01T00:00:00Z",
    }
    fake.db[(budget["PK"], budget["SK"])] = dict(budget)

    fake.fail_tx_at = 1  # first attempt fails: nothing changes, retry works (idempotent)
    with pytest.raises(renames.RenameFailed):
        renames.rename_project(C, "main", "reports", "s")
    assert fake.get({"PK": f"CLIENT#{C}", "SK": "IMPORT#main#abc"})
    renames.rename_project(C, "main", "reports", "s")

    new_pre = store.project_prefix(C, "reports")
    for k in ["project.json", *keys]:
        assert new_pre + k in fake.s3, k
    assert not fake.s3_list(pre)
    moved = fake.get({"PK": f"CLIENT#{C}", "SK": "IMPORT#reports#abc"})
    assert moved and moved["scanned_bytes"] == 123456  # budget carried over, not reset
    assert fake.get({"PK": f"CLIENT#{C}", "SK": "IMPORT#main#abc"}) is None
    assert renames.resolve(C, "main") == ("reports", None)


def test_every_project_route_of_the_import_api_resolves_old_slugs() -> None:
    """Old project slugs must keep working: each import route calls canon() before touching data."""
    import ast
    from pathlib import Path

    import app.routes_tableau_import as mod

    tree = ast.parse(Path(mod.__file__).read_text())
    handlers = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.decorator_list
        and any(a.arg == "p" for a in n.args.args)
    ]
    assert len(handlers) >= 6
    for fn in handlers:
        calls = [
            c.func.id
            for c in ast.walk(fn)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        ]
        assert "canon" in calls, f"{fn.name} does not resolve old project slugs"
