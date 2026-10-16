# Guarded execution and EXPLAIN validation against a real PostgreSQL.

from sqlalchemy import text

from backend.text_to_sql.query_executor import execute_query
from backend.text_to_sql.sql_validator import validate_sql
from backend.tests.conftest import CUSTOMER_COUNT, ORDER_COUNT


def test_row_cap_and_truncation_flag(engine):
    result = execute_query("SELECT * FROM itest_orders", engine, max_rows=100)
    assert result["success"]
    assert result["row_count"] == 100
    assert len(result["rows"]) == 100
    assert result["truncated"] is True


def test_result_within_cap_is_not_truncated(engine):
    result = execute_query("SELECT COUNT(*) AS n FROM itest_orders", engine, max_rows=100)
    assert result["success"]
    assert result["rows"] == [{"n": ORDER_COUNT}]
    assert result["truncated"] is False


def test_exact_cap_is_not_truncated(engine):
    result = execute_query("SELECT * FROM itest_orders LIMIT 100", engine, max_rows=100)
    assert result["row_count"] == 100
    assert result["truncated"] is False


def test_executor_never_runs_writes_even_without_safety_gate(engine):
    # Calls the executor directly, bypassing the AST safety check.
    for sql in (
        "CREATE TABLE itest_written (a int)",
        "SELECT * INTO itest_written FROM itest_customers",
        "WITH d AS (DELETE FROM itest_customers RETURNING *) SELECT * FROM d",
    ):
        result = execute_query(sql, engine)
        assert not result["success"], sql

    with engine.connect() as connection:
        written = connection.execute(text("SELECT to_regclass('public.itest_written') IS NOT NULL")).scalar()
        customers = connection.execute(text("SELECT COUNT(*) FROM itest_customers")).scalar()
    assert written is False
    assert customers == CUSTOMER_COUNT


def test_read_only_connection_rejects_writes(engine):
    from backend.text_to_sql.query_executor import read_only_connection

    with read_only_connection(engine) as connection:
        try:
            connection.exec_driver_sql("CREATE TABLE itest_written (a int)")
        except Exception as exc:
            message = str(getattr(exc, "orig", exc)).lower()
        else:
            message = ""
    assert "read-only transaction" in message


def test_statement_timeout_cancels_long_query(engine, monkeypatch):
    from backend.text_to_sql import query_executor

    original = query_executor.read_only_connection

    def short_timeout(engine, **_):
        return original(engine, statement_timeout_ms=200)

    monkeypatch.setattr(query_executor, "read_only_connection", short_timeout)
    result = query_executor.execute_query("SELECT pg_sleep(2)", engine)
    assert not result["success"]
    assert "statement timeout" in result["error"].lower()


def test_percent_and_colon_literals_are_sent_verbatim(engine):
    sql = (
        "SELECT '10:30 :not_a_param' AS t, '100%' AS p, COUNT(*) AS n "
        "FROM itest_customers WHERE customer_city LIKE '%paulo%'"
    )
    result = execute_query(sql, engine)
    assert result["success"], result["error"]
    row = result["rows"][0]
    assert row["t"] == "10:30 :not_a_param"
    assert row["p"] == "100%"
    assert row["n"] > 0


def test_execution_error_is_returned_not_raised(engine):
    result = execute_query("SELECT nope FROM itest_customers", engine)
    assert not result["success"]
    assert "nope" in result["error"]


def test_explain_rejects_unknown_column(engine):
    result = validate_sql("SELECT nope FROM itest_customers", engine=engine)
    assert not result["valid"]
    assert "nope" in " ".join(result["errors"])


def test_explain_rejects_unknown_table(engine):
    result = validate_sql("SELECT * FROM itest_missing_table", engine=engine)
    assert not result["valid"]


def test_explain_accepts_literal_with_colon(engine):
    result = validate_sql("SELECT '10:30 :x' AS t FROM itest_customers LIMIT 1", engine=engine)
    assert result["valid"], result
