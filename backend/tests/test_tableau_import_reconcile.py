"""Reconciliation (tolerance, dedupe, missing rows) and the import byte budget."""

from typing import Any

import pytest
from app.query_runner import QueryRejected
from app.tableau_import.budget import BudgetExhausted, ImportBudget
from app.tableau_import.reconcile import compare_tables, parse_csv, to_number

CSV = """Month of Date,Occupancy
July 2025,82.47%
August 2025,80.28%
July 2025,82.47%
August 2025,80.28%
September 2025,78.58%
"""


def test_to_number_percent_and_nulls() -> None:
    assert to_number("82.47%") == pytest.approx(0.8247)
    assert to_number("$1,234.5") == 1234.5
    assert to_number("%null%") is None and to_number("abc") is None


def test_dedupe_tolerance_and_missing_rows() -> None:
    cols, rows = parse_csv(CSV)
    cand = [
        ["2025-07-01", 0.82475],  # within 0.5% relative (display rounding)
        ["2025-08-01", 0.84],  # ~5% off -> mismatch
        ["2025-10-01", 0.7],  # only in BigQuery
    ]
    r = compare_tables(cols, rows, ["month", "occ"], cand)
    assert r["matched_rows"] == 1 and r["mismatched_rows"] == 1
    assert r["rows_only_in_reference"] == 1  # September
    assert r["rows_only_in_result"] == 1  # October
    assert r["duplicate_rows_collapsed"] == 2  # dual-axis doubling
    assert r["max_relative_difference"] == pytest.approx(0.0443, abs=0.001)
    assert len(r["examples"]) <= 5 and r["ok"] is False


def test_all_match_and_keyless_single_row() -> None:
    cols, rows = parse_csv(CSV)
    cand = [["2025-07-01", 0.8247], ["2025-08-01", 0.8028], ["2025-09-01", 0.7858]]
    assert compare_tables(cols, rows, ["m", "v"], cand)["ok"] is True
    one = compare_tables(["Occupancy"], [["78.5%"]], ["v"], [[0.7851]])
    assert one["ok"] is True and one["matched_rows"] == 1


def test_example_cap_and_misaligned_columns() -> None:
    ref = [[f"2025-{m:02d}-01", 1.0] for m in range(1, 13)]
    cand = [[f"2025-{m:02d}-01", 2.0] for m in range(1, 13)]
    r = compare_tables(["m", "v"], ref, ["m", "v"], cand)
    assert r["mismatched_rows"] == 12 and len(r["examples"]) == 5
    assert "error" in compare_tables(["a", "b"], ref, ["a"], cand)


# ---------- byte budget ----------
def make_budget(total: int, per_query: int, bytes_per_query: int) -> tuple[ImportBudget, list[Any]]:
    state = {"n": 0}
    calls: list[Any] = []

    def run(sql: str, *, max_bytes: int) -> dict[str, Any]:
        calls.append(max_bytes)
        if bytes_per_query > max_bytes:
            raise QueryRejected("over the cap")
        return {"bytes_processed": bytes_per_query, "cache_hit": False, "columns": [], "rows": []}

    def add(n: int) -> int:
        state["n"] += n
        return state["n"]

    return ImportBudget(
        run,
        per_query_cap=per_query,
        total_budget=total,
        get_scanned=lambda: state["n"],
        add_scanned=add,
    ), calls


def test_budget_caps_each_query_at_remaining_and_then_stops() -> None:
    b, calls = make_budget(total=25, per_query=10, bytes_per_query=10)
    b("q1")
    b("q2")
    assert b.remaining == 5
    with pytest.raises(QueryRejected):  # 10 bytes > remaining 5: the query is capped, so rejected
        b("q3")
    assert calls[-1] == 5  # per-query cap was tightened to the remaining budget


def test_budget_exhausted_message_and_cache_hits_are_free() -> None:
    b, _ = make_budget(total=10, per_query=10, bytes_per_query=10)
    b("q1")
    with pytest.raises(BudgetExhausted) as e:
        b("q2")
    assert "budget" in str(e.value) and "Nothing further was applied" in str(e.value)
