# backend/database.py
#
# PostgreSQL / MySQL / SQL Server / SQLite engine creation, connection
# testing and user-facing classification of connection errors.

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, Engine

from backend import dialects
from backend.config import database_settings, query_settings


class DatabaseConnectionError(Exception):
    """A connection problem with an HTTP status and a safe message."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class _ErrorRule:
    markers: tuple[str, ...]
    status_code: int
    message: str


# Ordered: the first rule whose marker appears in the driver message wins.
_CONNECTION_ERROR_RULES = (
    _ErrorRule(
        ("password authentication failed", "no password supplied", "authentication failed", "access denied for user",
         "login failed for user"),
        401,
        "Authentication failed: check the username and password.",
    ),
    _ErrorRule(
        ("role ",),  # 'role "x" does not exist'
        401,
        "The PostgreSQL user does not exist.",
    ),
    _ErrorRule(
        ("database ",),  # 'database "x" does not exist'
        404,
        "The database does not exist on this server.",
    ),
    _ErrorRule(
        ("unknown database", "cannot open database"),  # MySQL 1049, SQL Server 4060
        404,
        "The database does not exist on this server.",
    ),
    _ErrorRule(
        ("timeout expired", "timed out"),
        504,
        "Connection timed out: the server did not respond. Check host, port and firewall/VPN.",
    ),
    _ErrorRule(
        ("could not translate host name", "name or service not known", "nodename nor servname",
         "unknown mysql server host", "getaddrinfo failed"),
        400,
        "Unknown host name.",
    ),
    _ErrorRule(
        ("connection refused", "could not connect", "no route to host", "network is unreachable", "is the server running",
         "can't connect to mysql server", "can't connect to server", "adaptive server is unavailable",
         "unable to connect"),
        503,
        "Cannot reach the database server at this host/port. Check that the server is running.",
    ),
)


def create_postgres_engine(
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> Engine:
    database_url = URL.create(
        drivername="postgresql+psycopg2",
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    )

    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={
            # Fail fast instead of waiting for the OS TCP timeout.
            "connect_timeout": database_settings.connect_timeout_s,
            "application_name": "insightx",
        },
    )


def create_mysql_engine(
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> Engine:
    database_url = URL.create(
        drivername=dialects.DRIVERS[dialects.MYSQL],
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
        query={"charset": "utf8mb4"},
    )

    return create_engine(
        database_url,
        pool_pre_ping=True,
        # MySQL closes idle connections after wait_timeout (8 h default).
        pool_recycle=3600,
        connect_args={"connect_timeout": database_settings.connect_timeout_s},
    )


def create_mssql_engine(
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> Engine:
    try:
        import pymssql  # noqa: F401
    except ImportError as exc:
        raise DatabaseConnectionError(
            500, "SQL Server support needs the pymssql driver: run 'pip install pymssql' and restart the backend."
        ) from exc
    database_url = URL.create(
        drivername=dialects.DRIVERS[dialects.MSSQL],
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    )
    # SQL Server has no per-statement time limit setting, so the driver's
    # query timeout is the limit for every statement.
    timeout_s = math.ceil(max(query_settings.statement_timeout_ms,
                              database_settings.profile_statement_timeout_ms) / 1000)
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={"login_timeout": database_settings.connect_timeout_s, "timeout": timeout_s},
    )


def create_sqlite_engine(path: str) -> Engine:
    """A SQLite database file, opened read-only."""
    file = Path(path).expanduser().resolve()
    if not file.is_file():
        raise DatabaseConnectionError(404, "The SQLite file was not found.")
    with file.open("rb") as handle:
        if handle.read(16) != b"SQLite format 3\x00":
            raise DatabaseConnectionError(400, "This file is not a SQLite database.")
    uri = file.as_uri() + "?mode=ro"

    engine = create_engine(
        URL.create("sqlite", database=str(file)),  # names the file (cache keys, labels)
        creator=lambda: sqlite3.connect(uri, uri=True, check_same_thread=False),
    )

    @event.listens_for(engine, "checkin")
    def _clear_time_limit(dbapi_connection, _record):
        # A time limit set for one query must not stop the next one.
        dialects.clear_sqlite_timeout(dbapi_connection)

    return engine


def create_database_engine(
    db_type: str,
    host: str,
    port: int,
    database: str,
    username: str,
    password: str,
) -> Engine:
    """Engine for a PostgreSQL, MySQL or SQL Server server, or a SQLite file (database = its path)."""
    if dialects.kind(db_type or dialects.POSTGRES) == dialects.SQLITE:
        return create_sqlite_engine(database)
    if dialects.kind(db_type or dialects.POSTGRES) == dialects.MSSQL:
        return create_mssql_engine(host, port, database, username, password)
    if dialects.kind(db_type or dialects.POSTGRES) == dialects.MYSQL:
        try:
            import pymysql  # noqa: F401
        except ImportError as exc:
            raise DatabaseConnectionError(
                500, "MySQL support needs the PyMySQL driver: run 'pip install pymysql' and restart the backend."
            ) from exc
        return create_mysql_engine(host, port, database, username, password)
    return create_postgres_engine(host, port, database, username, password)


def classify_connection_error(exc: Exception, password: str = "") -> DatabaseConnectionError:
    """
    Map a driver exception to an HTTP status and a message that is
    safe to show (the password is never included).
    """
    raw = str(getattr(exc, "orig", None) or exc).strip()
    first_line = raw.splitlines()[0] if raw else type(exc).__name__
    if password and len(password) >= 3:
        first_line = first_line.replace(password, "***")

    lowered = raw.lower()
    for rule in _CONNECTION_ERROR_RULES:
        if any(marker in lowered for marker in rule.markers):
            # "role"/"database" markers only count for "does not exist".
            if rule.markers in (("role ",), ("database ",)) and "does not exist" not in lowered:
                continue
            return DatabaseConnectionError(rule.status_code, f"{rule.message} ({first_line})")

    return DatabaseConnectionError(502, f"Database connection failed: {first_line}")


def test_postgres_connection(engine: Engine) -> None:
    """Open a connection and run SELECT 1. Raises DatabaseConnectionError."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise classify_connection_error(exc, engine.url.password or "") from exc
