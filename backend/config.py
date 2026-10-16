# backend/config.py
#
# Central runtime settings for InsightX.
#
# Every limit that protects the database or the user lives here so it
# can be tuned from .env without code changes. Values are validated at
# import time: a bad value fails fast at startup instead of silently
# disabling a guardrail at query time.

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Do not override variables already set in the process environment.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}.") from exc
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}.")
    return value


def _fraction(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}.") from exc
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1, got {value}.")
    return value


def _csv_tuple(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    values = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    if not values:
        raise ValueError(f"{name} must list at least one value.")
    return values


@dataclass(frozen=True)
class QuerySettings:
    # Maximum rows returned to the agent/UI for one query. One extra
    # row is fetched to detect truncation.
    max_result_rows: int
    # Rows of the result shown to the answer-generation LLM.
    max_answer_rows: int
    # Server-side limit for EXPLAIN and query execution.
    statement_timeout_ms: int
    # Never wait long on locks held by other sessions.
    lock_timeout_ms: int
    # Database schemas generated SQL may read from.
    allowed_schemas: tuple[str, ...]
    # Maximum tables chosen by schema retrieval (join-path connector
    # tables are added on top of these).
    retrieval_top_k: int
    # A table is kept when its relevance is at least this fraction of
    # the best table's relevance (0-1).
    retrieval_relative_cutoff: float
    # Tables wider than this are pruned to their relevant, key and
    # join columns before reaching the LLM.
    wide_table_columns: int
    # SQL candidates generated per question. Each is executed
    # (read-only) and the result most candidates agree on wins.
    # 1 disables candidate selection (one LLM call).
    sql_candidates: int
    # Sampling temperature of the extra candidates (the first
    # candidate always uses the configured default temperature).
    candidate_temperature: float
    # After the first candidate is ready, wait at most this long for
    # the extra candidates; late ones are ignored.
    candidate_wait_s: int
    # Alternative phrasings generated per question (in addition to the
    # primary rewrite) to widen schema retrieval.
    query_variants: int
    # 1: a simple question (one table, no ratios, nesting or windows)
    # gets one SQL candidate instead of sql_candidates, saving AI calls
    # where extra candidates almost never change the answer. 0: off.
    adaptive_candidates: int


query_settings = QuerySettings(
    max_result_rows=_positive_int("INSIGHTX_MAX_RESULT_ROWS", 1000),
    max_answer_rows=_positive_int("INSIGHTX_MAX_ANSWER_ROWS", 50),
    statement_timeout_ms=_positive_int("INSIGHTX_STATEMENT_TIMEOUT_MS", 30_000),
    lock_timeout_ms=_positive_int("INSIGHTX_LOCK_TIMEOUT_MS", 5_000),
    allowed_schemas=_csv_tuple("INSIGHTX_ALLOWED_SCHEMAS", "public"),
    retrieval_top_k=_positive_int("INSIGHTX_RETRIEVAL_TOP_K", 8),
    retrieval_relative_cutoff=_fraction("INSIGHTX_RETRIEVAL_RELATIVE_CUTOFF", 0.35),
    wide_table_columns=_positive_int("INSIGHTX_WIDE_TABLE_COLUMNS", 40),
    sql_candidates=_positive_int("INSIGHTX_SQL_CANDIDATES", 3),
    candidate_temperature=_fraction("INSIGHTX_CANDIDATE_TEMPERATURE", 0.7),
    candidate_wait_s=_positive_int("INSIGHTX_CANDIDATE_WAIT_S", 60),
    query_variants=_positive_int("INSIGHTX_QUERY_VARIANTS", 4),
    adaptive_candidates=1 if os.getenv("INSIGHTX_ADAPTIVE_CANDIDATES", "1").strip().lower() in ("1", "true", "yes") else 0,
)


@dataclass(frozen=True)
class IngestionSettings:
    # Where uploaded ZIPs are extracted (one folder per upload session).
    upload_dir: Path
    # Size of the uploaded ZIP file.
    max_upload_bytes: int
    # Total uncompressed size and file count allowed inside a ZIP
    # (protects against ZIP bombs).
    max_extracted_bytes: int
    max_zip_entries: int
    # Upload sessions (and their files) older than this are removed.
    upload_ttl_seconds: int
    # Rows per COPY batch during import.
    copy_batch_rows: int
    # Target schema for imported tables.
    target_schema: str


ingestion_settings = IngestionSettings(
    upload_dir=PROJECT_ROOT / "temp_uploads",
    max_upload_bytes=_positive_int("INSIGHTX_MAX_UPLOAD_MB", 1024) * 1024 * 1024,
    max_extracted_bytes=_positive_int("INSIGHTX_MAX_EXTRACTED_MB", 4096) * 1024 * 1024,
    max_zip_entries=_positive_int("INSIGHTX_MAX_ZIP_ENTRIES", 500),
    upload_ttl_seconds=_positive_int("INSIGHTX_UPLOAD_TTL_HOURS", 24) * 3600,
    copy_batch_rows=_positive_int("INSIGHTX_COPY_BATCH_ROWS", 100_000),
    target_schema=query_settings.allowed_schemas[0],
)


@dataclass(frozen=True)
class DatabaseSettings:
    # Fail fast on unreachable hosts instead of waiting for the OS TCP
    # timeout (~2 minutes).
    connect_timeout_s: int
    # Per-statement limit while profiling a database.
    profile_statement_timeout_ms: int
    # Above this row count the full-row duplicate scan is skipped.
    duplicate_scan_max_rows: int
    # Text columns with at most this many distinct values have their
    # values stored in the profile (value index for retrieval and SQL
    # filters). High-cardinality columns (names, emails, ids) are
    # never collected.
    value_index_max_distinct: int
    # Longer values are not indexed.
    value_index_max_length: int


database_settings = DatabaseSettings(
    connect_timeout_s=_positive_int("INSIGHTX_DB_CONNECT_TIMEOUT_S", 10),
    profile_statement_timeout_ms=_positive_int("INSIGHTX_PROFILE_STATEMENT_TIMEOUT_MS", 60_000),
    duplicate_scan_max_rows=_positive_int("INSIGHTX_DUPLICATE_SCAN_MAX_ROWS", 200_000),
    value_index_max_distinct=_positive_int("INSIGHTX_VALUE_INDEX_MAX_DISTINCT", 100),
    value_index_max_length=_positive_int("INSIGHTX_VALUE_INDEX_MAX_LENGTH", 80),
)
