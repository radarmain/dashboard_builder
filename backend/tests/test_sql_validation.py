import pytest
from app.sql_validation import SqlValidationError, validate_allowlist_entry, validate_sql

OK = "proj-1.sales.orders"
OK2 = "proj-1.sales.customers"
ALLOW = {OK, OK2}


def ok(sql: str):
    return validate_sql(sql, ALLOW)


def bad(sql: str):
    with pytest.raises(SqlValidationError):
        validate_sql(sql, ALLOW)


# ---------- allowed ----------
def test_single_table_backticked_and_bare():
    assert ok(f"SELECT a FROM `{OK}`").tables == [OK]
    assert ok(f"SELECT a FROM {OK}").tables == [OK]
    assert ok("SELECT a FROM `proj-1`.sales.orders").tables == [OK]
    assert ok("SELECT a FROM `proj-1`.`sales`.`orders`").tables == [OK]


def test_join_of_allowed_tables():
    r = ok(f"SELECT o.a, c.b FROM `{OK}` o JOIN `{OK2}` c ON o.id = c.id")
    assert r.tables == sorted([OK, OK2])


def test_cte_names_are_not_tables():
    r = ok(f"WITH x AS (SELECT a FROM `{OK}`), y AS (SELECT * FROM x) SELECT * FROM y")
    assert r.tables == [OK]


def test_union_and_subquery_allowed():
    assert ok(f"SELECT a FROM `{OK}` UNION ALL SELECT a FROM `{OK2}`").tables == sorted([OK, OK2])
    assert ok(f"SELECT * FROM (SELECT a FROM `{OK}`)").tables == [OK]
    assert ok(f"(SELECT a FROM `{OK}`) UNION ALL (SELECT a FROM `{OK2}`)")


def test_unnest_on_allowed_table():
    r = ok(f"SELECT x FROM `{OK}` t, UNNEST(t.arr) AS x")
    assert r.tables == [OK]
    assert ok("SELECT * FROM UNNEST([1, 2, 3])").tables == []


def test_trailing_semicolon_ok():
    assert ok(f"SELECT 1 FROM `{OK}`;").tables == [OK]


def test_time_travel_and_pivot_ok():
    assert ok(f"SELECT * FROM `{OK}` FOR SYSTEM_TIME AS OF TIMESTAMP '2024-01-01'")


def test_normalized_sql_has_no_comments():
    r = ok(f"SELECT a /* hi */ FROM `{OK}` -- trailing\n")
    assert "hi" not in r.sql and "trailing" not in r.sql


# ---------- disallowed tables ----------
def test_other_table_same_dataset():
    bad("SELECT * FROM `proj-1.sales.secrets`")


def test_other_dataset():
    bad("SELECT * FROM `proj-1.hr.orders`")


def test_other_project():
    bad("SELECT * FROM `other-proj.sales.orders`")


def test_case_sensitive():
    bad("SELECT * FROM `proj-1.sales.ORDERS`")
    bad("SELECT * FROM `proj-1.Sales.orders`")


def test_unqualified_and_partially_qualified():
    bad("SELECT * FROM orders")
    bad("SELECT * FROM sales.orders")
    bad("SELECT * FROM `sales.orders`")


def test_subquery_with_disallowed_table():
    bad(f"SELECT * FROM `{OK}` WHERE id IN (SELECT id FROM `proj-1.hr.salaries`)")
    bad(f"SELECT a, (SELECT MAX(x) FROM `proj-1.hr.salaries`) FROM `{OK}`")
    bad(f"SELECT * FROM (SELECT * FROM `proj-1.hr.salaries`) s JOIN `{OK}` o ON o.id = s.id")


def test_cte_hiding_disallowed_table():
    bad("WITH x AS (SELECT * FROM `proj-1.hr.salaries`) SELECT * FROM x")
    bad(
        f"WITH x AS (SELECT a FROM `{OK}`), y AS (SELECT * FROM `proj-1.hr.salaries`) "
        "SELECT * FROM x, y"
    )


def test_union_with_disallowed_table():
    bad(f"SELECT a FROM `{OK}` UNION ALL SELECT a FROM `proj-1.hr.salaries`")


def test_cte_name_does_not_allow_qualified_lookalike():
    # a CTE named like a table must not let a qualified other-table through
    bad("WITH orders AS (SELECT 1 AS a) SELECT * FROM `proj-1.hr.orders`")


def test_wildcard_tables():
    bad("SELECT * FROM `proj-1.sales.orders_*`")
    bad("SELECT * FROM `proj-1.sales.*`")
    bad("SELECT * FROM `proj-1.*.orders`")


def test_information_schema_and_tables_meta():
    bad("SELECT * FROM `proj-1.sales.INFORMATION_SCHEMA.TABLES`")
    bad("SELECT * FROM proj-1.sales.INFORMATION_SCHEMA.COLUMNS")
    bad("SELECT * FROM sales.INFORMATION_SCHEMA.TABLES")
    bad("SELECT * FROM `region-us`.INFORMATION_SCHEMA.JOBS")
    bad("SELECT * FROM `proj-1.sales.__TABLES__`")
    bad("SELECT * FROM sales.__TABLES__")


def test_information_schema_only_if_explicitly_allowlisted():
    name = "proj-1.sales.INFORMATION_SCHEMA.TABLES"
    # 4-part names are never valid allowlist entries
    with pytest.raises(SqlValidationError):
        validate_allowlist_entry(name)


def test_external_query_and_table_functions():
    bad("SELECT * FROM EXTERNAL_QUERY('conn', 'SELECT 1')")
    bad("SELECT * FROM ML.PREDICT(MODEL `proj-1.sales.m`, (SELECT 1 AS a))")
    bad(f"SELECT * FROM ML.PREDICT(MODEL `proj-1.sales.m`, TABLE `{OK}`)")
    bad(f"SELECT * FROM VECTOR_SEARCH(TABLE `{OK}`, 'c', TABLE `{OK2}`)")
    bad(f"SELECT * FROM APPENDS(TABLE `{OK}`, NULL, NULL)")
    bad(f"SELECT AI.GENERATE(a) FROM `{OK}`")
    bad(f"SELECT ML.DISTANCE(a, b) FROM `{OK}`")


# ---------- statement shape ----------
def test_multi_statement():
    bad(f"SELECT 1 FROM `{OK}`; SELECT 2 FROM `{OK}`")
    bad(f"SELECT 1 FROM `{OK}`; DROP TABLE `{OK}`")
    bad(f"DROP TABLE `{OK}`; SELECT 1 FROM `{OK}`")


@pytest.mark.parametrize(
    "sql",
    [
        f"INSERT INTO `{OK}` (a) VALUES (1)",
        f"UPDATE `{OK}` SET a = 1 WHERE TRUE",
        f"DELETE FROM `{OK}` WHERE TRUE",
        f"MERGE `{OK}` t USING `{OK2}` s ON t.id = s.id WHEN MATCHED THEN DELETE",
        f"DROP TABLE `{OK}`",
        f"CREATE TABLE `proj-1.sales.new` AS SELECT * FROM `{OK}`",
        f"CREATE OR REPLACE VIEW `proj-1.sales.v` AS SELECT * FROM `{OK}`",
        f"ALTER TABLE `{OK}` ADD COLUMN z INT64",
        f"TRUNCATE TABLE `{OK}`",
        "EXECUTE IMMEDIATE 'SELECT 1'",
        "DECLARE x INT64 DEFAULT 1",
        "DECLARE x INT64 DEFAULT 1; SELECT x",
        "SET x = 1",
        "BEGIN SELECT 1; END",
        "CALL `proj-1.sales.proc`()",
        "EXPORT DATA OPTIONS(uri='gs://b/x*') AS SELECT 1",
        f"LOAD DATA INTO `{OK}` FROM FILES (format='CSV', uris=['gs://b/x'])",
        "",
        "   ",
        "this is not sql at all",
        "SELECT FROM WHERE",
    ],
)
def test_non_select_rejected(sql):
    bad(sql)


# ---------- comment / quoting tricks ----------
def test_comment_hiding_second_statement():
    bad(f"SELECT 1 FROM `{OK}` /* x */; /* y */ DROP TABLE `{OK}`")
    bad(f"SELECT 1 FROM `{OK}`; -- harmless\nDELETE FROM `{OK}` WHERE TRUE")
    bad(f"SELECT 1 FROM `{OK}`; # harmless\nDELETE FROM `{OK}` WHERE TRUE")


def test_comment_hiding_table():
    bad("SELECT * FROM /* `proj-1.sales.orders` */ `proj-1.hr.salaries`")
    bad(f"SELECT * FROM `{OK}` /* */ JOIN /* ok */ `proj-1.hr.salaries` ON TRUE")
    bad("SELECT * FROM `proj-1.hr.salaries` -- `proj-1.sales.orders`")
    bad("SELECT * FROM `proj-1.hr.salaries` # `proj-1.sales.orders`")


def test_comment_hiding_dml_keyword():
    bad("DR/**/OP TABLE x")  # broken tokens, must not parse into something valid
    bad(f"/* SELECT */ DELETE FROM `{OK}` WHERE TRUE")
    bad(f"-- SELECT 1\nDROP TABLE `{OK}`")


def test_comments_and_string_literals_do_not_confuse():
    r = ok(f"SELECT 'DROP TABLE x; -- not a comment' AS s FROM `{OK}`")
    assert r.tables == [OK]
    r = ok(f"SELECT 1 FROM `{OK}` -- ; DROP TABLE `{OK}`")
    assert "DROP" not in r.sql.upper()


def test_nested_comment_markers_cannot_smuggle_statement():
    bad(f"SELECT 1 FROM `{OK}` /* /* */ ; DROP TABLE `{OK}` ; /* */")


def test_backtick_quoted_full_names():
    assert ok(f"SELECT * FROM `{OK}`").tables == [OK]
    bad("SELECT * FROM `proj-1.sales.orders.extra`")
    bad("SELECT * FROM `proj-1.sales.orders`.field")
    bad("SELECT * FROM `proj-1`.`sales.orders`.`x`")


def test_normalized_sql_revalidates_to_same_tables():
    first = ok(f"WITH x AS (SELECT a FROM `{OK}`) SELECT * FROM x JOIN `{OK2}` c ON TRUE")
    second = ok(first.sql)
    assert second.tables == first.tables
    assert second.sql == first.sql


def test_allowlist_entry_validation():
    assert validate_allowlist_entry("p.d.t") == "p.d.t"
    for e in ("p.d", "p.d.t.x", "p.d.t*", "p..t", "p.d.`t`", "p.d.t; x"):
        with pytest.raises(SqlValidationError):
            validate_allowlist_entry(e)
