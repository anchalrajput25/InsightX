# backend/text_to_sql/schema_context.py
#
# Schema context for the Text-to-SQL agent:
#
# 1. Converts schema_profiler output (including inferred
#    relationships) into the format used by retriever.py,
#    graph_rag.py and schema_linker.py.
# 2. Caches that schema per database so /query does not
#    re-inspect the database on every turn.
# 3. Identifies LangGraph checkpoint tables so they never
#    appear in the business schema.

import copy
import threading
from typing import Any, Dict, Iterable, List

from sqlalchemy.engine import Engine


# ============================================================
# INTERNAL (CHECKPOINT) TABLES
# ============================================================

# Tables created by LangGraph's PostgresSaver, with columns that
# identify them. A table is internal only if both the name and the
# columns match, so a user's own table named e.g. "checkpoints"
# is not hidden.
INTERNAL_TABLE_SIGNATURES = {
    "checkpoints": {"thread_id", "checkpoint_ns", "checkpoint_id", "checkpoint"},
    "checkpoint_blobs": {"thread_id", "checkpoint_ns", "channel", "version", "blob"},
    "checkpoint_writes": {"thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "channel"},
    "checkpoint_migrations": {"v"},
}


def is_internal_table(table_name: str, column_names: Iterable[str]) -> bool:
    required = INTERNAL_TABLE_SIGNATURES.get(table_name)
    if required is None:
        return False
    return required.issubset({str(name) for name in column_names})


def filter_business_tables(
    inspector,
    table_names: Iterable[str],
    schema: str | None = None,
) -> List[str]:
    """Drop LangGraph checkpoint tables from a list of table names."""

    business_tables = []

    for table_name in table_names:
        if table_name in INTERNAL_TABLE_SIGNATURES:
            columns = [
                column["name"]
                for column in inspector.get_columns(table_name, schema=schema)
            ]
            if is_internal_table(table_name, columns):
                continue
        business_tables.append(table_name)

    return business_tables


# ============================================================
# PROFILER -> AGENT SCHEMA
# ============================================================

def _convert_relationships(profile_relationships: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Map schema_profiler relationships (child_table/parent_table)
    to the graph_rag.py format (source_table/target_table).

    Actual foreign keys come first; an inferred relationship is
    dropped when the same column pair is already present.
    """

    ordered = sorted(
        profile_relationships or [],
        key=lambda rel: 0 if rel.get("relationship_type") == "actual" else 1,
    )

    relationships = []
    seen = set()

    for rel in ordered:
        source_table = rel.get("child_table")
        source_column = rel.get("child_column")
        target_table = rel.get("parent_table")
        target_column = rel.get("parent_column")

        if not (source_table and source_column and target_table and target_column):
            continue

        pair = frozenset({(source_table, source_column), (target_table, target_column)})
        if pair in seen:
            continue
        seen.add(pair)

        is_actual = rel.get("relationship_type") == "actual"
        relationship = {
            "source_table": source_table,
            "source_column": source_column,
            "target_table": target_table,
            "target_column": target_column,
            "type": "foreign_key" if is_actual else "inferred",
            "confidence": rel.get("confidence", "confirmed" if is_actual else "unknown"),
        }
        if rel.get("overlap_percentage") is not None:
            relationship["overlap_percentage"] = rel["overlap_percentage"]

        relationships.append(relationship)

    return relationships


def build_agent_schema(profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert schema_profiler.get_database_schema() output into the
    agent schema format:

        {"tables": [{table_name, columns: [{column_name, data_type,
          description, nullable}], primary_key, foreign_keys}],
         "relationships": [{source_table, source_column,
          target_table, target_column, type, confidence}]}
    """

    tables = []

    for table in profile.get("tables", []):
        if table.get("error"):
            continue

        columns = []
        for column in table.get("columns", []):
            entry = {
                "column_name": column.get("column_name", ""),
                "data_type": str(column.get("data_type", "")),
                "description": column.get("description") or "",
                "nullable": bool(column.get("nullable", True)),
            }
            # Cardinality: used to pick breakdown dimensions (investigation).
            if isinstance(column.get("distinct_count"), int):
                entry["distinct_count"] = column["distinct_count"]
            # Unique (key-like) columns: used to detect join fan-out.
            non_null = column.get("non_null_count") or 0
            if non_null > 0 and column.get("distinct_count") == non_null:
                entry["unique"] = True
            # Stored values of low-cardinality text columns (value index).
            if column.get("top_values"):
                entry["values"] = [str(value) for value in column["top_values"]]
            columns.append(entry)

        tables.append(
            {
                "table_name": table.get("table_name", ""),
                "description": table.get("description") or "",
                "columns": columns,
                "primary_key": table.get("primary_key", []) or [],
                "foreign_keys": table.get("foreign_keys", []) or [],
            }
        )

    return {
        "tables": tables,
        "relationships": _convert_relationships(profile.get("relationships", [])),
    }


# ============================================================
# SCHEMA CACHE
# ============================================================
#
# Keyed by database identity (driver, user, host, port, database),
# not by connection_id, so an import into a database invalidates
# the schema for every session connected to it.

_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()


def _cache_key(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=True)


def cache_schema_from_profile(engine: Engine, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a profiler result and store it as the agent schema."""
    schema = build_agent_schema(profile)
    with _CACHE_LOCK:
        _SCHEMA_CACHE[_cache_key(engine)] = schema
    return copy.deepcopy(schema)


def invalidate_schema_cache(engine: Engine) -> None:
    with _CACHE_LOCK:
        _SCHEMA_CACHE.pop(_cache_key(engine), None)


def get_agent_schema(engine: Engine) -> Dict[str, Any]:
    """
    Return the cached agent schema, profiling the database on a
    cache miss (e.g. an engine that did not go through
    /connect-postgres or /import-uploaded-files).
    """
    key = _cache_key(engine)

    with _CACHE_LOCK:
        cached = _SCHEMA_CACHE.get(key)

    if cached is not None:
        return copy.deepcopy(cached)

    # Imported here: schema_profiler imports this module.
    from backend.schema_profiler import get_database_schema

    return cache_schema_from_profile(engine, get_database_schema(engine))
