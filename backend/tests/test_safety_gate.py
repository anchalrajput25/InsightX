# Deterministic SQL safety gate (no database needed).

import pytest

from backend.text_to_sql.sql_safety import (
    check_sql_safety,
    has_multiple_statements,
    strip_literals_and_comments,
)

SAFE = {
    "simple select": "SELECT customer_city, COUNT(*) FROM itest_customers GROUP BY 1 ORDER BY 2 DESC LIMIT 1",
    "cte + window": (
        "WITH c AS (SELECT customer_id, COUNT(*) n FROM itest_orders GROUP BY 1) "
        "SELECT customer_id, RANK() OVER (ORDER BY n DESC) FROM c"
    ),
    "literal 'set'": "SELECT COUNT(*) FROM itest_orders WHERE customer_id = 'set'",
    "literal 'do'": "SELECT 'do' AS x",
    "literal 'drop table'": "SELECT 'drop table x; delete' AS x",
    "semicolon in literal": "SELECT 'a;b' AS x",
    "trailing semicolon": "SELECT 1;",
    "postgres cast": "SELECT price::int FROM itest_orders",
    "like with percent": "SELECT * FROM itest_customers WHERE customer_city LIKE '%paulo%'",
    "explicit public schema": "SELECT * FROM public.itest_customers",
    "keyword in comment": "SELECT 1 -- DROP TABLE x",
    "keyword in quoted identifier": 'SELECT 1 AS "update"',
    "generate_series": "SELECT g FROM generate_series(1, 3) AS g",
    "markdown fence": "```sql\nSELECT 1\n```",
}

UNSAFE = {
    "insert": "INSERT INTO itest_customers VALUES ('x', 'y')",
    "update": "UPDATE itest_customers SET customer_city = 'x'",
    "delete": "DELETE FROM itest_customers",
    "drop": "DROP TABLE itest_customers",
    "truncate": "TRUNCATE itest_customers",
    "alter": "ALTER TABLE itest_customers ADD COLUMN x int",
    "grant": "GRANT SELECT ON itest_customers TO public",
    "copy": "COPY itest_customers TO STDOUT",
    "set": "SET statement_timeout = 0",
    "vacuum": "VACUUM itest_customers",
    "multiple statements": "SELECT 1; DROP TABLE itest_customers",
    "data-modifying cte": "WITH d AS (DELETE FROM itest_customers RETURNING *) SELECT * FROM d",
    "select into": "SELECT * INTO itest_written FROM itest_customers",
    "for update": "SELECT * FROM itest_customers FOR UPDATE",
    "pg_read_file": "SELECT pg_read_file('/etc/hostname')",
    "pg_sleep": "SELECT pg_sleep(10)",
    "set_config": "SELECT set_config('statement_timeout', '0', false)",
    "nextval": "SELECT nextval('some_seq')",
    "dblink": "SELECT * FROM dblink('host=x', 'select 1') AS t(a int)",
    "version": "SELECT version()",
    "current_user": "SELECT current_user",
    "pg_catalog": "SELECT * FROM pg_catalog.pg_class",
    "unqualified pg table": "SELECT usename, passwd FROM pg_shadow",
    "information_schema": "SELECT * FROM information_schema.tables",
    "checkpoint schema": "SELECT * FROM insightx.checkpoints",
    "empty": "   ",
    "unparseable": "SELEC FROM WHERE",
}


@pytest.mark.parametrize("name", SAFE)
def test_safe_queries_pass(name):
    result = check_sql_safety(SAFE[name])
    assert result["safe"], result


@pytest.mark.parametrize("name", UNSAFE)
def test_unsafe_queries_rejected(name):
    result = check_sql_safety(UNSAFE[name])
    assert not result["safe"], f"{name} was accepted: {result}"
    assert result["status"] == "UNSAFE"
    assert result["errors"]


def test_strip_literals_removes_strings_identifiers_and_comments():
    stripped = strip_literals_and_comments(
        "SELECT 'drop', E'it\\'s', $$delete$$, \"update\" /* set */ -- grant\nFROM t"
    )
    for word in ("drop", "delete", "update", "set", "grant", "it"):
        assert word not in stripped.lower()
    assert "FROM t" in stripped


def test_multiple_statement_detection_ignores_literals():
    assert not has_multiple_statements("SELECT 'a;b'")
    assert not has_multiple_statements("SELECT 1;")
    assert has_multiple_statements("SELECT 1; SELECT 2")
