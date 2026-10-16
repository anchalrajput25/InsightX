# backend/fast_ingest.py
#
# Fast path for big CSV files, using DuckDB (an in-process analytics
# engine; no server to install).
#
# The pandas path reads a whole file into memory twice (once to inspect,
# once to import) and checks types value by value in Python. For a file
# with millions of rows that takes minutes and gigabytes of memory.
# DuckDB reads the file in parallel and streams it, so inspection is one
# SQL pass and the import is a streamed COPY into PostgreSQL.
#
# The rules are the same as backend.ingestion: every value is read as
# text, only empty cells are NULL, and the type rules of infer_sql_type
# are applied to every value (not a sample). If DuckDB is not installed,
# or cannot read a file (unusual encoding, malformed rows), the caller
# falls back to the pandas path, which reports the problem as before.

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("insightx.ingestion")

try:  # optional dependency
    import duckdb
except ImportError:  # pragma: no cover - depends on the install
    duckdb = None

# CSV files at least this big use DuckDB; smaller ones keep the pandas
# path, which is fast enough and also handles non-UTF-8 files.
MIN_BYTES = int(float(os.getenv("INSIGHTX_DUCKDB_MIN_MB", "5")) * 1024 * 1024)

# Same patterns as backend.ingestion (RE2 syntax).
_INTEGER = r"[+-]?\d+"
_LEADING_ZERO = r"[+-]?0\d.*"
_FLOAT = r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?"
_DATE = r"\d{4}-\d{2}-\d{2}"
_TIMESTAMP = r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)?"


def available() -> bool:
    return duckdb is not None


def is_big_csv(path: Path) -> bool:
    path = Path(path)
    try:
        return path.suffix.lower() == ".csv" and path.stat().st_size >= MIN_BYTES
    except OSError:
        return False


def wants(path: Path) -> bool:
    """True when this file should take the DuckDB path."""
    path = Path(path)
    if not available() or path.suffix.lower() != ".csv":
        return False
    try:
        return path.stat().st_size >= MIN_BYTES
    except OSError:
        return False


def _literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _source(path: Path) -> str:
    # Every value as text, comma separated, like pandas.read_csv(dtype=str).
    return (
        f"read_csv({_literal(Path(path).as_posix())}, header=true, delim=',', quote='\"', "
        "escape='\"', all_varchar=true, normalize_names=false, encoding='utf-8')"
    )


def _connect():
    connection = duckdb.connect(database=":memory:")
    connection.execute("SET preserve_insertion_order = true")
    return connection


def _infer(stats: Dict[str, Any]) -> str:
    """infer_sql_type, from counts over all non-empty values."""
    filled = stats["filled"]
    if not filled:
        return "TEXT"
    if stats["integers"] == filled and not stats["leading_zero"]:
        return "BIGINT" if stats["bigints"] == filled else "NUMERIC"
    if stats["floats"] == filled:
        return "DOUBLE PRECISION" if not stats["leading_zero"] else "TEXT"
    if stats["booleans"] == filled:
        return "BOOLEAN"
    if stats["dates"] == filled:
        return "DATE"
    if stats["timestamps"] == filled:
        return "TIMESTAMP"
    return "TEXT"


def inspect_csv(path: Path) -> Tuple[List[str], List[str], List[int], int, List[Dict[str, Any]]]:
    """
    Read a CSV completely with DuckDB.

    Returns (source column names, inferred types, null counts, row count,
    preview rows). Raises on any read problem so the caller can fall
    back to pandas.
    """
    from backend.ingestion import PREVIEW_ROWS

    connection = _connect()
    try:
        source = _source(path)
        names = [row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]
        if not names:
            raise ValueError("no columns")

        # Trim each value once; "e<i>" says the raw cell was empty (NULL).
        columns = [(name, f"v{index}", f"e{index}") for index, name in enumerate(names)]
        cleaned = "(SELECT " + ", ".join(
            f"trim({_ident(name)}, ' \t\r\n') AS {value}, ({_ident(name)} IS NULL OR {_ident(name)} = '') AS {empty}"
            for name, value, empty in columns
        ) + f" FROM {source}) AS cleaned"

        # Pass 1 (cheap checks): which types are still possible per column.
        parts = ["COUNT(*)"]
        for name, value, empty in columns:
            filled = f"({value} <> '')"
            parts += [
                f"COUNT(*) FILTER (WHERE {empty})",
                f"COUNT(*) FILTER (WHERE {filled})",
                f"COUNT(TRY_CAST({value} AS DOUBLE)) FILTER (WHERE {filled})",
                f"COUNT(*) FILTER (WHERE {filled} AND lower({value}) IN ('true', 'false'))",
                f"COUNT(*) FILTER (WHERE {filled} AND regexp_full_match({value}, {_literal(_TIMESTAMP)}))",
            ]
        row = connection.execute(f"SELECT {', '.join(parts)} FROM {cleaned}").fetchone()
        rows = int(row[0])
        first = {}
        for index, name in enumerate(names):
            nulls, filled, numbers, booleans, times = row[1 + index * 5: 6 + index * 5]
            first[name] = {"nulls": int(nulls), "filled": int(filled), "numbers": numbers,
                           "booleans": booleans, "time_shaped": times}

        # Pass 2 (exact rules), only for columns pass 1 kept possible.
        checks = []
        for name, value, _ in columns:
            stats = first[name]
            filled = f"({value} <> '')"
            if stats["filled"] and stats["numbers"] == stats["filled"]:
                checks += [
                    (name, "integers", f"COUNT(*) FILTER (WHERE {filled} AND regexp_full_match({value}, {_literal(_INTEGER)}))"),
                    (name, "bigints", f"COUNT(*) FILTER (WHERE {filled} AND regexp_full_match({value}, {_literal(_INTEGER)}) "
                                      f"AND TRY_CAST({value} AS BIGINT) IS NOT NULL)"),
                    (name, "leading_zero", f"COUNT(*) FILTER (WHERE {filled} AND regexp_full_match({value}, {_literal(_LEADING_ZERO)}))"),
                    (name, "floats", f"COUNT(*) FILTER (WHERE {filled} AND regexp_full_match({value}, {_literal(_FLOAT)}))"),
                ]
            if stats["filled"] and stats["time_shaped"] == stats["filled"]:
                checks += [
                    (name, "dates", f"COUNT(*) FILTER (WHERE {filled} AND regexp_full_match({value}, {_literal(_DATE)}) "
                                    f"AND try_strptime({value}, '%Y-%m-%d') IS NOT NULL)"),
                    (name, "timestamps", f"COUNT(TRY_CAST(replace({value}, 'T', ' ') AS TIMESTAMP)) FILTER (WHERE {filled})"),
                ]
        exact = {}
        if checks:
            values = connection.execute(f"SELECT {', '.join(sql for _, _, sql in checks)} FROM {cleaned}").fetchone()
            for (name, key, _), count in zip(checks, values):
                exact.setdefault(name, {})[key] = count

        types, nulls = [], []
        for name in names:
            stats = {"integers": -1, "bigints": -1, "leading_zero": 0, "floats": -1, "dates": -1, "timestamps": -1}
            stats.update(first[name])
            stats.update(exact.get(name, {}))
            types.append(_infer(stats))
            nulls.append(stats["nulls"])

        cursor = connection.execute(f"SELECT * FROM {source} LIMIT {int(PREVIEW_ROWS)}")
        preview = [
            {name: ("" if value is None else value) for name, value in zip(names, values)}
            for values in cursor.fetchall()
        ]
        return names, types, nulls, rows, preview
    finally:
        connection.close()


def copy_csv_to_postgres(connection, path: Path, qualified_table: str, column_names: List[str], expected_columns: int) -> int:
    """
    Stream a CSV into an existing PostgreSQL table: DuckDB writes a clean
    CSV (empty cells as NULL) to a temporary file, then COPY reads it.
    Returns the number of rows loaded.
    """
    duck = _connect()
    temp = tempfile.NamedTemporaryFile(prefix="insightx_", suffix=".csv", delete=False, dir=Path(path).parent)
    temp.close()
    try:
        source = _source(path)
        names = [row[0] for row in duck.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]
        if len(names) != expected_columns:
            from backend.ingestion import IngestionError

            raise IngestionError(
                f"{Path(path).name} changed since inspection "
                f"({len(names)} columns, expected {expected_columns}). Upload it again."
            )
        select = ", ".join(f"NULLIF({_ident(name)}, '')" for name in names)
        duck.execute(
            f"COPY (SELECT {select} FROM {source}) TO {_literal(Path(temp.name).as_posix())} "
            "(FORMAT csv, HEADER false, QUOTE '\"', ESCAPE '\"')"
        )
        columns_sql = ", ".join(_ident(name) for name in column_names)
        copy_sql = f"COPY {qualified_table} ({columns_sql}) FROM STDIN WITH (FORMAT csv, NULL '')"
        cursor = connection.connection.cursor()
        try:
            with open(temp.name, "r", encoding="utf-8", newline="") as handle:
                cursor.copy_expert(copy_sql, handle, size=1024 * 1024)
            return int(cursor.rowcount)
        finally:
            cursor.close()
    finally:
        duck.close()
        try:
            os.unlink(temp.name)
        except OSError:
            logger.warning("Could not remove temporary file %s", temp.name)
