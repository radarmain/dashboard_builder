"""Tableau import: a SCAFFOLDING tool. It gives analysts a draft (SQL, chart config, filters,
layout) that they edit and verify. Numeric agreement with Tableau is informational only.

STRUCTURE (default): the LLM translates one worksheet literally using ONLY dry runs (validation +
BigQuery dry run: zero bytes scanned, no rows). At most 4 tool rounds. Elements are saved with
import metadata, status "unverified" (or "needs_review").

VERIFY (opt-in): plain code, no LLM. Runs the element's query once within the byte budget and
compares it with the Tableau reference CSV once. A difference is a finding; nothing is retried.
"""

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from app import store
from app.chat_agent import ModelFn, bedrock_model_fn, run_chat
from app.chat_tools import ToolContext, apply_operations
from app.config import get_settings
from app.models import Element, ImportMeta, ViewSpec
from app.query_runner import (
    QueryFailed,
    QueryRejected,
    dry_run_query,
    needs_confirmation,
    run_query,
)
from app.routes_gbq import schema_key
from app.tableau_import import imports_store as ist
from app.tableau_import.budget import BudgetExhausted, ImportBudget, ddb_counter
from app.tableau_import.prompt import CACHE_TTL, IMPORT_TOOLS, build_prompt
from app.tableau_import.reconcile import compare_tables, parse_csv, pick_reference

log = logging.getLogger("rdb.import")
ROW_CAP = 5000
STRUCTURE_MAX_ITERATIONS = 4  # tool rounds per sheet
MAX_NOTES = 20

# dry(cfg-bound) runner: sql -> {columns, schema, rows: [], bytes_processed, source_is_view, ...}
DryFn = Callable[[str], dict[str, Any]]


@dataclass
class ImportCtx(ToolContext):
    sheet: dict[str, Any] = field(default_factory=dict)
    element_id: str | None = None
    report: dict[str, Any] | None = None
    dry_info: dict[str, dict[str, Any]] = field(default_factory=dict)  # raw sql -> dry-run result
    dry_runs: int = 0


def make_dry_runner(dry: DryFn, info: dict[str, dict[str, Any]]) -> Callable[[str], dict[str, Any]]:
    """The ONLY thing structure mode can do against BigQuery. Remembers each dry-run result."""

    def run(sql: str) -> dict[str, Any]:
        res = dry(sql)
        info[sql] = res
        return res

    return run


# ---------- tools (structure mode) ----------
def _validate(ctx: ImportCtx, args: dict[str, Any]) -> dict[str, Any]:
    ctx.dry_runs += 1
    try:
        r = ctx.run(str(args.get("sql", "")))
    except (QueryRejected, QueryFailed) as e:
        return {"error": str(e)}
    return {
        "ok": True,
        "columns": r["schema"],
        "estimated_bytes": r["bytes_processed"],
        "source_is_view": r["source_is_view"],
        "base_tables": r["base_tables"],
        "note": "Dry run only: nothing was executed, no rows are available.",
    }


def _apply(ctx: ImportCtx, args: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validates via dry run and mutates ctx.spec. Nothing is emitted yet: the final element
    (with import metadata and status) is emitted once at the end of the run."""
    ops: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for i, op in enumerate(args.get("operations") or []):
        kind = op.get("op") if isinstance(op, dict) else None
        if kind == "add_element":
            if ctx.element_id:
                msg = "this run builds exactly ONE element; use update_element"
                rejected.append({"index": i, "ok": False, "error": msg})
                continue
            el = {k: v for k, v in dict(op.get("element") or {}).items() if k not in OWNED}
            el["cache_ttl_seconds"] = CACHE_TTL
            op = {**op, "element": el}
            if g := ctx.sheet.get("grid"):
                op["layout"] = dict(g)
        elif kind == "update_element" and op.get("id") == ctx.element_id:
            ch = {k: v for k, v in dict(op.get("changes") or {}).items() if k not in OWNED}
            op = {**op, "changes": {**ch, "cache_ttl_seconds": CACHE_TTL}}
        else:
            rejected.append(
                {
                    "index": i,
                    "ok": False,
                    "error": "only add_element / update_element of the element built in this run",
                }
            )
            continue
        ops.append(op)
    ctx.dry_runs += len(ops)
    accepted, result = apply_operations(ctx, ops)
    for a in accepted:
        if a["op"] == "add_element":
            ctx.element_id = a["element"]["id"]
    result["results"] += rejected
    return result, []


OWNED = {"import", "import_meta", "review_status"}  # set by the system, never by the model


def _submit(ctx: ImportCtx, args: dict[str, Any]) -> dict[str, Any]:
    lists = {
        k: [str(x)[:400] for x in (args.get(k) or [])][:30]
        for k in ("assumptions", "filters_baked", "warnings")
    }
    status = args.get("status")
    ctx.report = {
        "status": status if status in ("unverified", "needs_review", "skipped") else None,
        "reason": str(args.get("reason") or "")[:600],
        **lists,
    }
    return {"ok": True}


def dispatch(
    ctx: ToolContext, name: str, args: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    assert isinstance(ctx, ImportCtx)
    try:
        if name == "validate_sql":
            return _validate(ctx, args), []
        if name == "apply_changes":
            return _apply(ctx, args)
        if name == "submit_report":
            return _submit(ctx, args), []
    except Exception as e:  # noqa: BLE001
        log.warning("import tool %s crashed: %s", name, type(e).__name__)
        return {"error": f"tool failed: {type(e).__name__}"}, []
    return {"error": f"tool {name!r} is not available: structure mode only dry-runs"}, []


# ---------- finishing: import metadata, status, report entry ----------
def _trim(items: list[str]) -> list[str]:
    return [s[:400] for s in items if s][:MAX_NOTES]


def _finish(
    ctx: ImportCtx, sheet: dict[str, Any], errors: list[str]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """-> (report entry, ops to emit). Attaches import metadata to the built element."""
    rep = ctx.report or {}
    el = next((e for e in ctx.spec.elements if e.id == ctx.element_id), None)
    info = ctx.dry_info.get(el.sql, {}) if el else {}
    plan_reason = sheet.get("status_reason", "")
    notes: list[str] = []
    review = False
    if sheet["status"] == "approximate":
        review = True
        notes.append(f"Approximate translation: {plan_reason}")
    if rep.get("status") == "needs_review":
        review = True
        notes.append(f"Needs review: {rep.get('reason') or 'see warnings'}")
    if ctx.report is None:
        review = True
        notes.append("The agent did not submit a report; check this element carefully.")
    notes += [f"Filter baked in (not interactive yet): {f}" for f in rep.get("filters_baked", [])]
    notes += rep.get("assumptions", []) + rep.get("warnings", []) + errors
    if info.get("source_is_view"):
        bt = ", ".join(info.get("base_tables", [])) or "unknown base tables"
        notes.append(
            f"Source is a view over {bt}: each run re-executes it. Consider a materialized table."
        )
    ops: list[dict[str, Any]] = []
    if el:
        status = "needs_review" if review else "unverified"
        meta = ImportMeta(
            sheet_name=sheet["name"],
            status=status,
            notes=_trim(notes),
            dry_run_bytes=info.get("bytes_processed"),
            source_is_view=bool(info.get("source_is_view")),
            base_tables=info.get("base_tables", []),
        )
        el = el.model_copy(update={"import_meta": meta})
        ctx.spec.elements = [el if e.id == el.id else e for e in ctx.spec.elements]
        item = next(i for i in ctx.spec.layout if i.element_id == el.id)
        ops = [
            {
                "op": "add_element",
                "element": el.model_dump(),
                "layout": item.model_dump(exclude={"element_id"}),
            }
        ]
    else:
        status = "skipped"
        notes.append("No element was built: " + (rep.get("reason") or "see warnings"))
    config = (
        {
            k: v
            for k, v in el.model_dump().items()
            if k in ("type", "chart", "pivot", "heatmap", "format") and v
        }
        if el
        else None
    )
    entry = {
        "sheet": sheet["name"],
        "mode": "structure",
        "plan_status": sheet["status"],
        "status": status,
        "status_reason": rep.get("reason") or plan_reason,
        "element_id": ctx.element_id,
        "chart_type": sheet.get("chart_type"),
        "sql": el.sql if el else None,
        "result_columns": info.get("schema", []),
        "dry_run_bytes": info.get("bytes_processed"),
        "source_is_view": bool(info.get("source_is_view")),
        "base_tables": info.get("base_tables", []),
        "config": config,
        "filters_baked": rep.get("filters_baked", []),
        "not_interactive_yet": bool(rep.get("filters_baked")),
        "assumptions": rep.get("assumptions", []),
        "notes": _trim(notes),
        "warnings": rep.get("warnings", []) + errors,
        "reconciliation": {"result": "not run (structure mode; verify is opt-in)"},
        "bytes_scanned": 0,
        "created_at": store.now(),
    }
    return entry, ops


def structure_events(
    ctx: ImportCtx,
    sheet: dict[str, Any],
    plan: dict[str, Any],
    *,
    view_name: str = "",
    model_fn: ModelFn,
    max_iterations: int = STRUCTURE_MAX_ITERATIONS,
    max_tokens: int = 4096,
    timeout_seconds: float = 120,
    notes: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """The structure-mode loop (no IO besides the injected dry runner and model)."""
    errors: list[str] = list(notes or [])
    for ev in run_chat(
        ctx, view_name, f"Build the element for worksheet {sheet['name']!r}.", [], model_fn,
        max_iterations=min(max_iterations, STRUCTURE_MAX_ITERATIONS),
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        system=build_prompt(ctx, sheet, plan), dispatch=dispatch,
    ):  # fmt: skip
        if ev["type"] == "done":
            break
        if ev["type"] == "error":
            errors.append(ev["message"])
        yield ev
    entry, ops = _finish(ctx, sheet, errors)
    if ops:
        yield {"type": "changes", "operations": ops}
    yield {"type": "report", "entry": entry}
    yield {"type": "done"}


# ---------- orchestration ----------
def _context(c: str, p: str, spec: ViewSpec, cfg: Any) -> dict[str, Any]:
    schemas: dict[str, dict[str, Any]] = {}
    for fqn in cfg.tables:
        _, dataset, table = fqn.split(".", 2)
        if snap := store.s3_get_json(schema_key(c, p, dataset, table)):
            schemas[fqn] = snap
    return {"tables": list(cfg.tables), "schemas": schemas, "spec": spec.model_copy(deep=True)}


def _load(
    c: str, p: str, import_id: str, sheet_name: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | str:
    saved = ist.get_plan(c, p, import_id)
    if not saved:
        return "import not found: load the plan first"
    sheet = next((w for w in saved["plan"]["worksheets"] if w["name"] == sheet_name), None)
    if not sheet:
        return f"worksheet {sheet_name!r} is not in the plan"
    return saved["plan"], saved["link"], sheet


def run_sheet(
    c: str,
    p: str,
    import_id: str,
    sheet_name: str,
    current_spec: ViewSpec,
    model_fn: ModelFn | None = None,
    *,
    view_name: str = "",
    dry_fn: DryFn | None = None,
) -> Iterator[dict[str, Any]]:
    """STRUCTURE mode for one sheet. Events: status/text/changes/error, `report` {entry}, done."""
    s = get_settings()
    yield {"type": "status", "text": "Loading import plan..."}
    loaded = _load(c, p, import_id, sheet_name)
    if isinstance(loaded, str):
        yield {"type": "error", "message": loaded}
        yield {"type": "done"}
        return
    plan, link, sheet = loaded
    if sheet["status"] == "unsupported":
        entry = {
            "sheet": sheet_name, "mode": "structure", "plan_status": "unsupported",
            "status": "skipped", "status_reason": sheet.get("status_reason", ""),
            "element_id": None, "sql": None, "filters_baked": [], "assumptions": [],
            "notes": [f"Skipped: {sheet.get('status_reason', 'unsupported')}"],
            "warnings": ["skipped: unsupported"],
            "reconciliation": {"result": "not run"}, "bytes_scanned": 0,
            "created_at": store.now(),
        }  # fmt: skip
        ist.add_report_entry(c, p, import_id, link, entry)
        yield {"type": "report", "entry": entry}
        yield {"type": "done"}
        return

    cfg = store.get_project_config(c, p)
    dry = dry_fn or partial(dry_run_query, cfg)
    info: dict[str, dict[str, Any]] = {}
    ctx = ImportCtx(
        run=make_dry_runner(dry, info),
        dry=True,
        sheet=sheet,
        dry_info=info,
        **_context(c, p, current_spec, cfg),
    )
    mf = model_fn or partial(bedrock_model_fn, tools=IMPORT_TOOLS)
    for ev in structure_events(
        ctx, sheet, plan, view_name=view_name, model_fn=mf,
        max_iterations=s.import_max_iterations,
        max_tokens=s.chat_max_output_tokens,
        timeout_seconds=s.import_timeout_seconds,
    ):  # fmt: skip
        if ev["type"] == "report":
            ist.add_report_entry(c, p, import_id, link, ev["entry"])
        yield ev


# ---------- VERIFY (opt-in, no LLM) ----------
def _find_element(spec: ViewSpec, sheet_name: str) -> Element | None:
    return next(
        (
            e
            for e in spec.elements
            if e.import_meta and e.import_meta.sheet_name == sheet_name and e.sql.strip()
        ),
        None,
    )


def verify_sheet(
    c: str,
    p: str,
    import_id: str,
    sheet_name: str,
    current_spec: ViewSpec,
    *,
    confirm: bool = False,
    dry_fn: DryFn | None = None,
    run_fn: Callable[..., dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Run the element's query ONCE (byte budget, confirmation gate) and compare it with the
    Tableau reference ONCE. Records matches / differs / no reference. Never searches for a match."""
    s = get_settings()
    loaded = _load(c, p, import_id, sheet_name)
    if isinstance(loaded, str):
        yield {"type": "error", "message": loaded}
        yield {"type": "done"}
        return
    plan, link, sheet = loaded
    el = _find_element(current_spec, sheet_name)
    if not el:
        yield {"type": "error", "message": f"no imported element for {sheet_name!r} in the draft"}
        yield {"type": "done"}
        return
    cfg = store.get_project_config(c, p)
    dry = dry_fn or partial(dry_run_query, cfg)
    yield {"type": "status", "text": f"{sheet_name}: estimating (dry run)..."}
    try:
        est = dry(el.sql)
    except (QueryRejected, QueryFailed) as e:
        yield {"type": "error", "message": f"{sheet_name}: {e}"}
        yield {"type": "done"}
        return
    if (gate := needs_confirmation(est, s.import_confirm_bytes)) and not confirm:
        yield {"type": "needs_confirmation", "sheet": sheet_name, **gate}
        yield {"type": "done"}
        return

    get_n, add_n = ddb_counter(c, p, import_id)
    cap = min(cfg.max_bytes_billed or s.default_max_bytes_billed, s.import_max_bytes_billed)
    runner = run_fn or partial(run_query, cfg, row_cap=ROW_CAP)
    budget = ImportBudget(
        runner,
        per_query_cap=cap,
        total_budget=s.import_run_budget_bytes,
        get_scanned=get_n,
        add_scanned=add_n,
    )
    yield {"type": "status", "text": f"{sheet_name}: running the query once..."}
    verify: dict[str, Any]
    try:
        res = budget(el.sql)
    except (QueryRejected, QueryFailed, BudgetExhausted) as e:
        res = None
        verify = {"result": "error", "detail": str(e)[:500]}
    if res is not None:
        view_names = [w["name"] for w in plan["worksheets"]] + [
            d["name"] for d in plan["dashboards"]
        ]
        refs, ref_notes = ist.load_references(c, p, import_id, link["workbook_id"], view_names)
        ref = pick_reference(sheet, {n: parse_csv(t) for n, t in refs.items()})
        if ref is None:
            verify = {"result": "no reference", "detail": "; ".join(ref_notes)}
        else:
            stats = compare_tables(*ref, res["columns"], res["rows"])
            if "error" in stats:
                verify = {"result": "no reference", "detail": f"not comparable: {stats['error']}"}
            else:
                verify = {"result": "matches" if stats.get("ok") else "differs", "stats": stats}
    entry, ops = verify_result(el, sheet, verify)
    entry["bytes_scanned"] = budget.session_bytes
    rep = ist.get_report(c, p, import_id) or {}
    entry = {**rep.get("sheets", {}).get(sheet_name, {}), **entry}
    ist.add_report_entry(c, p, import_id, link, entry)
    if ops:
        yield {"type": "changes", "operations": ops}
    yield {"type": "report", "entry": entry}
    yield {"type": "done"}


def verify_result(
    el: Element, sheet: dict[str, Any], verify: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Pure: the element update + report fields for a verify outcome."""
    meta = el.import_meta
    assert meta is not None
    result = verify["result"]
    stats = verify.get("stats") or {}
    notes = [n for n in meta.notes if not n.startswith("Verify:")]
    if result == "matches":
        status = meta.status if meta.status == "needs_review" else "verified"
        notes.append("Verify: matches the Tableau reference (informational).")
    elif result == "differs":
        status = "differs"
        notes.append(
            f"Verify: differs from the Tableau reference ({stats.get('mismatched_rows', 0)} "
            f"mismatched, {stats.get('rows_only_in_reference', 0)} only in Tableau, "
            f"{stats.get('rows_only_in_result', 0)} only in BigQuery). A finding to review, "
            "not something to tune away."
        )
    elif result == "error":
        status = meta.status
        notes.append(f"Verify: could not run ({verify.get('detail', '')})")
    else:
        status = meta.status
        notes.append(f"Verify: no reference ({verify.get('detail') or 'none available'})")
    new = el.model_copy(
        update={
            "import_meta": meta.model_copy(update={"status": status, "notes": notes[:MAX_NOTES]})
        }
    )
    entry = {
        "sheet": sheet["name"],
        "status": status,
        "notes": notes[:MAX_NOTES],
        "verify": {"result": result, **{k: v for k, v in verify.items() if k != "result"}},
        "reconciliation": {"result": result, **stats},
    }
    ops = [{"op": "update_element", "element": new.model_dump()}]
    return entry, ops
