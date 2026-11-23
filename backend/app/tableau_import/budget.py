"""Per-import scanned-bytes budget (tracked in DynamoDB so it spans the per-sheet requests)."""

from collections.abc import Callable
from typing import Any

from app.query_runner import QueryRejected


class BudgetExhausted(QueryRejected):
    """The import run's total scanned-bytes budget is used up."""


class ImportBudget:
    """Wraps a query runner: caps each query at min(per-query cap, remaining budget), adds the
    bytes actually scanned (cache hits cost 0), and refuses once the budget is gone."""

    def __init__(
        self,
        run: Callable[..., dict[str, Any]],
        *,
        per_query_cap: int,
        total_budget: int,
        get_scanned: Callable[[], int],
        add_scanned: Callable[[int], int],
    ) -> None:
        self._run = run
        self.per_query_cap = per_query_cap
        self.total_budget = total_budget
        self._get = get_scanned
        self._add = add_scanned
        self.session_bytes = 0  # scanned by this request only

    @property
    def remaining(self) -> int:
        return max(0, self.total_budget - self._get())

    def exhausted_message(self) -> str:
        gb = self.total_budget / 1e9
        return (
            f"Import stopped: the total scan budget for this import run ({gb:.0f} GB) is used up. "
            "Nothing further was applied. Raise RDB_IMPORT_RUN_BUDGET_BYTES or start a new import."
        )

    def __call__(self, sql: str) -> dict[str, Any]:
        left = self.remaining
        if left <= 0:
            raise BudgetExhausted(self.exhausted_message())
        res = self._run(sql, max_bytes=min(self.per_query_cap, left))
        if not res.get("cache_hit"):
            n = int(res.get("bytes_processed") or 0)
            self.session_bytes += n
            self._add(n)
        return res


# ---------- DynamoDB-backed counter (item per import) ----------
def _key(c: str, p: str, import_id: str) -> dict[str, str]:
    return {"PK": f"CLIENT#{c}", "SK": f"IMPORT#{p}#{import_id}"}


def ddb_counter(c: str, p: str, import_id: str) -> tuple[Callable[[], int], Callable[[int], int]]:
    from app.aws import ddb_table

    def get() -> int:
        item = ddb_table().get_item(Key=_key(c, p, import_id), ConsistentRead=True).get("Item")
        return int(item["scanned_bytes"]) if item and "scanned_bytes" in item else 0

    def add(n: int) -> int:
        r = ddb_table().update_item(
            Key=_key(c, p, import_id),
            UpdateExpression="ADD scanned_bytes :n SET updated_at = :t",
            ExpressionAttributeValues={":n": n, ":t": _now()},
            ReturnValues="UPDATED_NEW",
        )
        return int(r["Attributes"]["scanned_bytes"])

    return get, add


def _now() -> str:
    from app.store import now

    return now()
