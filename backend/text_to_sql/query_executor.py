# backend/text_to_sql/query_executor.py
#
# Guarded execution of generated SQL.
#
# Independent of the AST safety check, every statement runs inside:
#   - a READ ONLY transaction that is always rolled back,
#   - statement_timeout / lock_timeout set for that transaction only,
#   - a server-side cursor with a hard row cap (+1 row to detect
#     truncation), so a broad query cannot pull millions of rows into
#     backend memory or the API response.

import logging
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy.engine import Connection, Engine

from backend import dialects, telemetry
from backend.config import query_settings
from backend.text_to_sql.sql_safety import clean_sql

logger = logging.getLogger("insightx.executor")


# ============================================================
# READ-ONLY TRANSACTION
# ============================================================

@contextmanager
def read_only_connection(
    engine: Engine,
    statement_timeout_ms: int = query_settings.statement_timeout_ms,
    lock_timeout_ms: int = query_settings.lock_timeout_ms,
) -> Iterator[Connection]:
    """
    Yield a connection inside a READ ONLY transaction with server-side
    timeouts. The transaction is always rolled back.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            # Must be the first statement of the transaction. Values are
            # ints validated in backend.config (SET cannot take binds).
            dialects.start_read_only(connection, statement_timeout_ms, lock_timeout_ms)
            yield connection
        finally:
            if transaction.is_active:
                transaction.rollback()


def driver_sql(sql: str, engine: Engine) -> str:
    """
    Prepare SQL for exec_driver_sql.

    Generated SQL is sent as-is (no SQLAlchemy text() parsing, which
    would treat ':name' inside string literals as bind parameters).
    Drivers with format/pyformat paramstyle (psycopg2) still interpret
    '%', so it is escaped for them.
    """
    sql = dialects.prepare_sql(sql)
    # pymssql leaves "%" alone when no parameters are passed.
    if engine.dialect.paramstyle in ("format", "pyformat") and dialects.kind(engine) != dialects.MSSQL:
        return sql.replace("%", "%%")
    return sql


# ============================================================
# RESULT FORMAT
# ============================================================

def execution_result(
    success: bool,
    status: str,
    rows: Optional[List[Dict[str, Any]]] = None,
    columns: Optional[List[str]] = None,
    execution_time_ms: float = 0.0,
    error: Optional[str] = None,
    truncated: bool = False,
    max_rows: int = query_settings.max_result_rows,
) -> Dict[str, Any]:
    rows = rows or []
    return {
        "success": success,
        "status": status,
        "rows": rows,
        "columns": columns or [],
        # Number of rows returned (never more than max_rows).
        "row_count": len(rows),
        # True when the query produced more rows than max_rows.
        "truncated": truncated,
        "max_rows": max_rows,
        "execution_time_ms": execution_time_ms,
        "error": error,
    }


# ============================================================
# EXECUTION
# ============================================================

def execute_query(
    sql: str,
    engine: Engine,
    max_rows: int = query_settings.max_result_rows,
) -> Dict[str, Any]:
    """
    Execute SQL that already passed the safety check and validation.

    Never raises for database errors: failures are returned as a
    FAILED result so the agent can route to correction.
    """
    started = time.perf_counter()
    result = _execute_query(sql, engine, max_rows)
    telemetry.record_db(
        clean_sql(sql or ""), time.perf_counter() - started, rows=result.get("row_count", 0),
        ok=bool(result.get("success")), error=str(result.get("error") or ""),
    )
    return result


def _execute_query(sql: str, engine: Engine, max_rows: int) -> Dict[str, Any]:
    sql = clean_sql(sql)

    if not sql:
        return execution_result(success=False, status="FAILED", error="SQL query is empty.", max_rows=max_rows)
    if engine is None:
        return execution_result(success=False, status="FAILED", error="Database engine was not provided.", max_rows=max_rows)

    start = time.perf_counter()

    try:
        with read_only_connection(engine) as connection:
            result = connection.exec_driver_sql(
                driver_sql(sql, engine),
                execution_options={"stream_results": True, "max_row_buffer": max_rows + 1},
            )
            columns = list(result.keys())
            fetched = result.fetchmany(max_rows + 1)
            result.close()

        truncated = len(fetched) > max_rows
        rows = [dict(row._mapping) for row in fetched[:max_rows]]
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

        return execution_result(
            success=True,
            status="SUCCESS",
            rows=rows,
            columns=columns,
            execution_time_ms=elapsed_ms,
            truncated=truncated,
            max_rows=max_rows,
        )

    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        # The driver message (e.g. "column x does not exist") is what
        # the correction step needs; keep it, without the SQLAlchemy
        # boilerplate that repeats the full statement.
        message = str(getattr(exc, "orig", None) or exc).strip()
        logger.warning("SQL execution failed after %.2f ms: %s", elapsed_ms, message.splitlines()[0] if message else "")
        return execution_result(
            success=False,
            status="FAILED",
            execution_time_ms=elapsed_ms,
            error=message,
            max_rows=max_rows,
        )


def execute_sql(sql: str, engine: Engine) -> List[Dict[str, Any]]:
    """Execute SQL and return only rows; raises on failure."""
    result = execute_query(sql=sql, engine=engine)
    if not result["success"]:
        raise RuntimeError(result["error"])
    return result["rows"]
