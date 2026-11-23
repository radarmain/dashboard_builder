"""Compare a BigQuery result with Tableau's view CSV (or with another BigQuery result) in code.

Only statistics and at most MAX_EXAMPLES example differences leave this module; the Tableau
values themselves are never sent to the model.
"""

import csv
import io
import re
from datetime import datetime
from typing import Any

TOLERANCE = 0.005  # relative
MAX_EXAMPLES = 5
MONTH_FORMATS = ("%B %Y", "%b %Y", "%Y-%m", "%b-%Y", "%B-%Y")
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M:%S")
NULLS = {"", "null", "%null%", "nan", "n/a"}


def parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows:
        return [], []
    return rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]


def to_number(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s.lower() in NULLS:
        return None
    pct = s.endswith("%")
    s = re.sub(r"[%$,\s]", "", s)
    if not re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", s):
        return None
    return float(s) / (100 if pct else 1)


def _month_key(v: Any) -> str | None:
    s = str(v).strip()
    for f in MONTH_FORMATS:
        try:
            return datetime.strptime(s, f).strftime("%Y-%m")
        except ValueError:
            pass
    return None


def _date_key(v: Any) -> str | None:
    s = str(v).strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s[:19] if "T" in f else s, f).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _is_key_col(rows: list[list[Any]], i: int) -> bool:
    vals = [r[i] for r in rows if i < len(r) and str(r[i]).strip().lower() not in NULLS]
    if not vals:
        return False
    if all(_month_key(v) or _date_key(v) for v in vals):
        return True
    return all(to_number(v) is None for v in vals)


def _key_cols(cols: list[str], rows: list[list[Any]]) -> list[int]:
    return [i for i in range(len(cols)) if _is_key_col(rows, i)]


def _norm_key(v: Any, grain: str) -> str:
    m, d = _month_key(v), _date_key(v)
    if grain == "month" and (m or d):
        return m or (d or "")[:7]
    if d:
        return d
    return m or str(v).strip().lower()


def _grain(rows: list[list[Any]], idx: list[int]) -> str:
    vals = [r[i] for r in rows for i in idx]
    return "month" if vals and all(_month_key(v) and not _date_key(v) for v in vals) else "date"


def _close(a: float, b: float) -> tuple[bool, float]:
    diff = abs(a - b)
    base = max(abs(a), abs(b))
    rel = diff / base if base > 1e-12 else 0.0
    return rel <= TOLERANCE, rel


def compare_tables(
    ref_cols: list[str],
    ref_rows: list[list[Any]],
    cand_cols: list[str],
    cand_rows: list[list[Any]],
    *,
    value_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """ref = Tableau CSV (or the trusted side), cand = the element's BigQuery result.
    Keys are aligned by position among key-like columns; values by position among the rest unless
    `value_map` {ref_col: cand_col} is given. Exact duplicate ref rows (dual axis) are collapsed."""
    rk, ck = _key_cols(ref_cols, ref_rows), _key_cols(cand_cols, cand_rows)
    rv = [i for i in range(len(ref_cols)) if i not in rk]
    cv = [i for i in range(len(cand_cols)) if i not in ck]
    if value_map:
        try:
            rv = [ref_cols.index(k) for k in value_map]
            cv = [cand_cols.index(v) for v in value_map.values()]
        except ValueError:
            return {"error": f"value_map columns not found: ref={ref_cols} cand={cand_cols}"}
    if len(rk) != len(ck) or not rv or len(rv) != len(cv):
        return {
            "error": "could not align columns",
            "reference_columns": ref_cols,
            "result_columns": cand_cols,
        }
    grain = _grain(ref_rows, rk)

    def build(rows: list[list[Any]], keys: list[int], vals: list[int]) -> tuple[dict, int, int]:
        out: dict[tuple, list[float | None]] = {}
        dup = conflicts = 0
        for r in rows:
            k = tuple(_norm_key(r[i], grain) for i in keys)
            v = [to_number(r[i]) for i in vals]
            if k in out:
                if out[k] == v:
                    dup += 1
                else:
                    conflicts += 1
                continue
            out[k] = v
        return out, dup, conflicts

    ref, dup, conflicts = build(ref_rows, rk, rv)
    cand, _, cand_conflicts = build(cand_rows, ck, cv)
    matched = mismatched = 0
    max_rel = 0.0
    examples: list[dict[str, Any]] = []
    for k in sorted(set(ref) & set(cand)):
        bad = False
        for j, (a, b) in enumerate(zip(ref[k], cand[k], strict=True)):
            if a is None or b is None:
                ok, rel = a == b, 0.0
            else:
                ok, rel = _close(a, b)
            max_rel = max(max_rel, rel)
            if not ok:
                bad = True
                if len(examples) < MAX_EXAMPLES:
                    examples.append(
                        {
                            "key": list(k),
                            "column": ref_cols[rv[j]],
                            "reference": a,
                            "result": b,
                            "relative_difference": round(rel, 6),
                        }
                    )
        matched, mismatched = (matched, mismatched + 1) if bad else (matched + 1, mismatched)
    only_ref, only_cand = sorted(set(ref) - set(cand)), sorted(set(cand) - set(ref))
    for side, keys in (("only_in_reference", only_ref), ("only_in_result", only_cand)):
        for k in keys[: max(0, MAX_EXAMPLES - len(examples))]:
            examples.append({"key": list(k), "difference": side})
    return {
        "matched_rows": matched,
        "mismatched_rows": mismatched,
        "rows_only_in_reference": len(only_ref),
        "rows_only_in_result": len(only_cand),
        "max_relative_difference": round(max_rel, 6),
        "tolerance": TOLERANCE,
        "duplicate_rows_collapsed": dup,
        "conflicting_duplicate_keys": conflicts + cand_conflicts,
        "examples": examples[:MAX_EXAMPLES],
        "ok": not (mismatched or only_ref or only_cand or conflicts or cand_conflicts)
        and matched > 0,
    }


Parsed = tuple[list[str], list[list[str]]]


def pick_reference(sheet: dict[str, Any], refs: dict[str, Parsed]) -> Parsed | None:
    """The Tableau CSV to compare this sheet with. REST only exposes dashboards as views, so a
    dashboard CSV is a candidate only for a sheet whose sole dimension is a date (a time series);
    a KPI or a multi-dimension sheet never picks up a monthly dashboard reference."""
    if sheet["name"] in refs:
        return refs[sheet["name"]]
    dims = {
        str(f["field"]).lower() for f in sheet["rows"] + sheet["columns"] if not f.get("measure")
    }
    if dims == {"date"}:
        return next((refs[d] for d in sheet.get("dashboards", []) if d in refs), None)
    return None
