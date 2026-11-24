"""Tableau import as a scaffold: structure mode never executes a query, iteration limit, datasource
prefix, reference rule, verify outcomes, query gate, publish guard. No AWS, BigQuery or Bedrock."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from app import routes_core, store
from app.auth import User, current_user
from app.main import app
from app.models import Element, ImportMeta, ViewMeta, ViewSpec
from app.query_runner import QueryRejected, dry_run_query, needs_confirmation
from app.tableau_import.agent import ImportCtx, make_dry_runner, structure_events, verify_result
from app.tableau_import.parser import _bq_name
from app.tableau_import.reconcile import pick_reference
from fastapi.testclient import TestClient

TABLE = "proj.ds.occ"
SQL = f"SELECT m, SAFE_DIVIDE(SUM(a), SUM(b)) AS occ FROM `{TABLE}` GROUP BY m"
SHEET = {
    "name": "Trend",
    "chart_type": "area",
    "status": "supported",
    "status_reason": "",
    "rows": [],
    "columns": [],
    "grid": {"x": 0, "y": 0, "w": 12, "h": 5},
}


# ---------- parser: datasource prefix ----------
def test_datasource_prefix_is_stripped() -> None:
    assert _bq_name("[StorageMart].[p-1].[ds].[tbl]", "", "") == "p-1.ds.tbl"
    assert _bq_name("StorageMart.p-1.ds.tbl", "", "") == "p-1.ds.tbl"
    assert _bq_name("[p-1].[ds].[tbl]", "", "") == "p-1.ds.tbl"
    assert _bq_name("[ds].[tbl]", "p-1", "") == "p-1.ds.tbl"


# ---------- dashboard CSV reference rule ----------
def field(name: str, measure: bool = False) -> dict[str, Any]:
    return {"field": name, "measure": measure}


def test_kpi_never_picks_up_a_monthly_reference() -> None:
    monthly = (["Month of Date", "Occupancy"], [["July 2025", "82%"]])
    refs = {"Dash": monthly}
    kpi = {
        "name": "Total",
        "rows": [field("Occupancy", True)],
        "columns": [],
        "dashboards": ["Dash"],
    }
    assert pick_reference(kpi, refs) is None
    heat = {
        "name": "Heat",
        "rows": [field("Facility")],
        "columns": [field("Date")],
        "dashboards": ["Dash"],
    }
    assert pick_reference(heat, refs) is None  # two dimensions
    trend = {
        "name": "Trend",
        "rows": [field("Occupancy", True)],
        "columns": [field("Date")],
        "dashboards": ["Dash"],
    }
    assert pick_reference(trend, refs) == monthly  # date-only sheet may use it
    assert pick_reference({**kpi, "name": "Dash"}, refs) == monthly  # own CSV always wins


# ---------- structure mode: dry runs only ----------
class FakeDry:
    """Stands in for the BigQuery dry run. Any attempt to EXECUTE a query would call `execute`."""

    def __init__(self, fail_first: bool = False) -> None:
        self.calls = 0
        self.executed = 0
        self.fail_first = fail_first

    def __call__(self, sql: str) -> dict[str, Any]:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise QueryRejected("Unrecognized name: z at [1:8]")
        schema = [{"name": "m", "type": "DATE"}, {"name": "occ", "type": "FLOAT"}]
        return {
            "columns": ["m", "occ"], "schema": schema, "rows": [], "truncated": False,
            "bytes_processed": 123456, "dry_run": True, "source_is_view": True,
            "base_tables": ["radar.snow.big"],
        }  # fmt: skip

    def execute(self, sql: str) -> dict[str, Any]:  # pragma: no cover - must never be called
        self.executed += 1
        raise AssertionError("structure mode executed a query")


def tool(name: str, inp: dict[str, Any], n: int = 0) -> dict[str, Any]:
    return {"toolUse": {"toolUseId": f"t{n}", "name": name, "input": inp}}


def scripted(*turns: list[dict[str, Any]]) -> Any:
    it: Iterator[list[dict[str, Any]]] = iter(turns)
    seen: list[list[dict[str, Any]]] = []

    def model(system: str, messages: list[Any], max_tokens: int) -> Iterator[dict[str, Any]]:
        seen.append(messages)
        content = next(it, [{"text": "done"}])
        uses = [b for b in content if "toolUse" in b]
        yield {"end": {"content": content, "stop": "tool_use" if uses else "end_turn"}}

    model.calls = seen  # type: ignore[attr-defined]
    return model


def last_tool_result(model: Any) -> dict[str, Any]:
    msgs = model.calls[-1]  # the (shared, mutated) conversation
    blocks = [b for m in msgs for b in m["content"] if "toolResult" in b]
    return blocks[-1]["toolResult"]["content"][0]["json"]


def make_ctx(dry: FakeDry) -> ImportCtx:
    info: dict[str, dict[str, Any]] = {}
    return ImportCtx(
        tables=[TABLE],
        schemas={},
        spec=ViewSpec(view=ViewMeta(name="v", slug="v")),
        run=make_dry_runner(dry, info),
        dry=True,
        sheet=SHEET,
        dry_info=info,
    )


ELEMENT = {
    "type": "chart",
    "title": "Occupancy",
    "sql": SQL,
    "chart": {"kind": "area", "x": "m", "y": ["occ"]},
}
REPORT = {
    "status": "unverified",
    "assumptions": ["a"],
    "filters_baked": ["Country = MMS"],
    "warnings": [],
}


def run_events(model: Any, dry: FakeDry, **kw: Any) -> list[dict[str, Any]]:
    ctx = make_ctx(dry)
    return list(
        structure_events(
            ctx, SHEET, {"worksheets": [SHEET], "dashboards": []}, model_fn=model,
            max_iterations=kw.pop("max_iterations", 8), timeout_seconds=30, **kw,
        )
    )  # fmt: skip


def test_structure_mode_dry_runs_only_and_scans_zero_bytes() -> None:
    dry = FakeDry()
    model = scripted(
        [tool("validate_sql", {"sql": SQL}, 1)],
        [tool("apply_changes", {"operations": [{"op": "add_element", "element": ELEMENT}]}, 2)],
        [tool("submit_report", REPORT, 3)],
        [{"text": "Built it."}],
    )
    evs = run_events(model, dry)
    assert dry.executed == 0 and dry.calls == 2  # validate + apply's check, both dry
    rep = next(e for e in evs if e["type"] == "report")["entry"]
    assert rep["bytes_scanned"] == 0 and rep["dry_run_bytes"] == 123456
    assert rep["status"] == "unverified" and rep["mode"] == "structure"
    assert [c["name"] for c in rep["result_columns"]] == ["m", "occ"]
    assert rep["not_interactive_yet"] and rep["filters_baked"] == ["Country = MMS"]
    ops = next(e for e in evs if e["type"] == "changes")["operations"]
    el = Element(**ops[0]["element"])
    assert el.import_meta and el.import_meta.status == "unverified"
    assert el.import_meta.source_is_view and el.import_meta.base_tables == ["radar.snow.big"]
    assert el.import_meta.dry_run_bytes == 123456 and el.cache_ttl_seconds == 21600
    assert ops[0]["layout"] == {"x": 0, "y": 0, "w": 12, "h": 5}
    assert any("view over radar.snow.big" in n for n in el.import_meta.notes)


def test_dry_run_error_message_goes_back_to_the_model() -> None:
    dry = FakeDry(fail_first=True)
    model = scripted([tool("validate_sql", {"sql": SQL}, 1)], [{"text": "stop"}])
    run_events(model, dry)
    result = last_tool_result(model)
    assert "Unrecognized name" in result["error"]


def test_run_query_is_not_available_in_structure_mode() -> None:
    dry = FakeDry()
    model = scripted([tool("run_query", {"sql": SQL}, 1)], [{"text": "ok"}])
    run_events(model, dry)
    result = last_tool_result(model)
    assert "not available" in result["error"] and dry.calls == 0


def test_iteration_limit_is_four_rounds() -> None:
    dry = FakeDry()
    loop = [tool("validate_sql", {"sql": SQL}, 1)]
    model = scripted(*[loop] * 10)
    evs = run_events(model, dry, max_iterations=50)  # the cap is min(setting, 4)
    assert dry.calls == 4
    assert any(e["type"] == "error" and "4 tool iterations" in e["message"] for e in evs)
    rep = next(e for e in evs if e["type"] == "report")["entry"]
    assert rep["status"] == "skipped" and rep["bytes_scanned"] == 0


def test_needs_review_for_approximate_sheets() -> None:
    dry = FakeDry()
    rep = {**REPORT, "status": "needs_review", "reason": "LOD expression approximated"}
    model = scripted(
        [tool("apply_changes", {"operations": [{"op": "add_element", "element": ELEMENT}]}, 1)],
        [tool("submit_report", rep, 2)],
    )
    evs = run_events(model, dry)
    entry = next(e for e in evs if e["type"] == "report")["entry"]
    assert entry["status"] == "needs_review"
    assert any("LOD expression" in n for n in entry["notes"])


def test_model_cannot_set_import_or_review_fields() -> None:
    dry = FakeDry()
    el = {**ELEMENT, "review_status": "reviewed", "import": {"status": "verified"}}
    model = scripted(
        [tool("apply_changes", {"operations": [{"op": "add_element", "element": el}]}, 1)],
        [tool("submit_report", REPORT, 2)],
    )
    evs = run_events(model, dry)
    op = next(e for e in evs if e["type"] == "changes")["operations"][0]["element"]
    assert op["import"]["status"] == "unverified" and "review_status" not in op


# ---------- dry_run_query itself ----------
class FakeBQ:
    def __init__(self) -> None:
        self.configs: list[Any] = []

    def query(self, sql: str, job_config: Any = None) -> Any:
        self.configs.append(job_config)
        schema = [SimpleNamespace(name="n", field_type="INTEGER")]
        return SimpleNamespace(schema=schema, total_bytes_processed=42)

    def get_table(self, fqn: str) -> Any:
        return SimpleNamespace(
            table_type="VIEW", view_query="SELECT * FROM `radar.snow.big` JOIN `radar.snow.dim`"
        )


def test_dry_run_query_is_a_dry_run_and_reports_view_bases() -> None:
    cfg = SimpleNamespace(bq_project="proj", tables=[TABLE])
    bq = FakeBQ()
    r = dry_run_query(cfg, f"SELECT 1 AS n FROM `{TABLE}`", client=bq)  # type: ignore[arg-type]
    assert bq.configs[0].dry_run is True
    assert r["rows"] == [] and r["bytes_processed"] == 42 and r["source_is_view"]
    assert r["base_tables"] == ["radar.snow.big", "radar.snow.dim"]
    with pytest.raises(QueryRejected):  # same allowlist as real runs
        dry_run_query(cfg, "SELECT 1 FROM `other.ds.t`", client=bq)  # type: ignore[arg-type]


def test_confirmation_gate() -> None:
    assert needs_confirmation({"bytes_processed": 10, "source_is_view": False}, 100) is None
    assert needs_confirmation({"bytes_processed": 1000, "source_is_view": False}, 100)
    g = needs_confirmation(
        {"bytes_processed": 10, "source_is_view": True, "base_tables": ["a"]}, 100
    )
    assert g and g["source_is_view"] and "materialized" in g["suggestion"]


# ---------- verify outcomes (pure) ----------
def imported(status: str = "unverified") -> Element:
    return Element(
        id="el_1",
        type="kpi",
        sql=SQL,
        import_meta=ImportMeta(sheet_name="Trend", status=status),  # type: ignore[arg-type]
    )


def test_verify_outcomes() -> None:
    _, ops = verify_result(imported(), SHEET, {"result": "matches", "stats": {"ok": True}})
    assert ops[0]["element"]["import"]["status"] == "verified"
    _, ops = verify_result(
        imported(), SHEET, {"result": "differs", "stats": {"mismatched_rows": 3}}
    )
    assert ops[0]["element"]["import"]["status"] == "differs"
    _, ops = verify_result(imported(), SHEET, {"result": "no reference", "detail": "none"})
    assert ops[0]["element"]["import"]["status"] == "unverified"
    _, ops = verify_result(imported("needs_review"), SHEET, {"result": "matches", "stats": {}})
    assert ops[0]["element"]["import"]["status"] == "needs_review"  # a match doesn't clear it


# ---------- publish guard ----------
def spec_dict(status: str | None, reviewed: bool = False) -> dict[str, Any]:
    el: dict[str, Any] = {"id": "a", "type": "kpi", "title": "N", "sql": "SELECT 1 FROM `p.d.t`"}
    if status:
        el["import"] = {"source": "tableau", "sheet_name": "S", "status": status}
    if reviewed:
        el["review_status"] = "reviewed"
    return {"schema_version": 1, "view": {"name": "v", "slug": "v"}, "elements": [el], "layout": []}


@pytest.fixture
def pub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    st: dict[str, Any] = {"spec": spec_dict("unverified"), "published": 0, "audits": []}
    s = monkeypatch.setattr
    s(routes_core, "need_view", lambda c, p, v, mode="read": {"draft_revision": 1})
    s(store, "read_revision", lambda c, p, v, n: st["spec"])
    s(store, "get_project_config", lambda c, p: SimpleNamespace(tables=["p.d.t"]))

    def fake_publish(*a: Any) -> int:
        st["published"] += 1
        return 1

    s(store, "publish", fake_publish)
    s(routes_core, "audit", lambda who, action, target, client=None: st["audits"].append(action))
    app.dependency_overrides[current_user] = lambda: User(
        sub="a", email="a@x.io", groups=["analyst"]
    )
    yield st
    app.dependency_overrides.clear()


URL = "/api/clients/c/projects/p/views/v/publish"


@pytest.mark.parametrize("status", ["unverified", "needs_review", "differs"])
def test_publish_guard_blocks_unreviewed(pub: dict[str, Any], status: str) -> None:
    pub["spec"] = spec_dict(status)
    r = TestClient(app).post(URL, json={})
    assert r.status_code == 409 and pub["published"] == 0
    d = r.json()["detail"]
    assert d["reason"] == "unreviewed_imports" and d["elements"][0]["id"] == "a"


def test_publish_guard_confirm_is_audited(pub: dict[str, Any]) -> None:
    r = TestClient(app).post(URL, json={"confirm_unreviewed": True})
    assert r.status_code == 200 and pub["published"] == 1
    assert "publish_unreviewed_confirmed" in pub["audits"]


def test_publish_guard_skips_reviewed_verified_and_plain(pub: dict[str, Any]) -> None:
    for spec in (spec_dict("unverified", reviewed=True), spec_dict("verified"), spec_dict(None)):
        pub["spec"] = spec
        assert TestClient(app).post(URL, json={}).status_code == 200
    assert pub["audits"] == []


def test_portal_strips_import_metadata() -> None:
    out = routes_core.strip_sql(spec_dict("unverified", reviewed=True))
    el = out["elements"][0]
    assert el["sql"] == "" and "import" not in el and "review_status" not in el
