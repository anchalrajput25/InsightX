# backend/ingestion.py
#
# File ingestion: ZIP extraction -> file discovery -> inspection ->
# (user approval in the UI) -> atomic import into PostgreSQL.
#
# Generic for arbitrary CSV/Excel files; no dataset-specific names.
#
# Principles:
#   - Inspection reads every file completely, so malformed files are
#     reported before the user approves the import.
#   - Types are inferred deterministically from the text values. Values
#     are never rewritten: codes with leading zeros stay text, and only
#     unambiguous ISO dates become timestamps.
#   - Table/column names are made valid, unique PostgreSQL identifiers
#     at inspection time, so the user sees the exact names that will be
#     created.
#   - Import is all-or-nothing: files load into a staging schema and are
#     moved into the target schema in one transaction.

import io
import logging
import re
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from backend import fast_ingest
from backend.config import ingestion_settings

logger = logging.getLogger("insightx.ingestion")

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}

# PostgreSQL silently truncates longer identifiers, which can make two
# different names collide; names are shortened explicitly instead.
MAX_IDENTIFIER_LENGTH = 63

CSV_ENCODINGS = ("utf-8-sig", "cp1252")
PREVIEW_ROWS = 5


class IngestionError(Exception):
    """A problem with the uploaded data that the user must fix."""


class UploadTooLargeError(IngestionError):
    """The uploaded file exceeds the configured size limit."""


# ============================================================
# UPLOAD NAMES
# ============================================================

def safe_upload_name(filename: Optional[str], default: str = "upload.zip") -> str:
    """
    Reduce a client-supplied filename to a safe basename: no directory
    parts, only conservative characters. Prevents writing outside the
    session directory (e.g. "../../evil.zip").
    """
    name = PurePosixPath(str(filename or "").replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", name).strip(" .")
    return name or default


def save_upload(
    source,
    destination: Path,
    max_bytes: Optional[int] = None,
    chunk_size: int = 1024 * 1024,
) -> int:
    """
    Stream an uploaded file to disk, stopping as soon as it exceeds
    max_bytes. Returns the number of bytes written.
    """
    if max_bytes is None:
        max_bytes = ingestion_settings.max_upload_bytes
    written = 0
    with open(destination, "wb") as output:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise UploadTooLargeError(
                    f"The upload exceeds the {max_bytes / 1024 ** 2:.0f} MB limit."
                )
            output.write(chunk)
    return written


# ============================================================
# ZIP EXTRACTION
# ============================================================

def _is_ignored_member(path: PurePosixPath) -> bool:
    """macOS resource forks, hidden files and folders are not data."""
    return any(part.startswith(".") or part == "__MACOSX" for part in path.parts)


def extract_zip(
    zip_path: Path,
    extract_directory: Path,
    max_entries: Optional[int] = None,
    max_extracted_bytes: Optional[int] = None,
) -> Path:
    """
    Extract a ZIP after checking it: valid archive, bounded number of
    entries and total uncompressed size, and no member paths that
    escape the extraction directory.
    """
    if max_entries is None:
        max_entries = ingestion_settings.max_zip_entries
    if max_extracted_bytes is None:
        max_extracted_bytes = ingestion_settings.max_extracted_bytes

    extract_path = Path(extract_directory)
    extract_path.mkdir(parents=True, exist_ok=True)
    root = extract_path.resolve()

    try:
        archive = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile as exc:
        raise IngestionError("The uploaded file is not a valid ZIP archive.") from exc

    with archive:
        members = [info for info in archive.infolist() if not info.is_dir()]

        if len(members) > max_entries:
            raise IngestionError(
                f"The ZIP contains {len(members)} files; the limit is {max_entries}."
            )

        total_size = sum(info.file_size for info in members)
        if total_size > max_extracted_bytes:
            raise IngestionError(
                f"The ZIP expands to {total_size / 1024 ** 2:.0f} MB; "
                f"the limit is {max_extracted_bytes / 1024 ** 2:.0f} MB."
            )

        for info in members:
            member = PurePosixPath(info.filename.replace("\\", "/"))
            if _is_ignored_member(member):
                continue
            if member.is_absolute() or ".." in member.parts:
                raise IngestionError(f"Unsafe path inside ZIP: {info.filename}")
            target = (root / Path(*member.parts)).resolve()
            if root not in target.parents:
                raise IngestionError(f"Unsafe path inside ZIP: {info.filename}")
            archive.extract(info, root)

    return extract_path


def find_data_files(directory: Path) -> List[Path]:
    """Supported data files, in a stable order."""
    directory = Path(directory)
    return sorted(
        (
            path
            for path in directory.rglob("*")
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and not _is_ignored_member(PurePosixPath(path.relative_to(directory).as_posix()))
        ),
        key=lambda path: path.relative_to(directory).as_posix().lower(),
    )


# ============================================================
# IDENTIFIERS
# ============================================================

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def normalize_identifier(name: str, fallback: str) -> str:
    """
    Lower-case snake_case identifier valid in PostgreSQL without
    quoting: [a-z_][a-z0-9_]*, at most 63 characters.

    CamelCase word boundaries are kept ("OrderDate" -> "order_date",
    "CustomerID" -> "customer_id") so names stay readable for people,
    keyword retrieval and the LLM.
    """
    name = _CAMEL_BOUNDARY_RE.sub("_", str(name).strip())
    identifier = re.sub(r"[^a-z0-9_]+", "_", name.lower())
    identifier = re.sub(r"_+", "_", identifier).strip("_")
    if not identifier:
        identifier = fallback
    if identifier[0].isdigit():
        identifier = f"{fallback.split('_')[0]}_{identifier}"
    return identifier[:MAX_IDENTIFIER_LENGTH].rstrip("_") or fallback


def make_unique(names: Iterable[str], taken: Iterable[str] = ()) -> List[str]:
    """Append _2, _3, ... to repeated names, staying within 63 chars."""
    used = {name for name in taken}
    unique = []
    for name in names:
        candidate, counter = name, 2
        while candidate in used:
            suffix = f"_{counter}"
            candidate = name[: MAX_IDENTIFIER_LENGTH - len(suffix)] + suffix
            counter += 1
        used.add(candidate)
        unique.append(candidate)
    return unique


def sanitize_table_name(filename: str) -> str:
    """Kept for compatibility: identifier for a file's table."""
    return normalize_identifier(Path(filename).stem, "table")


# ============================================================
# TYPE INFERENCE
# ============================================================

_INTEGER_RE = re.compile(r"^[+-]?\d+$")
_LEADING_ZERO_RE = re.compile(r"^[+-]?0\d")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
_BOOLEAN_VALUES = {"true", "false"}
# Only unambiguous ISO-8601 forms; formats like 03/04/2020 stay text
# because day/month order cannot be known.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Time part optional: real exports often mix date-only and date-time
# values in one column; PostgreSQL reads both as TIMESTAMP.
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)?$")

INT64_MIN, INT64_MAX = -(2 ** 63), 2 ** 63 - 1


def infer_sql_type(values: pd.Series) -> str:
    """
    PostgreSQL type for a column of text values (NaN = NULL).

    The first rule that matches every non-null value wins:
      BIGINT, DOUBLE PRECISION, BOOLEAN, DATE, TIMESTAMP, else TEXT.
    Integers with leading zeros (zip codes, IDs) stay TEXT.
    """
    non_null = values.dropna().astype(str).str.strip()
    non_null = non_null[non_null != ""]
    if non_null.empty:
        return "TEXT"

    if non_null.str.match(_INTEGER_RE).all() and not non_null.str.match(_LEADING_ZERO_RE).any():
        numbers = pd.to_numeric(non_null, errors="coerce")
        # Values beyond int64 come back as float/NaN or exceed bounds.
        if numbers.notna().all() and str(numbers.dtype).startswith("int"):
            if numbers.min() >= INT64_MIN and numbers.max() <= INT64_MAX:
                return "BIGINT"
        return "NUMERIC"

    if non_null.str.match(_FLOAT_RE).all():
        integer_parts = non_null.str.replace(r"^[+-]", "", regex=True)
        if not integer_parts.str.match(r"^0\d").any():
            return "DOUBLE PRECISION"
        return "TEXT"

    if non_null.str.lower().isin(_BOOLEAN_VALUES).all():
        return "BOOLEAN"

    if non_null.str.match(_DATE_RE).all() and pd.to_datetime(non_null, format="%Y-%m-%d", errors="coerce").notna().all():
        return "DATE"

    if non_null.str.match(_TIMESTAMP_RE).all() and pd.to_datetime(non_null, format="ISO8601", errors="coerce").notna().all():
        return "TIMESTAMP"

    return "TEXT"


# ============================================================
# READING FILES
# ============================================================

def read_data_file(path: Path) -> Tuple[pd.DataFrame, List[str]]:
    """
    Read a whole CSV/Excel file as text. Only empty cells become NULL
    ("NA", "null", "None" are kept as values). Returns the frame and
    warnings about how it was read.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    warnings: List[str] = []

    if suffix == ".csv":
        last_error: Optional[Exception] = None
        for encoding in CSV_ENCODINGS:
            try:
                frame = pd.read_csv(
                    path,
                    dtype=str,
                    keep_default_na=False,
                    na_values=[""],
                    encoding=encoding,
                )
                if encoding != CSV_ENCODINGS[0]:
                    warnings.append(f"File is not UTF-8; read as {encoding}.")
                return frame, warnings
            except UnicodeDecodeError as exc:
                last_error = exc
            except pd.errors.EmptyDataError as exc:
                raise IngestionError("The file is empty (no header row).") from exc
            except pd.errors.ParserError as exc:
                raise IngestionError(f"The CSV is malformed: {exc}") from exc
        raise IngestionError(f"Could not decode the file: {last_error}")

    if suffix in (".xlsx", ".xls"):
        try:
            workbook = pd.ExcelFile(path)
        except ImportError as exc:
            raise IngestionError(
                f"Reading {suffix} files needs an extra package: {exc}"
            ) from exc
        except Exception as exc:
            raise IngestionError(f"The Excel file could not be opened: {exc}") from exc

        with workbook:
            sheets = workbook.sheet_names
            if not sheets:
                raise IngestionError("The Excel file has no sheets.")
            if len(sheets) > 1:
                warnings.append(
                    f"Only the first sheet ('{sheets[0]}') is imported; "
                    f"{len(sheets) - 1} other sheet(s) ignored."
                )
            frame = workbook.parse(
                sheets[0], dtype=str, keep_default_na=False, na_values=[""]
            )
        if frame.columns.empty:
            raise IngestionError("The first sheet is empty (no header row).")
        return frame, warnings

    raise IngestionError(f"Unsupported file type: {suffix}")


# ============================================================
# INSPECTION
# ============================================================

@dataclass
class ColumnSpec:
    source_name: str
    column_name: str
    data_type: str
    null_count: int


@dataclass
class FileInspection:
    file_name: str
    relative_path: str
    file_type: str
    table_name: str
    importable: bool
    rows: int = 0
    columns: int = 0
    column_names: List[str] = field(default_factory=list)
    column_types: List[Dict[str, Any]] = field(default_factory=list)
    preview: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    # "duckdb" for big CSV files read by backend.fast_ingest, else "pandas".
    reader: str = "pandas"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _column_specs(frame: pd.DataFrame) -> List[ColumnSpec]:
    return _specs_from(
        [str(column) for column in frame.columns],
        [infer_sql_type(frame.iloc[:, index]) for index in range(len(frame.columns))],
        [int(frame.iloc[:, index].isna().sum()) for index in range(len(frame.columns))],
    )


def _specs_from(source_names: List[str], types: List[str], nulls: List[int]) -> List[ColumnSpec]:
    normalized = [
        normalize_identifier(name, f"column_{index + 1}")
        for index, name in enumerate(source_names)
    ]
    unique_names = make_unique(normalized)
    return [
        ColumnSpec(source_name=source, column_name=name, data_type=data_type, null_count=null_count)
        for source, name, data_type, null_count in zip(source_names, unique_names, types, nulls)
    ]


def _finish(inspection: FileInspection, specs: List[ColumnSpec], rows: int, preview, warnings: List[str]) -> Dict[str, Any]:
    renamed = [spec for spec in specs if spec.source_name != spec.column_name]
    if renamed:
        warnings.append(
            f"{len(renamed)} column name(s) will be normalized, e.g. "
            f"'{renamed[0].source_name}' -> '{renamed[0].column_name}'."
        )
    if rows == 0:
        warnings.append("The file has a header but no data rows.")

    inspection.importable = True
    inspection.rows = int(rows)
    inspection.columns = len(specs)
    inspection.column_names = [spec.source_name for spec in specs]
    inspection.column_types = [asdict(spec) for spec in specs]
    inspection.preview = preview
    inspection.warnings = warnings
    return inspection.to_dict()


def inspect_file(file_path: Path, table_name: str, base_directory: Optional[Path] = None) -> Dict[str, Any]:
    """
    Read and validate one file completely. Never raises for bad data:
    problems are reported with importable=False and an error message.
    """
    file_path = Path(file_path)
    relative = (
        file_path.relative_to(base_directory).as_posix()
        if base_directory is not None
        else file_path.name
    )
    inspection = FileInspection(
        file_name=file_path.name,
        relative_path=relative,
        file_type=file_path.suffix.lower().lstrip("."),
        table_name=table_name,
        importable=False,
    )

    if fast_ingest.wants(file_path):
        try:
            names, types, nulls, rows, preview = fast_ingest.inspect_csv(file_path)
            inspection.reader = "duckdb"
            return _finish(inspection, _specs_from(names, types, nulls), rows, preview, [])
        except Exception as exc:
            # Unusual encoding or malformed rows: the pandas path below
            # reads it the old way and reports any real problem.
            print(f"[InsightX] DuckDB could not read {relative} ({exc}); using the normal reader.")

    try:
        frame, warnings = read_data_file(file_path)
    except IngestionError as exc:
        inspection.error = str(exc)
        return inspection.to_dict()
    except Exception as exc:  # unexpected reader failure, still reported
        logger.exception("Inspection of %s failed", relative)
        inspection.error = f"{type(exc).__name__}: {exc}"
        return inspection.to_dict()

    if fast_ingest.is_big_csv(file_path) and not fast_ingest.available():
        warnings.append("Big file: run 'pip install duckdb' and restart the backend to load it 2-3x faster.")
    preview = frame.head(PREVIEW_ROWS).fillna("").to_dict(orient="records")
    return _finish(inspection, _column_specs(frame), len(frame), preview, warnings)


def inspect_files(data_files: List[Path], base_directory: Path) -> List[Dict[str, Any]]:
    """Inspect all files and assign each a unique, valid table name."""
    table_names = make_unique(
        normalize_identifier(Path(path).stem, "table") for path in data_files
    )
    return [
        inspect_file(path, table_name, base_directory)
        for path, table_name in zip(data_files, table_names)
    ]


# ============================================================
# IMPORT
# ============================================================

def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _copy_frame(connection, qualified_table: str, column_names: List[str], frame: pd.DataFrame, batch_rows: int) -> None:
    """Bulk-load a frame with COPY in bounded batches."""
    columns_sql = ", ".join(_quote(name) for name in column_names)
    copy_sql = f"COPY {qualified_table} ({columns_sql}) FROM STDIN WITH (FORMAT csv, NULL '')"
    cursor = connection.connection.cursor()
    try:
        for start in range(0, len(frame), batch_rows):
            buffer = io.StringIO()
            # NaN -> unquoted empty field -> NULL. Real empty strings do
            # not exist: read_data_file turns empty cells into NaN.
            frame.iloc[start:start + batch_rows].to_csv(buffer, index=False, header=False)
            buffer.seek(0)
            cursor.copy_expert(copy_sql, buffer)
    finally:
        cursor.close()


def _existing_tables(connection, schema: str, names: List[str]) -> List[str]:
    rows = connection.execute(
        text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = :schema AND tablename = ANY(:names)"
        ),
        {"schema": schema, "names": names},
    )
    return sorted(row[0] for row in rows)


def import_files_to_postgres(
    file_specs: List[Dict[str, Any]],
    engine: Engine,
    base_directory: Path,
    target_schema: Optional[str] = None,
    batch_rows: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Import inspected, importable files atomically.

    All tables are created and loaded in a private staging schema.
    Only when every file loaded successfully are they moved into the
    target schema in a single transaction (replacing same-named
    tables). On any failure the target schema is untouched.

    Returns imported tables, replaced tables and skipped files.
    """
    target_schema = target_schema or ingestion_settings.target_schema
    batch_rows = batch_rows or ingestion_settings.copy_batch_rows

    importable = [spec for spec in file_specs if spec.get("importable")]
    skipped = [
        {"file_name": spec["file_name"], "reason": spec.get("error") or "Not importable."}
        for spec in file_specs
        if not spec.get("importable")
    ]
    if not importable:
        raise IngestionError("None of the uploaded files can be imported.")

    staging_schema = f"insightx_staging_{uuid.uuid4().hex[:12]}"
    imported: List[Dict[str, Any]] = []

    try:
        with engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {_quote(staging_schema)}"))

        for spec in importable:
            path = Path(base_directory) / spec["relative_path"]
            columns = spec["column_types"]
            fast = spec.get("reader") == "duckdb" and fast_ingest.available()
            frame = None if fast else read_data_file(path)[0]

            if frame is not None and len(frame.columns) != len(columns):
                raise IngestionError(
                    f"{spec['file_name']} changed since inspection "
                    f"({len(frame.columns)} columns, expected {len(columns)}). Upload it again."
                )

            table = spec["table_name"]
            qualified = f"{_quote(staging_schema)}.{_quote(table)}"
            column_names = [column["column_name"] for column in columns]
            column_ddl = ", ".join(
                f"{_quote(column['column_name'])} {column['data_type']}" for column in columns
            )

            # print, not logger.info: uvicorn does not show INFO logs of
            # this logger, and the user should see which loader ran.
            print(f"[InsightX] Loading {spec['file_name']} into {table} with {'DuckDB (fast)' if fast else 'pandas'}...")
            started = time.perf_counter()
            try:
                with engine.begin() as connection:
                    connection.execute(text(f"CREATE TABLE {qualified} ({column_ddl})"))
                    if fast:
                        loaded = fast_ingest.copy_csv_to_postgres(connection, path, qualified, column_names, len(columns))
                    else:
                        _copy_frame(connection, qualified, column_names, frame, batch_rows)
                        loaded = len(frame)
                    for column in columns:
                        if column["source_name"] != column["column_name"]:
                            connection.execute(
                                text(f"COMMENT ON COLUMN {qualified}.{_quote(column['column_name'])} IS :comment"),
                                {"comment": f"Source column: {column['source_name']}"},
                            )
            except IngestionError:
                raise
            except Exception as exc:
                message = str(getattr(exc, "orig", None) or exc).strip()
                raise IngestionError(f"Loading {spec['file_name']} failed: {message}") from exc

            print(f"[InsightX] Loaded {int(loaded):,} rows into {table} in {time.perf_counter() - started:.1f}s")
            imported.append({
                "table_name": table,
                "source_file": spec["file_name"],
                "rows": int(loaded),
                "columns": len(columns),
            })

        # Atomic swap into the target schema.
        table_names = [table["table_name"] for table in imported]
        with engine.begin() as connection:
            replaced = _existing_tables(connection, target_schema, table_names)
            for table in replaced:
                # No CASCADE: if views or foreign keys depend on the
                # table, the import fails instead of silently dropping them.
                connection.execute(text(f"DROP TABLE {_quote(target_schema)}.{_quote(table)}"))
            for table in table_names:
                connection.execute(
                    text(f"ALTER TABLE {_quote(staging_schema)}.{_quote(table)} SET SCHEMA {_quote(target_schema)}")
                )

        # Fresh statistics so PostgreSQL plans the first questions well
        # (row counts, value spread) instead of guessing.
        for table in table_names:
            try:
                with engine.begin() as connection:
                    connection.execute(text(f"ANALYZE {_quote(target_schema)}.{_quote(table)}"))
            except Exception:
                logger.warning("ANALYZE of %s failed", table)

    except IngestionError:
        raise
    except Exception as exc:
        message = str(getattr(exc, "orig", None) or exc).strip()
        raise IngestionError(f"Import failed; no tables were changed. {message}") from exc
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(text(f"DROP SCHEMA IF EXISTS {_quote(staging_schema)} CASCADE"))
        except Exception:
            logger.exception("Could not drop staging schema %s", staging_schema)

    return {
        "imported_tables": imported,
        "replaced_tables": replaced,
        "skipped_files": skipped,
    }
