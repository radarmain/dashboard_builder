"""SQL validation: the security boundary for a global BigQuery service account.

Fail closed. We validate the parsed AST and then EXECUTE THE NORMALIZED SQL regenerated from
that AST (comments stripped), so what runs is exactly what was checked.

Rules: exactly one SELECT-ish statement; every real table must be fully qualified
`project.dataset.table` and appear verbatim (case-sensitive) in the project's allowlist;
CTE names are fine; no table-valued functions / wildcards / external queries / ML.* / AI.*.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

DIALECT = "bigquery"

ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Subquery)  # Union covers UNION/INTERSECT/EXCEPT
# Function-name prefixes/names that read outside the allowlist or call external services.
DENIED_FUNCS = (
    "EXTERNAL_QUERY",
    "EXTERNAL_OBJECT_TRANSFORM",
    "VECTOR_SEARCH",
    "APPENDS",
    "CHANGES",
    "AI.",
    "ML.",
    "OBJ.",
    "TEXT_EMBEDDING",
    "GAP_FILL",
    "CALL",
)


DENIED_NAMESPACES = {"AI", "ML", "OBJ"}


class SqlValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str  # normalized; this is what must be executed
    tables: list[str]  # fully-qualified tables referenced (sorted, unique)


def _fail(msg: str) -> SqlValidationError:
    return SqlValidationError(msg)


def _table_fqn(t: exp.Table) -> str | None:
    """'project.dataset.table' for a plain 3-part reference, else None."""
    if not isinstance(t.this, exp.Identifier):
        return None
    parts = [t.catalog, t.db, t.name]
    if not all(parts):
        return None
    return ".".join(parts)


def _func_denied(name: str) -> bool:
    n = name.upper().strip()
    return any(
        n == d or (d.endswith(".") and n.startswith(d)) or n.startswith(d + ".")
        for d in DENIED_FUNCS
    )


def validate_sql(sql: str, allowed_tables: set[str] | list[str]) -> ValidatedSQL:
    allowed = set(allowed_tables)
    if not sql or not sql.strip():
        raise _fail("empty SQL")

    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except SqlglotError as e:
        raise _fail(f"could not parse SQL: {str(e)[:200]}") from None
    if len(statements) != 1:
        raise _fail("exactly one statement is allowed")
    root = statements[0]
    if not isinstance(root, ALLOWED_ROOTS):
        raise _fail(f"only SELECT statements are allowed (got {type(root).__name__})")

    # Anything that isn't a plain query hiding inside the tree.
    for node in root.walk():
        if isinstance(node, (exp.Command, exp.DDL, exp.DML, exp.Declare, exp.Set)):
            raise _fail(f"{type(node).__name__} is not allowed")

    # Function denylist (external reads, ML/AI, table-valued functions).
    for fn in root.find_all(exp.Func):
        names = {fn.name or "", fn.sql_name() if hasattr(fn, "sql_name") else ""}
        names.add(fn.sql(dialect=DIALECT, comments=False).split("(", 1)[0])
        if any(n and _func_denied(n) for n in names):
            raise _fail(f"function not allowed: {fn.name or fn.sql_name()}")

    # Namespaced functions parse as Dot(Column(AI), Func): AI.*, ML.*, OBJ.*
    for dot in root.find_all(exp.Dot):
        left = dot.this
        if (
            isinstance(left, (exp.Column, exp.Identifier))
            and not (isinstance(left, exp.Column) and left.table)
            and left.name.upper() in DENIED_NAMESPACES
            and isinstance(dot.expression, exp.Func)
        ):
            raise _fail(f"function not allowed: {left.name}.{dot.expression.name}")

    cte_names = {c.alias for c in root.find_all(exp.CTE) if c.alias}
    referenced: set[str] = set()
    for t in root.find_all(exp.Table):
        # Unqualified reference to a CTE is fine; anything else unqualified is not.
        if (
            not t.catalog
            and not t.db
            and isinstance(t.this, exp.Identifier)
            and t.name in cte_names
        ):
            continue
        if not isinstance(t.this, exp.Identifier):
            raise _fail("table functions and computed table references are not allowed")
        if "*" in t.name or "*" in (t.db or "") or "*" in (t.catalog or ""):
            raise _fail("wildcard tables are not allowed")
        fqn = _table_fqn(t)
        if fqn is None:
            raise _fail(f"table must be fully qualified project.dataset.table: {t.sql(DIALECT)}")
        if fqn not in allowed:
            raise _fail(f"table not connected to this project: {fqn}")
        referenced.add(fqn)

    normalized = root.sql(dialect=DIALECT, comments=False)
    return ValidatedSQL(sql=normalized, tables=sorted(referenced))


def validate_allowlist_entry(entry: str) -> str:
    """Connected tables must be exactly project.dataset.table, no wildcards."""
    parts = entry.split(".")
    if len(parts) != 3 or not all(parts) or "*" in entry or any(c in entry for c in "`; \n"):
        raise _fail(f"table must be project.dataset.table: {entry!r}")
    return entry
