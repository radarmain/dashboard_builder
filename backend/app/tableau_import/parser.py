"""Parse a Tableau workbook (.twb XML) into a SANITIZED plan.

Sanitization is structural: this module copies only an allowlist of attributes. Connection
elements contribute their `class` and BigQuery project/dataset/table names only; usernames,
passwords, OAuth/server-oauth tokens, authentication modes, hostnames (`server`), extract file
paths (`dbname`, `filename`) and every other attribute are never read. The raw file is handled
by `plan_from_download`, which keeps it in a temp dir and deletes it in a finally block.

Parse errors never include XML content (only the exception type).
"""

import io
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

MAX_XML_BYTES = 60_000_000
REF = re.compile(r"\[([^\]]+)\]\.\[([^\]]+)\]")
INST = re.compile(r"^(?P<d>[a-z]+):(?P<c>.+):(?P<s>[a-z]{2})$")
AGG = {"Sum", "Avg", "Min", "Max", "Count", "CountD", "Median", "Stdev", "Var", "Attribute"}
SKIP_MARKS = {"Automatic"}
UNSUPPORTED_MARKS = {"Map", "Polygon", "Multipolygon", "GanttBar", "Density", "VizExtension"}
LOD = re.compile(r"\{\s*(FIXED|INCLUDE|EXCLUDE)\b", re.I)
TABLE_CALC = re.compile(
    r"\b(WINDOW_\w+|RUNNING_\w+|LOOKUP|INDEX|FIRST|LAST|SIZE|RANK\w*|TOTAL|PREVIOUS_VALUE)\s*\(",
    re.I,
)
PARAM_REF = re.compile(r"\[Parameters\]\.\[([^\]]+)\]")


class ParseError(Exception):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _strip(name: str) -> str:
    """'[abc]' -> 'abc'."""
    return name[1:-1] if name.startswith("[") and name.endswith("]") else name


# ---------- file handling ----------
def xml_from_bytes(data: bytes) -> bytes:
    """twbx (zip) -> the .twb member read in memory; twb -> as is. Nothing else is read."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".twb") and "/" not in n]
            if not names:
                raise ParseError("no .twb inside the .twbx")
            info = z.getinfo(names[0])
            if info.file_size > MAX_XML_BYTES:
                raise ParseError("workbook XML is too large")
            return z.read(names[0])
    return data


def plan_from_download(data: bytes) -> dict[str, Any]:
    """Raw workbook bytes -> sanitized plan. The file touches disk only inside a temp dir that
    is removed in `finally`; it is never logged or returned."""
    tmp = Path(tempfile.mkdtemp(prefix="rdb-twb-"))
    try:
        f = tmp / "wb.bin"
        f.write_bytes(data)
        return parse_workbook(xml_from_bytes(f.read_bytes()))
    except (ParseError, zipfile.BadZipFile):
        raise
    except Exception as e:  # noqa: BLE001 - never echo content
        raise ParseError(f"could not parse workbook ({type(e).__name__})") from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def parse_workbook(xml: bytes) -> dict[str, Any]:
    head = xml[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in xml[:100000].lower():
        raise ParseError("DOCTYPE/ENTITY declarations are not allowed")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ParseError("workbook XML is not well-formed") from None
    ctx = _Ctx()
    ctx.datasources = [_datasource(ds, ctx) for ds in _children(root, "datasources", "datasource")]
    ctx.datasources = [d for d in ctx.datasources if d is not None]
    sheets = [_worksheet(w, ctx) for w in _children(root, "worksheets", "worksheet")]
    dashboards = [
        _dashboard(d, {s["name"] for s in sheets})
        for d in _children(root, "dashboards", "dashboard")
    ]
    _shared_filters(root, sheets, ctx)
    _attach_quick_filters(root, sheets, dashboards)
    _attach_grid(sheets, dashboards)
    for s in sheets:
        _status(s)
    return {
        "workbook": {"version": root.get("version") or ""},
        "datasources": ctx.datasources,
        "calculated_fields": ctx.calcs,
        "parameters": ctx.params,
        "worksheets": sheets,
        "dashboards": dashboards,
    }


class _Ctx:
    def __init__(self) -> None:
        self.datasources: list[dict[str, Any]] = []
        self.calcs: list[dict[str, Any]] = []
        self.params: list[dict[str, Any]] = []
        self.captions: dict[str, str] = {}  # '[Calculation_123]' -> 'Occupancy'
        self.formulas: dict[str, str] = {}  # caption -> formula
        self.roles: dict[str, str] = {}  # raw column name -> measure|dimension
        self.dtypes: dict[str, str] = {}
        self.param_names: dict[str, str] = {}  # raw '[Parameter 1]' -> caption


def _children(el: ET.Element, *path: str) -> list[ET.Element]:
    cur = [el]
    for p in path:
        cur = [c for x in cur for c in x if _local(c.tag) == p]
    return cur


def _find_all(el: ET.Element, tag: str) -> list[ET.Element]:
    return [x for x in el.iter() if _local(x.tag) == tag]


# ---------- datasources ----------
def _datasource(ds: ET.Element, ctx: _Ctx) -> dict[str, Any] | None:
    name = ds.get("name") or ""
    cols = _children(ds, "column")
    if name == "Parameters":
        for c in cols:
            caption = c.get("caption") or _strip(c.get("name") or "")
            ctx.param_names[c.get("name") or ""] = caption
            members = [m.get("value") for m in _find_all(c, "member") if m.get("value")]
            p: dict[str, Any] = {
                "name": caption,
                "datatype": c.get("datatype") or "",
                "default": _strip_quotes(c.get("value") or ""),
                "domain": c.get("param-domain-type") or "any",
            }
            if members:
                p["allowed_values"] = [_strip_quotes(m or "") for m in members][:30]
            ctx.params.append(p)
        return None
    caption_ds = ds.get("caption") or name
    for c in cols:
        raw = c.get("name") or ""
        cap = c.get("caption") or _strip(raw)
        ctx.captions[raw] = cap
        ctx.roles[raw] = c.get("role") or "dimension"
        ctx.dtypes[raw] = c.get("datatype") or ""
        calc = next(iter(_children(c, "calculation")), None)
        if calc is not None and calc.get("formula") and not raw.startswith("[Parameters]"):
            ctx.formulas[cap] = calc.get("formula") or ""
    for c in cols:  # second pass: formulas refer to other captions
        raw = c.get("name") or ""
        calc = next(iter(_children(c, "calculation")), None)
        if calc is None or not calc.get("formula"):
            continue
        formula = _humanize_formula(calc.get("formula") or "", ctx)
        ctx.formulas[ctx.captions[raw]] = formula
        ctx.calcs.append(
            {
                "datasource": caption_ds,
                "name": ctx.captions[raw],
                "formula": formula,
                "datatype": c.get("datatype") or "",
                "role": c.get("role") or "",
                "kind": _calc_kind(formula),
            }
        )
    extract = [e for e in _children(ds, "extract") if (e.get("enabled") or "true") != "false"]
    tables, joins, warnings, classes = _relations(ds)
    return {
        "name": caption_ds,
        "id": name,
        "connection_classes": sorted(classes),
        "mode": "extract" if extract else "live",
        "tables": tables,
        "joins": joins,
        "filters": [f for f in (_filter(f, ctx, None) for f in _children(ds, "filter")) if f],
        "warnings": warnings,
    }


def _strip_quotes(v: str) -> str:
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v


def _humanize_formula(formula: str, ctx: _Ctx) -> str:
    def sub(m: re.Match[str]) -> str:
        raw = m.group(0)
        if raw.startswith("[Parameters]."):
            return (
                f"[Parameters].[{ctx.param_names.get('[' + m.group(1) + ']', m.group(1))}]"
                if m.lastindex
                else raw
            )
        return "[" + ctx.captions.get(raw, raw[1:-1]) + "]"

    out = re.sub(r"\[Parameters\]\.\[([^\]]+)\]", sub, formula)
    return re.sub(r"(?<!\.)\[[^\]]+\]", sub, out)


def _calc_kind(formula: str) -> str:
    if LOD.search(formula):
        return "lod"
    if TABLE_CALC.search(formula):
        return "table_calc"
    if PARAM_REF.search(formula):
        return "parameter"
    return "row_or_aggregate"


def _bq_name(table: str, project: str, dataset: str) -> str:
    parts = [_strip(p) for p in re.findall(r"\[[^\]]+\]", table)] or table.strip("`").split(".")
    if len(parts) >= 3:  # 4+ parts: a leading Tableau datasource name ("StorageMart.") is dropped
        return ".".join(parts[-3:])
    if len(parts) == 2 and project:
        return f"{project}.{parts[0]}.{parts[1]}"
    if len(parts) == 1 and project and dataset:
        return f"{project}.{dataset}.{parts[0]}"
    return ".".join(parts)


def _relations(ds: ET.Element) -> tuple[list[str], list[dict[str, Any]], list[str], set[str]]:
    classes: set[str] = set()
    named: dict[str, tuple[str, str]] = {}  # connection name -> (project, dataset)
    for c in _find_all(ds, "connection"):
        if c.get("class"):
            classes.add(c.get("class") or "")
        # only names of BigQuery objects are read; nothing else from this element
        if c.get("class") == "bigquery":
            proj = c.get("project") or c.get("CATALOG") or c.get("catalog") or ""
            data = c.get("dataset") or c.get("schema") or ""
            named[c.get("name") or ""] = (proj, data)
    for nc in _find_all(ds, "named-connection"):
        inner = next(iter(_children(nc, "connection")), None)
        if inner is not None and inner.get("class") == "bigquery":
            named[nc.get("name") or ""] = (
                inner.get("project") or inner.get("CATALOG") or "",
                inner.get("dataset") or inner.get("schema") or "",
            )
    default = next(iter(named.values()), ("", ""))
    tables: list[str] = []
    warnings: list[str] = []
    joins: list[dict[str, Any]] = []
    for rel in _find_all(ds, "relation"):
        kind = rel.get("type")
        if kind == "table":
            tbl = rel.get("table") or ""
            if tbl.strip().lower() in ("[extract].[extract]", "[extract]"):
                continue
            proj, data = named.get(rel.get("connection") or "", default)
            fqn = _bq_name(tbl, proj, data)
            if fqn not in tables:
                tables.append(fqn)
        elif kind == "text":
            warnings.append("custom SQL relation (query text omitted): needs analyst review")
        elif kind == "join":
            keys = []
            for e in _find_all(rel, "expression"):
                if e.get("op") == "=" and len(list(e)) == 2:
                    a, b = (x.get("op") or "" for x in e)
                    keys.append([_clean_key(a), _clean_key(b)])
            joins.append({"type": rel.get("join") or "inner", "on": keys})
    for r in _find_all(ds, "relationship"):  # newer "relationships" data model
        exprs = _find_all(r, "expression")
        if len(exprs) >= 3:
            joins.append(
                {
                    "type": "relationship",
                    "on": [
                        [_clean_key(exprs[1].get("op") or ""), _clean_key(exprs[2].get("op") or "")]
                    ],
                }
            )
    return tables, joins, warnings, classes


def _clean_key(op: str) -> str:
    return ".".join(_strip(p) for p in re.findall(r"\[[^\]]+\]", op)) or op


# ---------- filters ----------
def _val(v: str) -> str:
    v = _strip_quotes(v.strip())
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] == "#" else v


def _members(g: ET.Element) -> tuple[str, list[str]]:
    """groupfilter tree -> (mode, values). mode: include | exclude | all | complex."""
    fn = g.get("function")
    if fn == "member":
        return "include", [_val(g.get("member") or "")]
    if fn == "level-members":
        return "all", []
    kids = [k for k in g if _local(k.tag) == "groupfilter"]
    if fn == "union":
        vals: list[str] = []
        for k in kids:
            m, v = _members(k)
            if m != "include":
                return "complex", []
            vals += v
        return "include", vals
    if fn == "except" and len(kids) == 2:
        m0, _ = _members(kids[0])
        m1, v1 = _members(kids[1])
        if m0 == "all" and m1 == "include":
            return "exclude", v1
    return "complex", []


def _field(ref: str, ctx: _Ctx, inst: dict[str, dict[str, str]] | None) -> dict[str, Any]:
    """'[usr:Calculation_1:qk]' (inner name only) -> {field, derivation, measure, date, calc}."""
    info = (inst or {}).get(ref)
    m = INST.match(ref)
    raw = (info or {}).get("column") or (f"[{m.group('c')}]" if m else f"[{ref}]")
    deriv = (info or {}).get("derivation") or {
        "sum": "Sum",
        "avg": "Avg",
        "usr": "User",
        "none": "None",
    }.get(m.group("d") if m else "", m.group("d") if m else "None")
    cap = ctx.captions.get(raw) or _strip(raw)
    role = ctx.roles.get(raw, "dimension")
    dtype = ctx.dtypes.get(raw, "")
    date = dtype in ("date", "datetime") or deriv.split("-")[0] in {
        "Year",
        "Month",
        "Day",
        "Quarter",
        "Week",
        "Weekday",
        "Hour",
    }
    measure = deriv in AGG or (deriv in ("User", "None") and role == "measure")
    f: dict[str, Any] = {"field": cap, "derivation": deriv, "measure": measure, "date": date}
    if cap in ctx.formulas:
        f["calculation"] = {"formula": ctx.formulas[cap], "kind": _calc_kind(ctx.formulas[cap])}
    return f


def _fmt_default(f: dict[str, Any]) -> str:
    t = f["type"]
    if t == "categorical":
        if f["mode"] == "all":
            return "all values"
        return f"{'all except ' if f['mode'] == 'exclude' else ''}{f['values']}"
    if t == "quantitative":
        return f"{f.get('min', '-inf')} to {f.get('max', '+inf')}"
    return str({k: v for k, v in f.items() if k not in ("field", "type", "ref")})


def _filter(
    el: ET.Element, ctx: _Ctx, inst: dict[str, dict[str, str]] | None
) -> dict[str, Any] | None:
    col = el.get("column") or ""
    if col.startswith("[Action") or ":Measure Names" in col:
        return None
    parts = REF.findall(col)
    ref = parts[-1][1] if parts else _strip(col)
    fld = _field(ref, ctx, inst)
    out: dict[str, Any] = {
        "field": fld["field"],
        "derivation": fld["derivation"],
        "type": el.get("class") or "",
        "ref": ref,
    }
    cls = out["type"]
    if cls == "categorical":
        g = next(iter(_children(el, "groupfilter")), None)
        mode, vals = _members(g) if g is not None else ("complex", [])
        out.update(mode=mode, values=vals)
        if mode == "complex":
            out["complex"] = True
    elif cls in ("quantitative", "date"):
        for k in el:
            t = _local(k.tag)
            if t in ("min", "max") and k.text:
                out[t] = _val(k.text)
        if el.get("included-values"):
            out["included_values"] = el.get("included-values")
    elif cls == "relative-date":
        out.update({k: el.get(k) for k in ("period-type", "range-type", "range-n") if el.get(k)})
    else:
        out["complex"] = True  # e.g. top-N / context / wildcard: needs analyst review
    out["default"] = _fmt_default(out)
    return out


# ---------- worksheets ----------
ENC = {
    "color": "color",
    "size": "size",
    "text": "label",
    "lod": "detail",
    "angle": "angle",
    "shape": "shape",
    "tooltip": "tooltip",
    "path": "path",
}


def _shelf(text: str | None, ctx: _Ctx, inst: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    return [_field(m.group(2), ctx, inst) for m in REF.finditer(text or "")]


def _worksheet(w: ET.Element, ctx: _Ctx) -> dict[str, Any]:
    name = w.get("name") or ""
    table = next(iter(_children(w, "table")), None)
    view = next(iter(_children(table, "view")), None) if table is not None else None
    inst: dict[str, dict[str, str]] = {}
    if view is not None:
        for dep in _find_all(view, "datasource-dependencies"):
            for c in _children(dep, "column"):
                raw = c.get("name") or ""
                ctx.captions.setdefault(raw, c.get("caption") or _strip(raw))
                ctx.roles.setdefault(raw, c.get("role") or "dimension")
                ctx.dtypes.setdefault(raw, c.get("datatype") or "")
                calc = next(iter(_children(c, "calculation")), None)
                if (
                    calc is not None
                    and calc.get("formula")
                    and ctx.captions[raw] not in ctx.formulas
                ):
                    ctx.formulas[ctx.captions[raw]] = _humanize_formula(
                        calc.get("formula") or "", ctx
                    )
            for ci in _children(dep, "column-instance"):
                inst[ci.get("name") or ""] = {
                    "column": ci.get("column") or "",
                    "derivation": ci.get("derivation") or "None",
                }
    rows_t = next(iter(_children(table, "rows")), None) if table is not None else None
    cols_t = next(iter(_children(table, "cols")), None) if table is not None else None
    rows = _shelf((rows_t.text or "") if rows_t is not None else "", ctx, inst)
    cols = _shelf((cols_t.text or "") if cols_t is not None else "", ctx, inst)
    panes = _children(table, "panes", "pane") if table is not None else []
    marks: list[str] = []
    enc: dict[str, list[dict[str, Any]]] = {v: [] for v in ENC.values()}
    axis_panes = 0
    for p in panes:
        if p.get("y-axis-name") or p.get("x-axis-name"):
            axis_panes += 1
        for m in _children(p, "mark"):
            marks.append(m.get("class") or "Automatic")
        for e in _children(p, "encodings"):
            for k in e:
                key = ENC.get(_local(k.tag))
                parts = REF.findall(k.get("column") or "")
                if key and parts:
                    f = _field(parts[-1][1], ctx, inst)
                    if f not in enc[key]:
                        enc[key].append(f)
    shelf_text = ((rows_t.text or "") if rows_t is not None else "") + (
        (cols_t.text or "") if cols_t is not None else ""
    )
    filters = (
        [x for x in (_filter(f, ctx, inst) for f in _children(view, "filter")) if x]
        if view is not None
        else []
    )
    slices = (
        [
            REF.findall(c.text or "")[-1][1]
            for c in _find_all(view, "column")
            if _local(c.tag) == "column" and REF.findall(c.text or "")
        ]
        if view is not None
        else []
    )
    sorts = []
    if view is not None:
        for s in _find_all(view, "computed-sort") + _find_all(view, "manual-sort"):
            parts = REF.findall(s.get("column") or "")
            using = REF.findall(s.get("using") or "")
            sorts.append(
                {
                    "field": _field(parts[-1][1], ctx, inst)["field"] if parts else "",
                    "direction": s.get("direction") or "ASC",
                    "by": _field(using[-1][1], ctx, inst)["field"] if using else None,
                }
            )
    table_calc = bool(table is not None and _find_all(table, "table-calc"))
    ref_lines = len(_find_all(table, "reference-line")) if table is not None else 0
    measure_insts = [f for f in rows + cols if f["measure"]]
    plus_measures = "+" in shelf_text and len(measure_insts) >= 2
    sheet: dict[str, Any] = {
        "name": name,
        "marks": sorted(set(marks)),
        "rows": rows,
        "columns": cols,
        **enc,
        "filters": filters,
        "_slices": slices,
        "sort": sorts,
        "dual_axis": bool(plus_measures and (axis_panes >= 2 or len(set(marks) - SKIP_MARKS) >= 2)),
        "measure_names_values": ":Measure Names" in shelf_text or "Multiple Values" in shelf_text,
        "uses_table_calc": table_calc,
        "reference_lines": ref_lines,
        "dashboards": [],
        "quick_filters": [],
    }
    _resolve_chart(sheet)
    sheet["calculations_used"] = _used_calcs(sheet, ctx)
    return sheet


def _all_fields(s: dict[str, Any]) -> list[dict[str, Any]]:
    out = s["rows"] + s["columns"]
    for k in ENC.values():
        out += s[k]
    return out + [{"field": f["field"]} for f in s["filters"]]


def _used_calcs(s: dict[str, Any], ctx: _Ctx) -> list[dict[str, str]]:
    seen: dict[str, str] = {}

    def visit(cap: str, depth: int = 0) -> None:
        f = ctx.formulas.get(cap)
        if f is None or cap in seen or depth > 3:
            return
        seen[cap] = _calc_kind(f)
        for m in re.finditer(r"(?<!\.)\[([^\]]+)\]", f):
            visit(m.group(1), depth + 1)

    for f in _all_fields(s):
        visit(f["field"])
    return [{"name": k, "kind": v} for k, v in seen.items()]


def _resolve_chart(s: dict[str, Any]) -> None:
    marks = set(s["marks"]) - SKIP_MARKS
    rows, cols = s["rows"], s["columns"]
    meas_r = [f for f in rows if f["measure"]]
    meas_c = [f for f in cols if f["measure"]]
    dim_r = [
        f for f in rows if not f["measure"] and not f["field"].startswith(("Latitude", "Longitude"))
    ]
    dim_c = [
        f for f in cols if not f["measure"] and not f["field"].startswith(("Latitude", "Longitude"))
    ]
    geo = any(f["field"].startswith(("Latitude", "Longitude")) for f in rows + cols)
    enc_measure = [f for f in s["color"] + s["label"] if f["measure"]]
    s["chart_notes"] = []
    if geo or marks & UNSUPPORTED_MARKS:
        s["chart_type"] = "unsupported"
        s["chart_notes"].append("map/polygon/gantt marks have no equivalent renderer")
    elif "Pie" in marks:
        s["chart_type"] = "pie"
    elif not rows and not cols:
        s["chart_type"] = "kpi" if enc_measure else "unsupported"
        if not enc_measure:
            s["chart_notes"].append("no measure found")
    elif not meas_r and not meas_c:
        # text table / heatmap: dimensions on shelves, measures only in marks
        if not enc_measure:
            s["chart_type"] = "table"  # detail list
        elif "Square" in marks and s["color"] and dim_r and dim_c:
            s["chart_type"] = "heatmap"
        else:
            s["chart_type"] = "table" if not (dim_r and dim_c) else "pivot_table"
    elif meas_r and meas_c and not dim_r and not dim_c:
        s["chart_type"] = "scatter"
    else:
        date_axis = any(f["date"] for f in (dim_c if meas_r else dim_r))
        horizontal = bool(meas_c and not meas_r)
        pick = {
            "Area": "area",
            "Line": "line",
            "Bar": "bar",
            "Circle": "scatter",
            "Shape": "scatter",
        }
        kinds = [pick[m] for m in marks if m in pick]
        kind = (
            "area" if "area" in kinds else (kinds[0] if kinds else ("line" if date_axis else "bar"))
        )
        s["chart_type"] = kind
        if horizontal:
            s["chart_notes"].append("horizontal orientation is drawn vertically")
    s["chart_type"] = s["chart_type"] or "unsupported"


# ---------- dashboards ----------
CONTAINERS = {
    "layout-basic",
    "layout-flow",
    "empty",
    "title",
    "text",
    "bitmap",
    "web",
    "color",
    "paramctrl",
    "legend",
    "filter",
}
ROW_PX = 68  # one row unit of the 12-column grid (56px row + 12px gap), see SpecRenderer


def _dashboard(d: ET.Element, sheet_names: set[str]) -> dict[str, Any]:
    size = next(iter(_children(d, "size")), None)
    h_px = int(
        float((size.get("maxheight") or size.get("minheight") or 800) if size is not None else 800)
    )
    w_px = int(
        float((size.get("maxwidth") or size.get("minwidth") or 1000) if size is not None else 1000)
    )
    out: dict[str, Any] = {
        "name": d.get("name") or "",
        "size_px": [w_px, h_px],
        "sheets": [],
        "filter_controls": [],
    }
    zones = _children(d, "zones")
    for z in zones[0].iter() if zones else []:
        if _local(z.tag) != "zone":
            continue
        t = z.get("type-v2") or z.get("type") or ""
        name = z.get("name") or ""
        if t == "filter" and z.get("param"):
            parts = REF.findall(z.get("param") or "")
            out["filter_controls"].append(
                {"ref": parts[-1][1] if parts else "", "sheet": name, "mode": z.get("mode") or ""}
            )
        elif name in sheet_names and t not in CONTAINERS:
            try:
                x, y, w, h = (int(z.get(k) or 0) / 1000 for k in ("x", "y", "w", "h"))
            except ValueError:
                continue
            out["sheets"].append(
                {
                    "name": name,
                    "x_pct": round(x, 2),
                    "y_pct": round(y, 2),
                    "w_pct": round(w, 2),
                    "h_pct": round(h, 2),
                }
            )
    return out


def _grid(placed: list[dict[str, Any]], w_px: int, h_px: int) -> dict[str, dict[str, int]]:
    """Percent boxes -> 12-column grid boxes (y/h in 68px row units, normalized to start at 0)."""
    raw = {}
    for p in placed:
        x0 = round(p["x_pct"] * 12 / 100)
        x1 = max(x0 + 1, round((p["x_pct"] + p["w_pct"]) * 12 / 100))
        y0 = round(p["y_pct"] * h_px / 100 / ROW_PX)
        y1 = max(y0 + 2, round((p["y_pct"] + p["h_pct"]) * h_px / 100 / ROW_PX))
        raw[p["name"]] = [min(x0, 11), min(x1, 12), y0, y1]
    top = min((v[2] for v in raw.values()), default=0)
    return {
        n: {"x": v[0], "y": v[2] - top, "w": v[1] - v[0], "h": v[3] - v[2]} for n, v in raw.items()
    }


def _attach_grid(sheets: list[dict[str, Any]], dashboards: list[dict[str, Any]]) -> None:
    by_name = {s["name"]: s for s in sheets}
    for d in dashboards:
        d["layout"] = _grid(d["sheets"], *d["size_px"])
        for n, g in d["layout"].items():
            s = by_name[n]
            s["dashboards"].append(d["name"])
            s.setdefault("grid", g)  # first dashboard that places the sheet wins


def _attach_quick_filters(
    root: ET.Element, sheets: list[dict[str, Any]], dashboards: list[dict[str, Any]]
) -> None:
    shown = {(c["ref"]): c["mode"] for d in dashboards for c in d["filter_controls"]}
    for s in sheets:
        for f in s["filters"]:
            f["interactive_in_tableau"] = f["ref"] in shown
            if f["ref"] in shown:
                f["control"] = shown[f["ref"]]


def _shared_filters(root: ET.Element, sheets: list[dict[str, Any]], ctx: _Ctx) -> None:
    for sv in _find_all(root, "shared-view"):
        for f in _children(sv, "filter"):
            parsed = _filter(f, ctx, None)
            if not parsed:
                continue
            for s in sheets:
                if parsed["ref"] in s["_slices"] and not any(
                    x["ref"] == parsed["ref"] for x in s["filters"]
                ):
                    s["filters"].append({**parsed, "shared": True})


# ---------- status ----------
def _base(cap: str) -> str:
    return re.sub(r"\s*\(copy[^)]*\)$", "", cap)


def _status(s: dict[str, Any]) -> None:
    unsupported: list[str] = []
    approx: list[str] = []
    if s["chart_type"] == "unsupported":
        unsupported += s["chart_notes"] or ["could not resolve a chart type"]
    for c in s["calculations_used"]:
        if c["kind"] == "table_calc":
            unsupported.append(
                f"table calculation in field '{c['name']}' (window/running/rank functions)"
            )
        elif c["kind"] == "lod":
            approx.append(
                f"LOD expression in '{c['name']}': SQL is proposed and needs analyst review"
            )
        elif c["kind"] == "parameter":
            approx.append(
                f"parameter used by '{c['name']}': baked at its current value, not interactive"
            )
    if s["uses_table_calc"]:
        unsupported.append("table calculation (quick table calc or computed using)")
    if s["measure_names_values"]:
        approx.append("uses Measure Names/Values")
    if s["reference_lines"]:
        approx.append(f"{s['reference_lines']} reference line(s) not rendered")
    if any(f.get("complex") for f in s["filters"]):
        approx.append("a filter (top-N/condition/wildcard) cannot be translated exactly")
    if s["dual_axis"]:
        measures = {_base(f["field"]) for f in s["rows"] + s["columns"] if f["measure"]}
        if len(measures) > 1:
            approx.append("dual axis with different measures is drawn on one shared axis")
    approx += [n for n in s["chart_notes"] if s["chart_type"] != "unsupported"]
    s["status"] = "unsupported" if unsupported else ("approximate" if approx else "supported")
    s["status_reason"] = "; ".join(unsupported or approx)
    s.pop("_slices", None)
    for f in s["filters"]:
        f.pop("ref", None)
