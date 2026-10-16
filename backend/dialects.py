# backend/dialects.py
#
# The few places where PostgreSQL, MySQL, SQL Server and SQLite differ,
# in one module.
#
# Most of InsightX is database-neutral (SQLAlchemy reflection and Core
# queries). What differs: how a read-only, time-limited transaction is
# opened, EXPLAIN, identifier quoting, a handful of SQL functions
# (date buckets, casts), the sqlglot dialect used to parse SQL, and the
# rules the AI must follow when it writes SQL.
#
# The dialect of the question being answered is kept in a context
# variable (set by run_text_to_sql / the investigation service) so code
# that only sees SQL text (safety gate, verifier) parses it correctly.

import contextvars
import re
import time
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional

POSTGRES = "postgresql"
MYSQL = "mysql"
MSSQL = "mssql"
SQLITE = "sqlite"

_current = contextvars.ContextVar("insightx_dialect", default=POSTGRES)
# Business schema of the current question: "public" (or the configured
# schema) on PostgreSQL, the connected database's name on MySQL.
_schema = contextvars.ContextVar("insightx_schema", default=None)
# SQL Server: the connected database (allowed as a prefix: shop.dbo.orders).
_database = contextvars.ContextVar("insightx_database", default=None)

LABELS = {POSTGRES: "PostgreSQL", MYSQL: "MySQL", MSSQL: "SQL Server", SQLITE: "SQLite"}
DEFAULT_PORTS = {POSTGRES: 5432, MYSQL: 3306, MSSQL: 1433, SQLITE: 0}
DRIVERS = {POSTGRES: "postgresql+psycopg2", MYSQL: "mysql+pymysql", MSSQL: "mssql+pymssql", SQLITE: "sqlite"}
# Names people may use for each type (e.g. in the connect form).
ALIASES = {
    "postgresql": POSTGRES, "postgres": POSTGRES, "pg": POSTGRES,
    "mysql": MYSQL, "mariadb": MYSQL,
    "mssql": MSSQL, "sqlserver": MSSQL, "sql server": MSSQL, "sql_server": MSSQL,
    "sqlite": SQLITE, "sqlite3": SQLITE,
}

# Schemas (and SQL Server databases) that hold the server's own
# metadata, never business data.
SYSTEM_SCHEMAS = {
    POSTGRES: {"pg_catalog", "information_schema", "pg_toast"},
    MYSQL: {"mysql", "information_schema", "performance_schema", "sys"},
    MSSQL: {"sys", "information_schema", "master", "msdb", "tempdb", "model", "guest"},
    SQLITE: {"temp"},
}


def kind(engine_or_name: Any = None) -> str:
    """POSTGRES, MYSQL, MSSQL or SQLITE for an engine, a name, or the current question."""
    if engine_or_name is None:
        return _current.get()
    name = engine_or_name if isinstance(engine_or_name, str) else engine_or_name.dialect.name
    name = name.strip().lower()
    if name in ALIASES:
        return ALIASES[name]
    if name.startswith(("mysql", "mariadb")):
        return MYSQL
    if name.startswith(("mssql", "sqlserver")):
        return MSSQL
    if name.startswith("sqlite"):
        return SQLITE
    return POSTGRES


def is_known(name: str) -> bool:
    return str(name or "").strip().lower() in ALIASES


def keeps_chat_memory_in_database(engine_or_name: Any) -> bool:
    """Only PostgreSQL stores the chat memory in the connected database itself."""
    return kind(engine_or_name) == POSTGRES


def current() -> str:
    return _current.get()


_MSSQL_SCHEMAS: dict = {}


def default_schema(engine) -> str:
    """Where the business tables live for this connection."""
    if kind(engine) == MYSQL:
        return str(engine.url.database or "")
    if kind(engine) == SQLITE:
        return "main"
    if kind(engine) == MSSQL:
        key = engine.url.render_as_string(hide_password=True)
        if key not in _MSSQL_SCHEMAS:
            try:
                with engine.connect() as connection:
                    _MSSQL_SCHEMAS[key] = str(connection.exec_driver_sql("SELECT SCHEMA_NAME()").scalar() or "dbo")
            except Exception:
                return "dbo"
        return _MSSQL_SCHEMAS[key]
    from backend.config import query_settings

    return query_settings.allowed_schemas[0]


def allowed_schemas(engine=None) -> tuple:
    """Schemas generated SQL may name explicitly."""
    if kind(engine) in (MYSQL, MSSQL, SQLITE):
        schema = default_schema(engine) if engine is not None else _schema.get()
        schema = schema or {MSSQL: "dbo", SQLITE: "main"}.get(kind(engine))
        return (schema.lower(),) if schema else ()
    from backend.config import query_settings

    return tuple(query_settings.allowed_schemas)


def current_schema() -> Optional[str]:
    return _schema.get()


@contextmanager
def using(engine_or_name: Any) -> Iterator[str]:
    """Answer one question (or investigation) against this database type."""
    token = _current.set(kind(engine_or_name))
    is_engine = not isinstance(engine_or_name, str)
    schema_token = _schema.set(default_schema(engine_or_name) if is_engine else _schema.get())
    database_token = _database.set(
        str(engine_or_name.url.database or "") if is_engine and kind(engine_or_name) == MSSQL else _database.get()
    )
    try:
        yield _current.get()
    finally:
        _database.reset(database_token)
        _schema.reset(schema_token)
        _current.reset(token)


_SQLGLOT = {POSTGRES: "postgres", MYSQL: "mysql", MSSQL: "tsql", SQLITE: "sqlite"}


def sqlglot_name(engine_or_name: Any = None) -> str:
    return _SQLGLOT[kind(engine_or_name)]


def database_name(engine_or_name: Any = None) -> Optional[str]:
    """The connected database's name, when SQL may use it as a prefix (SQL Server)."""
    if engine_or_name is not None and not isinstance(engine_or_name, str) and kind(engine_or_name) == MSSQL:
        return str(engine_or_name.url.database or "") or None
    return _database.get() if kind(engine_or_name) == MSSQL else None


def label(engine_or_name: Any = None) -> str:
    return LABELS[kind(engine_or_name)]


def quote(name: str, engine_or_name: Any = None) -> str:
    if kind(engine_or_name) == MYSQL:
        return "`" + str(name).replace("`", "``") + "`"
    if kind(engine_or_name) == MSSQL:
        return "[" + str(name).replace("]", "]]") + "]"
    return '"' + str(name).replace('"', '""') + '"'


def select_limited(body: str, rows: int, distinct: bool = False, engine_or_name: Any = None) -> str:
    """'SELECT [DISTINCT] <body>' returning at most `rows` rows (body has no LIMIT)."""
    head = "SELECT DISTINCT" if distinct else "SELECT"
    if kind(engine_or_name) == MSSQL:
        return f"{head} TOP {int(rows)} {body}"
    return f"{head} {body} LIMIT {int(rows)}"


# ------------------------------------------------------------
# Transactions, timeouts, EXPLAIN
# ------------------------------------------------------------

def start_read_only(connection, statement_timeout_ms: int, lock_timeout_ms: Optional[int] = None) -> None:
    """
    Make the transaction the connection is about to run read-only and
    time-limited.

    PostgreSQL: SET TRANSACTION / SET LOCAL inside the open transaction.
    MySQL: SET TRANSACTION READ ONLY applies to the next transaction, so
    it is sent before the first statement; the time limit is a session
    setting (max_execution_time on MySQL, max_statement_time on MariaDB).
    """
    if kind(connection.engine) == MYSQL:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        set_timeout(connection, statement_timeout_ms)
        if lock_timeout_ms:
            seconds = max(1, int(lock_timeout_ms) // 1000)
            connection.exec_driver_sql(f"SET SESSION innodb_lock_wait_timeout = {seconds}")
        return
    if kind(connection.engine) == SQLITE:
        # The file is also opened read-only (see database.create_sqlite_engine).
        connection.exec_driver_sql("PRAGMA query_only = ON")
        set_timeout(connection, statement_timeout_ms)
        return
    if kind(connection.engine) == MSSQL:
        # SQL Server has no read-only transaction: the safety check blocks
        # writes and the transaction is always rolled back. The time limit
        # is the driver's query timeout (database.create_mssql_engine).
        if lock_timeout_ms:
            connection.exec_driver_sql(f"SET LOCK_TIMEOUT {int(lock_timeout_ms)}")
        return
    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
    connection.exec_driver_sql(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}")
    if lock_timeout_ms:
        connection.exec_driver_sql(f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}")


def _sqlite_connection(connection):
    fairy = connection.connection
    return getattr(fairy, "driver_connection", None) or fairy.dbapi_connection


def clear_sqlite_timeout(dbapi_connection) -> None:
    dbapi_connection.set_progress_handler(None, 0)


def set_timeout(connection, timeout_ms: int) -> None:
    """Session-level statement time limit (used for profiling, MySQL and SQLite)."""
    if kind(connection.engine) == SQLITE:
        # SQLite has no time limit setting: stop the query from Python
        # once the deadline passes.
        deadline = time.monotonic() + int(timeout_ms) / 1000.0
        _sqlite_connection(connection).set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0, 10000
        )
        return
    if kind(connection.engine) == MSSQL:
        return  # the driver's query timeout applies
    if kind(connection.engine) == MYSQL:
        try:
            connection.exec_driver_sql(f"SET SESSION max_execution_time = {int(timeout_ms)}")
        except Exception:
            # MariaDB names it differently and counts seconds.
            connection.exec_driver_sql(f"SET SESSION max_statement_time = {max(1, int(timeout_ms) // 1000)}")
        return
    connection.exec_driver_sql(f"SET statement_timeout = {int(timeout_ms)}")


def reset_timeout(connection) -> None:
    if kind(connection.engine) == SQLITE:
        clear_sqlite_timeout(_sqlite_connection(connection))
        return
    if kind(connection.engine) == MSSQL:
        return
    if kind(connection.engine) == MYSQL:
        for statement in ("SET SESSION max_execution_time = 0", "SET SESSION max_statement_time = 0"):
            try:
                connection.exec_driver_sql(statement)
                return
            except Exception:
                continue
        return
    connection.exec_driver_sql("RESET statement_timeout")


def explain(sql: str, engine_or_name: Any) -> str:
    if kind(engine_or_name) == MYSQL:
        return "EXPLAIN " + sql
    if kind(engine_or_name) == SQLITE:
        return "EXPLAIN QUERY PLAN " + sql
    return "EXPLAIN (FORMAT JSON) " + sql


def check_plan(connection, sql: str) -> None:
    """Ask the database to plan (not run) the query; raises if it is invalid."""
    if kind(connection.engine) == MSSQL:
        # SHOWPLAN must be alone in its batch; while it is on, statements
        # are compiled and planned but not run.
        connection.exec_driver_sql("SET SHOWPLAN_XML ON")
        try:
            result = connection.exec_driver_sql(sql)
            if result.returns_rows:
                result.fetchall()
        finally:
            connection.exec_driver_sql("SET SHOWPLAN_XML OFF")
        return
    connection.exec_driver_sql(explain(sql, connection.engine)).first()


def prepare_sql(sql: str) -> str:
    """
    Last fixes before SQL is sent to the database. SQL Server does not
    accept GROUP BY 1 (a position in the SELECT list), which InsightX's
    own chart and investigation queries use: the expression is written
    out instead.
    """
    if current() != MSSQL or not re.search(r"(?i)\bGROUP\s+BY\s+\d", sql):
        return sql
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, read="tsql")
    except Exception:
        return sql
    for select in tree.find_all(exp.Select):
        group = select.args.get("group")
        if not group:
            continue
        expressions = []
        for item in group.expressions:
            position = int(item.this) if isinstance(item, exp.Literal) and item.is_int else 0
            if 1 <= position <= len(select.expressions):
                expressions.append(select.expressions[position - 1].unalias().copy())
            else:
                expressions.append(item)
        group.set("expressions", expressions)
    return tree.sql(dialect="tsql")


# ------------------------------------------------------------
# SQL snippets that InsightX writes itself (charts, investigations)
# ------------------------------------------------------------

_GRAINS = ("day", "week", "month", "quarter", "year")


def period(grain: str, column_sql: str, engine_or_name: Any = None) -> str:
    """The start of the day/week/month/quarter/year a timestamp falls in."""
    grain = grain.lower()
    if grain not in _GRAINS:
        raise ValueError(f"Unknown time grain: {grain}")
    if kind(engine_or_name) == SQLITE:
        c = column_sql
        if grain == "day":
            return f"datetime(date({c}))"
        if grain == "week":  # Monday of the week, like date_trunc
            return f"datetime(date({c}, '-6 days', 'weekday 1'))"
        if grain == "month":
            return f"datetime(strftime('%Y-%m-01', {c}))"
        if grain == "quarter":
            return (f"datetime(strftime('%Y', {c}) || '-' || "
                    f"printf('%02d', ((CAST(strftime('%m', {c}) AS INTEGER) - 1) / 3) * 3 + 1) || '-01')")
        return f"datetime(strftime('%Y-01-01', {c}))"
    if kind(engine_or_name) == MSSQL:
        c = column_sql
        if grain == "week":  # Monday of the week, whatever DATEFIRST is
            return f"CAST(DATEADD(day, -((DATEPART(weekday, {c}) + @@DATEFIRST - 2) % 7), CAST({c} AS DATE)) AS DATETIME)"
        return f"DATEADD({grain}, DATEDIFF({grain}, 0, {c}), 0)"
    if kind(engine_or_name) != MYSQL:
        return f"date_trunc('{grain}', {column_sql})"
    if grain == "day":
        return f"CAST(DATE({column_sql}) AS DATETIME)"
    if grain == "week":  # ISO weeks start on Monday, like date_trunc
        return f"CAST(DATE_SUB(DATE({column_sql}), INTERVAL WEEKDAY({column_sql}) DAY) AS DATETIME)"
    if grain == "month":
        return f"CAST(DATE_FORMAT({column_sql}, '%Y-%m-01') AS DATETIME)"
    if grain == "quarter":
        return (f"CAST(MAKEDATE(YEAR({column_sql}), 1) + INTERVAL (QUARTER({column_sql}) - 1) QUARTER "
                "AS DATETIME)")
    return f"CAST(DATE_FORMAT({column_sql}, '%Y-01-01') AS DATETIME)"


def timestamp_literal(value: str, engine_or_name: Any = None) -> str:
    text = "'" + str(value).replace("'", "''") + "'"
    if kind(engine_or_name) == MYSQL:
        return f"CAST({text} AS DATETIME)"
    if kind(engine_or_name) == SQLITE:
        return f"datetime({text})"
    if kind(engine_or_name) == MSSQL:
        return f"CAST({text} AS DATETIME2)"
    return f"{text}::timestamp"


def cast_text(expression: str, engine_or_name: Any = None) -> str:
    if kind(engine_or_name) == MYSQL:
        return f"CAST({expression} AS CHAR)"
    if kind(engine_or_name) == MSSQL:
        return f"CAST({expression} AS NVARCHAR(4000))"
    return f"CAST({expression} AS TEXT)"


def seconds_between(later: str, earlier: str, engine_or_name: Any = None) -> str:
    if kind(engine_or_name) == MYSQL:
        return f"TIMESTAMPDIFF(SECOND, {earlier}, {later})"
    if kind(engine_or_name) == SQLITE:
        return f"CAST(ROUND((julianday({later}) - julianday({earlier})) * 86400) AS INTEGER)"
    if kind(engine_or_name) == MSSQL:
        return f"DATEDIFF_BIG(SECOND, {earlier}, {later})"
    return f"EXTRACT(EPOCH FROM ({later} - {earlier}))"


# ------------------------------------------------------------
# Rules for the AI
# ------------------------------------------------------------

MYSQL_RULES = """
============================================================
DATABASE ENGINE: MySQL 8 / MariaDB (NOT PostgreSQL)
============================================================

Write SQL that runs on MySQL:
- Quote identifiers with backticks only when needed: `order`.
- No PostgreSQL casts (x::int). Use CAST(x AS SIGNED), CAST(x AS DECIMAL(18,2)),
  CAST(x AS CHAR), CAST(x AS DATE), CAST(x AS DATETIME).
- No ILIKE (LIKE is already case-insensitive with the default collation).
- Dates: YEAR(d), MONTH(d), QUARTER(d), DATE(d), DATE_FORMAT(d, '%Y-%m'),
  DATEDIFF(later, earlier) for days, TIMESTAMPDIFF(unit, earlier, later),
  d + INTERVAL 7 DAY, CURDATE(), NOW(). There is no DATE_TRUNC,
  no EXTRACT(EPOCH ...), no AGE(), no generate_series.
- Strings: CONCAT(a, b) (not ||), SUBSTRING, LOWER, TRIM.
- No FULL OUTER JOIN (use LEFT JOIN ... UNION ... RIGHT JOIN), no
  NULLS FIRST/LAST, no DISTINCT ON, no FILTER (WHERE ...) on aggregates:
  use SUM(CASE WHEN ... THEN 1 ELSE 0 END).
- Window functions and CTEs (WITH ...) are allowed.
- Integer division: a / b gives a decimal; use DIV for whole numbers.
- Text comparisons (=, LIKE) usually ignore case and accents here, but
  still use the stored values exactly as shown in the schema.
"""


MSSQL_RULES = """
============================================================
DATABASE ENGINE: Microsoft SQL Server / T-SQL (NOT PostgreSQL)
============================================================

Write SQL that runs on SQL Server:
- There is no LIMIT. Use SELECT TOP 10 ... ORDER BY ..., or
  ORDER BY ... OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY.
- Quote identifiers with square brackets only when needed: [order].
- No PostgreSQL casts (x::int). Use CAST(x AS INT), CAST(x AS DECIMAL(18,2)),
  CAST(x AS NVARCHAR(200)), CAST(x AS DATE), CAST(x AS DATETIME2).
- GROUP BY must repeat the expressions; GROUP BY 1 (a position) is not allowed.
  Columns in ORDER BY with TOP or DISTINCT must be in the SELECT list or grouped.
- No ILIKE (LIKE is usually case-insensitive already). No boolean
  columns in WHERE without a comparison: use flag = 1.
- Dates: YEAR(d), MONTH(d), DATEPART(quarter, d), CAST(d AS DATE),
  FORMAT(d, 'yyyy-MM'), DATEFROMPARTS(y, m, 1), EOMONTH(d),
  DATEDIFF(day, earlier, later), DATEADD(day, 7, d), GETDATE().
  There is no DATE_TRUNC (except on SQL Server 2022), no EXTRACT, no INTERVAL,
  no generate_series.
- Strings: CONCAT(a, b) or a + b, SUBSTRING(s, start, length), LEN(s),
  LOWER, LTRIM/RTRIM, STRING_AGG(x, ', ').
- Integer division: 7 / 2 = 3. Use 1.0 * a / b or CAST(a AS DECIMAL(18,4)) / b.
- No NULLS FIRST/LAST, no DISTINCT ON, no FILTER (WHERE ...): use
  SUM(CASE WHEN ... THEN 1 ELSE 0 END). Use IIF or CASE, no GREATEST/LEAST
  before SQL Server 2022.
- CTEs (WITH ...) and window functions are allowed. One statement only,
  no semicolon-separated batches, no EXEC, no SELECT ... INTO.
"""

SQLITE_RULES = """
============================================================
DATABASE ENGINE: SQLite (NOT PostgreSQL)
============================================================

Write SQL that runs on SQLite:
- Quote identifiers with double quotes only when needed: "order".
- No PostgreSQL casts (x::int). Use CAST(x AS INTEGER), CAST(x AS REAL), CAST(x AS TEXT).
- Dates are usually stored as text 'YYYY-MM-DD HH:MM:SS'. Use
  strftime('%Y', d), strftime('%Y-%m', d), date(d), datetime(d),
  date(d, '+7 days'), julianday(later) - julianday(earlier) for days,
  date('now'). There is no DATE_TRUNC, EXTRACT, YEAR(), MONTH(),
  INTERVAL, AGE() or generate_series. strftime returns text: compare
  with text ('2017'), or CAST(strftime('%Y', d) AS INTEGER) = 2017.
- No ILIKE: LIKE already ignores case for ASCII letters. lower(x) = lower('v').
- Strings: a || b, substr(s, start, length), length(s), lower, trim,
  group_concat(x, ', ').
- Integer division: 7 / 2 = 3. Use a * 1.0 / b.
- No FULL OUTER JOIN or RIGHT JOIN on old versions; prefer LEFT JOIN.
  No DISTINCT ON. NULLS FIRST/LAST, CTEs and window functions are allowed.
- Use LIMIT for top-N results. One statement only.
"""

_RULES = {MYSQL: MYSQL_RULES, MSSQL: MSSQL_RULES, SQLITE: SQLITE_RULES}


def _rename_engine(text: str) -> str:
    return re.sub(r"\bPostgreSQL\b", LABELS[current()], text)


def adapt_text(prompt: str) -> str:
    """adapt_messages for a prompt that is already one string."""
    if current() not in _RULES or "PostgreSQL" not in prompt:
        return prompt
    return _rename_engine(prompt) + "\n" + _RULES[current()]


def adapt_messages(messages: List[Any]) -> List[Any]:
    """
    Prompts are written for PostgreSQL. For a question on another
    database, rename the engine in them and add that database's rules
    to the system message.
    """
    if current() not in _RULES:
        return messages
    adapted = []
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, str):
            content = _rename_engine(content)
            if getattr(message, "type", "") == "system":
                content = content + "\n" + _RULES[current()]
            message = message.model_copy(update={"content": content}) if hasattr(message, "model_copy") else type(message)(content=content)
        adapted.append(message)
    return adapted
