import logging
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    MetaData,
    Table,
    inspect,
    select,
    func,
    distinct,
)
from sqlalchemy.exc import SQLAlchemyError

from backend import dialects

from backend.config import database_settings
from backend.text_to_sql.schema_context import filter_business_tables

logger = logging.getLogger("insightx.profiler")


@contextmanager
def profiling_connection(engine):
    """
    Connection for profiling queries.

    AUTOCOMMIT gives every statement its own transaction, so one failing
    statement (e.g. min() on a type without ordering) cannot abort the
    remaining profiling queries. A session statement_timeout bounds each
    query and is reset before the connection returns to the pool.
    """
    with engine.connect() as raw_connection:
        connection = raw_connection.execution_options(isolation_level="AUTOCOMMIT")
        dialects.set_timeout(connection, int(database_settings.profile_statement_timeout_ms))
        try:
            yield connection
        finally:
            try:
                dialects.reset_timeout(connection)
            except Exception:
                logger.warning("Could not reset statement_timeout on profiling connection.")


# =========================================================
# JSON SERIALIZATION HELPER
# =========================================================

def make_json_safe(value):
    """
    Convert database values into JSON-serializable values.
    """

    if value is None:
        return None

    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return value


# =========================================================
# COLUMN PROFILING
# =========================================================

def profile_column(connection, table, column_name, total_rows):
    """
    Collect generic statistics for one database column.
    """

    column = table.c[column_name]

    # Null count
    null_count = connection.execute(
        select(func.count())
        .select_from(table)
        .where(column.is_(None))
    ).scalar_one()

    non_null_count = total_rows - null_count

    # Distinct count. Types without an equality operator (json, ...)
    # cannot be counted; skip the statistic, not the table.
    try:
        distinct_count = connection.execute(
            select(func.count(distinct(column)))
            .select_from(table)
        ).scalar_one()
    except Exception as exc:
        logger.debug("distinct count skipped for %s.%s: %s", table.name, column_name, exc)
        distinct_count = None

    if total_rows > 0:
        null_percentage = round(
            (null_count / total_rows) * 100,
            2,
        )
    else:
        null_percentage = 0.0

    if non_null_count > 0 and distinct_count is not None:
        uniqueness_percentage = round(
            (distinct_count / non_null_count) * 100,
            2,
        )
    else:
        uniqueness_percentage = 0.0

    result = {
        "column_name": column_name,
        "data_type": str(table.c[column_name].type),
        "nullable": True,
        "total_rows": total_rows,
        "null_count": null_count,
        "null_percentage": null_percentage,
        "non_null_count": non_null_count,
        "distinct_count": distinct_count,
        "uniqueness_percentage": uniqueness_percentage,
    }

    # Value index: the stored values of low-cardinality text columns
    # (statuses, categories, countries, ...), most frequent first.
    # Lets retrieval map question words to the stored spelling
    # ('cancelled' -> 'canceled') before SQL is generated.
    if (
        is_text_column(column)
        and not _is_id_like(column_name)
        and not is_sensitive_column(column_name)
        and distinct_count
        and distinct_count <= database_settings.value_index_max_distinct
    ):
        result["top_values"] = collect_top_values(connection, table, column)

    # Try to calculate min/max.
    # Some data types may not support these operations.
    try:
        min_value, max_value = connection.execute(
            select(
                func.min(column),
                func.max(column),
            ).select_from(table)
        ).one()

        result["min_value"] = make_json_safe(min_value)
        result["max_value"] = make_json_safe(max_value)

    except Exception as exc:
        # Expected for types without ordering (json, arrays, ...).
        logger.debug("min/max skipped for %s.%s: %s", table.name, column_name, exc)
        result["min_value"] = None
        result["max_value"] = None

    return result


# Columns whose values are never collected for the value index (and so
# never sent to an LLM), even when they have few distinct values:
# contact details, personal names, secrets. Matched on name words.
_SENSITIVE_WORDS = {
    "phone", "fax", "mobile", "cell", "email", "mail", "address", "street",
    "postal", "zip", "postcode", "ssn", "password", "passwd", "pwd", "secret",
    "token", "apikey", "birth", "dob", "salary", "iban", "card", "contact",
    "ip", "passport", "license", "tax",
}
_SENSITIVE_PAIRS = {("first", "name"), ("last", "name"), ("full", "name"), ("middle", "name"), ("api", "key")}


def is_sensitive_column(column_name) -> bool:
    from backend.text_to_sql.retriever import split_words
    words = split_words(column_name)
    if any(word in _SENSITIVE_WORDS for word in words):
        return True
    return any(pair in _SENSITIVE_PAIRS for pair in zip(words, words[1:]))


def is_text_column(column) -> bool:
    try:
        return column.type.python_type is str
    except (NotImplementedError, AttributeError):
        return False


def collect_top_values(connection, table, column):
    limit = database_settings.value_index_max_distinct
    max_length = database_settings.value_index_max_length
    try:
        rows = connection.execute(
            select(column, func.count().label("n"))
            .select_from(table)
            .where(column.is_not(None))
            .group_by(column)
            .order_by(func.count().desc())
            .limit(limit)
        ).all()
    except Exception as exc:
        logger.debug("value collection skipped for %s.%s: %s", table.name, column.name, exc)
        return []
    values = []
    for value, _ in rows:
        text = str(value)
        if text.strip() and len(text) <= max_length:
            values.append(text)
    return values


# =========================================================
# INFERRED RELATIONSHIP DETECTION
# =========================================================

def declared_groups(actual_relationships):
    """Table -> group: tables joined by declared foreign keys share a group."""
    parent = {}

    def find(table):
        parent.setdefault(table, table)
        while parent[table] != table:
            parent[table] = parent[parent[table]]
            table = parent[table]
        return table

    for relationship in actual_relationships:
        if relationship.get("parent_table"):
            parent[find(relationship["child_table"])] = find(relationship["parent_table"])
    return {table: find(table) for table in list(parent)}


def inference_filter(actual_relationships, table_profiles=None):
    """
    Which guessed links are worth checking, given the declared foreign
    keys. A column that already has a foreign key needs no guess, and
    two tables that belong to different declared groups (e.g. separate
    apps sharing one database) are not linked just because both have a
    customer_id. A table's own single-column primary key identifies its
    rows; it is not guessed to point at another table. Tables with no
    foreign keys at all are always checked.
    """
    declared_columns = {(r["child_table"], r["child_column"]) for r in actual_relationships}
    groups = declared_groups(actual_relationships)
    own_keys = {
        (table, profile["primary_key"][0])
        for table, profile in (table_profiles or {}).items()
        if len(profile.get("primary_key") or []) == 1
    }

    def allowed(child_table, child_column, parent_table):
        if (child_table, child_column) in declared_columns or (child_table, child_column) in own_keys:
            return False
        child_group, parent_group = groups.get(child_table), groups.get(parent_table)
        return child_group is None or parent_group is None or child_group == parent_group

    return allowed


def infer_relationships(
    connection,
    metadata,
    table_profiles,
    allowed=None,
):
    """
    Infer possible relationships between tables.

    This does NOT depend on a specific dataset.

    Rules:
    1. Column names should match.
    2. Parent column should contain unique non-null values.
    3. Child values should mostly exist in the parent column.
    4. Relationship is marked as inferred, not guaranteed.
    """

    relationships = []

    table_names = list(table_profiles.keys())

    for child_table_name in table_names:

        child_profile = table_profiles[child_table_name]
        child_table = child_profile["_table_object"]

        for parent_table_name in table_names:

            if child_table_name == parent_table_name:
                continue

            parent_profile = table_profiles[parent_table_name]
            parent_table = parent_profile["_table_object"]

            child_columns = child_profile["columns"]
            parent_columns = parent_profile["columns"]

            for child_column_name, child_info in child_columns.items():

                # Ignore columns with no useful values
                if child_info["non_null_count"] == 0:
                    continue

                for parent_column_name, parent_info in parent_columns.items():

                    # Generic first-level matching:
                    # same column name
                    if child_column_name.lower() != parent_column_name.lower():
                        continue

                    if allowed and not allowed(child_table_name, child_column_name, parent_table_name):
                        continue

                    if _type_family(child_info["data_type"]) != _type_family(parent_info["data_type"]):
                        continue

                    # Parent should behave like a candidate key
                    parent_is_unique = (
                        parent_info["non_null_count"] > 0
                        and parent_info["distinct_count"]
                        == parent_info["non_null_count"]
                    )

                    if not parent_is_unique:
                        continue

                    child_column = child_table.c[child_column_name]
                    parent_column = parent_table.c[parent_column_name]

                    try:
                        child_distinct_non_null = connection.execute(
                            select(func.count(distinct(child_column)))
                            .select_from(child_table)
                            .where(child_column.is_not(None))
                        ).scalar_one()

                        if child_distinct_non_null == 0:
                            continue

                        matching_values = connection.execute(
                            select(func.count(distinct(child_column)))
                            .select_from(child_table)
                            .where(
                                child_column.is_not(None),
                                child_column.in_(
                                    select(parent_column).where(
                                        parent_column.is_not(None)
                                    )
                                ),
                            )
                        ).scalar_one()

                        overlap_percentage = round(
                            (
                                matching_values
                                / child_distinct_non_null
                            ) * 100,
                            2,
                        )

                        # Avoid weak matches
                        if overlap_percentage < 80:
                            continue

                        relationships.append(
                            {
                                "relationship_type": "inferred",
                                "child_table": child_table_name,
                                "child_column": child_column_name,
                                "parent_table": parent_table_name,
                                "parent_column": parent_column_name,
                                "overlap_percentage": overlap_percentage,
                                "confidence": (
                                    "high"
                                    if overlap_percentage >= 95
                                    else "medium"
                                ),
                            }
                        )

                    except Exception as exc:
                        logger.warning(
                            "Relationship check %s.%s -> %s.%s failed: %s",
                            child_table_name, child_column_name,
                            parent_table_name, parent_column_name,
                            str(exc).splitlines()[0],
                        )
                        continue

    relationships += infer_differently_named_relationships(
        connection,
        table_profiles,
        already_linked={(r["child_table"], r["child_column"]) for r in relationships},
        allowed=allowed,
    )
    return relationships


# Differently named join columns (orders.ship_via -> shippers.shipper_id,
# tickets.assignee -> users.id). Only checked when the child column's
# name shares a word with the parent table's name, so the number of
# database checks stays small; the data must then confirm it.
_NAME_MATCH_MIN_PREFIX = 4
_DIFFERENT_NAME_MIN_OVERLAP = 95.0


def _name_words(name):
    from backend.text_to_sql.retriever import split_words, stem
    return {stem(word) for word in split_words(name) if len(word) >= 3}


# Columns that measure something (rental_duration, order_count) are
# never references, however well their numbers happen to match ids.
_MEASURE_WORDS = {
    "duration", "length", "count", "qty", "quantity", "amount", "total", "price", "cost",
    "rate", "days", "hours", "minutes", "seconds", "weight", "size", "age", "score",
}


def _names_related(column_name, table_name) -> bool:
    from backend.text_to_sql.retriever import split_words
    if _MEASURE_WORDS & set(split_words(column_name)):
        return False
    column_words = _name_words(column_name) - {"id", "key", "code", "num", "number"}
    table_words = _name_words(table_name)
    for a in column_words:
        for b in table_words:
            if a == b:
                return True
            shorter, longer = sorted((a, b), key=len)
            if len(shorter) >= _NAME_MATCH_MIN_PREFIX and longer.startswith(shorter):
                return True
    return False


def _is_id_like(name) -> bool:
    from backend.text_to_sql.retriever import split_words
    words = split_words(name)
    return bool(words) and words[-1] in {"id", "key", "code", "no", "num", "number"}


def _type_family(data_type) -> str:
    lowered = str(data_type).lower()
    if any(word in lowered for word in ("int", "serial")):
        return "integer"
    if any(word in lowered for word in ("char", "text", "uuid")):
        return "text"
    return lowered


def _single_key_column(parent_profile):
    """The parent's single-column key: its primary key, else an id-like unique column."""
    columns = parent_profile["columns"]
    primary_key = parent_profile.get("primary_key") or []
    if len(primary_key) == 1 and primary_key[0] in columns:
        return primary_key[0]
    for name, info in columns.items():
        unique = info["non_null_count"] > 0 and info["distinct_count"] == info["non_null_count"]
        if unique and _is_id_like(name):
            return name
    return None


def infer_differently_named_relationships(connection, table_profiles, already_linked, allowed=None):
    relationships = []
    for parent_table_name, parent_profile in table_profiles.items():
        parent_column_name = _single_key_column(parent_profile)
        if parent_column_name is None:
            continue
        parent_info = parent_profile["columns"][parent_column_name]
        parent_column = parent_profile["_table_object"].c[parent_column_name]

        for child_table_name, child_profile in table_profiles.items():
            if child_table_name == parent_table_name:
                continue
            child_table = child_profile["_table_object"]
            for child_column_name, child_info in child_profile["columns"].items():
                if (child_table_name, child_column_name) in already_linked:
                    continue
                if allowed and not allowed(child_table_name, child_column_name, parent_table_name):
                    continue
                if child_column_name.lower() == parent_column_name.lower():
                    continue  # same-name pairs are handled above
                if child_info["non_null_count"] == 0:
                    continue
                if _type_family(child_info["data_type"]) != _type_family(parent_info["data_type"]):
                    continue
                if not _names_related(child_column_name, parent_table_name):
                    continue
                child_column = child_table.c[child_column_name]
                try:
                    child_distinct = connection.execute(
                        select(func.count(distinct(child_column)))
                        .select_from(child_table)
                        .where(child_column.is_not(None))
                    ).scalar_one()
                    if child_distinct < 2:
                        continue
                    matching = connection.execute(
                        select(func.count(distinct(child_column)))
                        .select_from(child_table)
                        .where(
                            child_column.is_not(None),
                            child_column.in_(select(parent_column).where(parent_column.is_not(None))),
                        )
                    ).scalar_one()
                except Exception as exc:
                    logger.warning(
                        "Relationship check %s.%s -> %s.%s failed: %s",
                        child_table_name, child_column_name, parent_table_name,
                        parent_column_name, str(exc).splitlines()[0],
                    )
                    continue
                overlap = round(matching / child_distinct * 100, 2)
                if overlap < _DIFFERENT_NAME_MIN_OVERLAP:
                    continue
                relationships.append({
                    "relationship_type": "inferred",
                    "child_table": child_table_name,
                    "child_column": child_column_name,
                    "parent_table": parent_table_name,
                    "parent_column": parent_column_name,
                    "overlap_percentage": overlap,
                    # Name and data agree, but the names differ: never "high".
                    "confidence": "medium",
                    "match": "name_similarity",
                })
    return relationships


# =========================================================
# MAIN SCHEMA PROFILER
# =========================================================

class _SkipDuplicateScan(Exception):
    """Internal: duplicate scan intentionally skipped for a large table."""


def get_database_schema(
    engine,
    schema_name=None,
):
    """
    Generic PostgreSQL schema profiler.

    Works with any PostgreSQL database.
    No dataset-specific table names or relationships.
    """

    if schema_name is None:
        schema_name = dialects.default_schema(engine)

    inspector = inspect(engine)

    # Exclude LangGraph checkpoint tables (left in public by
    # earlier versions) from the business schema.
    table_names = filter_business_tables(
        inspector,
        inspector.get_table_names(
            schema=schema_name
        ),
        schema=schema_name,
    )

    tables = []
    actual_relationships = []
    table_profiles = {}

    metadata = MetaData()

    with profiling_connection(engine) as connection:

        for table_name in table_names:

            try:
                # Reflect table safely using SQLAlchemy
                table = Table(
                    table_name,
                    metadata,
                    schema=schema_name,
                    autoload_with=engine,
                )

                total_rows = connection.execute(
                    select(func.count()).select_from(table)
                ).scalar_one()

                # -----------------------------------------
                # Columns
                # -----------------------------------------

                inspector_columns = inspector.get_columns(
                    table_name,
                    schema=schema_name,
                )

                columns = {}
                column_list = []

                for column_info in inspector_columns:

                    column_name = column_info["name"]

                    profile = profile_column(
                        connection=connection,
                        table=table,
                        column_name=column_name,
                        total_rows=total_rows,
                    )

                    profile["nullable"] = column_info.get(
                        "nullable",
                        True,
                    )
                    # The database's own documentation (COMMENT ON COLUMN).
                    profile["description"] = (column_info.get("comment") or "").strip()

                    columns[column_name] = profile
                    column_list.append(profile)

                # -----------------------------------------
                # Primary Key
                # -----------------------------------------

                primary_key_info = inspector.get_pk_constraint(
                    table_name,
                    schema=schema_name,
                )

                primary_key_columns = primary_key_info.get(
                    "constrained_columns",
                    [],
                )

                # -----------------------------------------
                # Foreign Keys
                # -----------------------------------------

                foreign_keys = inspector.get_foreign_keys(
                    table_name,
                    schema=schema_name,
                )

                formatted_foreign_keys = []

                for foreign_key in foreign_keys:

                    referred_table = foreign_key.get(
                        "referred_table"
                    )

                    referred_columns = foreign_key.get(
                        "referred_columns",
                        [],
                    )

                    constrained_columns = foreign_key.get(
                        "constrained_columns",
                        [],
                    )

                    formatted_foreign_keys.append(
                        {
                            "name": foreign_key.get("name"),
                            "columns": constrained_columns,
                            "referred_schema": foreign_key.get(
                                "referred_schema",
                                schema_name,
                            ),
                            "referred_table": referred_table,
                            "referred_columns": referred_columns,
                        }
                    )

                    if referred_table and constrained_columns:
                        for index, child_column in enumerate(
                            constrained_columns
                        ):

                            parent_column = (
                                referred_columns[index]
                                if index < len(referred_columns)
                                else None
                            )

                            actual_relationships.append(
                                {
                                    "relationship_type": "actual",
                                    "child_table": table_name,
                                    "child_column": child_column,
                                    "parent_table": referred_table,
                                    "parent_column": parent_column,
                                    "confidence": "confirmed",
                                }
                            )

                # -----------------------------------------
                # Unique Constraints
                # -----------------------------------------

                try:
                    unique_constraints = inspector.get_unique_constraints(
                        table_name,
                        schema=schema_name,
                    )
                except NotImplementedError:  # SQL Server: unique indexes cover these
                    unique_constraints = []

                formatted_unique_constraints = []

                for constraint in unique_constraints:
                    formatted_unique_constraints.append(
                        {
                            "name": constraint.get("name"),
                            "columns": constraint.get(
                                "column_names",
                                [],
                            ),
                        }
                    )

                # -----------------------------------------
                # Indexes
                # -----------------------------------------

                indexes = inspector.get_indexes(
                    table_name,
                    schema=schema_name,
                )

                formatted_indexes = []

                for index in indexes:
                    formatted_indexes.append(
                        {
                            "name": index.get("name"),
                            "columns": index.get(
                                "column_names",
                                [],
                            ),
                            "unique": index.get(
                                "unique",
                                False,
                            ),
                        }
                    )

                # -----------------------------------------
                # Sample Rows
                # -----------------------------------------

                sample_rows = connection.execute(
                    select(table).limit(5)
                ).mappings().all()

                formatted_sample_rows = []

                for row in sample_rows:
                    formatted_sample_rows.append(
                        {
                            key: make_json_safe(value)
                            for key, value in row.items()
                        }
                    )

                # -----------------------------------------
                # Duplicate Row Count
                # -----------------------------------------

                duplicate_row_count = None
                duplicate_check = "completed"

                if total_rows > database_settings.duplicate_scan_max_rows:
                    duplicate_check = (
                        f"skipped: {total_rows} rows exceeds "
                        f"{database_settings.duplicate_scan_max_rows}"
                    )

                try:
                    if duplicate_check != "completed":
                        raise _SkipDuplicateScan()

                    all_columns = [
                        table.c[column_info["name"]]
                        for column_info in inspector_columns
                    ]

                    if all_columns:
                        grouped_query = (
                            select(
                                *all_columns,
                                func.count().label(
                                    "_duplicate_count"
                                ),
                            )
                            .select_from(table)
                            .group_by(*all_columns)
                            .having(
                                func.count() > 1
                            )
                        )

                        duplicate_groups = connection.execute(
                            select(
                                func.count()
                            ).select_from(
                                grouped_query.subquery()
                            )
                        ).scalar_one()

                        duplicate_row_count = duplicate_groups

                except _SkipDuplicateScan:
                    duplicate_row_count = None

                except Exception as exc:
                    duplicate_row_count = None
                    duplicate_check = f"failed: {str(exc).splitlines()[0]}"
                    logger.warning("Duplicate scan failed for %s: %s", table_name, duplicate_check)

                try:
                    table_comment = (
                        inspector.get_table_comment(table_name, schema=schema_name).get("text") or ""
                    ).strip()
                except (NotImplementedError, SQLAlchemyError):
                    table_comment = ""

                table_result = {
                    "schema_name": schema_name,
                    "table_name": table_name,
                    "description": table_comment,
                    "row_count": total_rows,
                    "column_count": len(column_list),
                    "primary_key": primary_key_columns,
                    "foreign_keys": formatted_foreign_keys,
                    "unique_constraints": formatted_unique_constraints,
                    "indexes": formatted_indexes,
                    "duplicate_groups": duplicate_row_count,
                    "duplicate_check": duplicate_check,
                    "columns": column_list,
                    "sample_rows": formatted_sample_rows,
                }

                tables.append(table_result)

                table_profiles[table_name] = {
                    "columns": columns,
                    "primary_key": primary_key_columns,
                    "_table_object": table,
                }

            except SQLAlchemyError as error:
                logger.warning("Profiling table %s failed: %s", table_name, str(error).splitlines()[0])
                tables.append(
                    {
                        "schema_name": schema_name,
                        "table_name": table_name,
                        "error": str(error),
                    }
                )

        # ---------------------------------------------
        # Infer Relationships
        # ---------------------------------------------

        inferred_relationships = infer_relationships(
            connection=connection,
            metadata=metadata,
            table_profiles=table_profiles,
            allowed=inference_filter(actual_relationships, table_profiles),
        )

    # Remove internal SQLAlchemy table objects
    # before returning the response.
    for profile in table_profiles.values():
        profile.pop("_table_object", None)

    # Avoid duplicate relationship entries
    all_relationships = actual_relationships.copy()

    existing_relationship_keys = {
        (
            relationship["child_table"],
            relationship["child_column"],
            relationship["parent_table"],
            relationship["parent_column"],
        )
        for relationship in actual_relationships
    }

    for relationship in inferred_relationships:

        relationship_key = (
            relationship["child_table"],
            relationship["child_column"],
            relationship["parent_table"],
            relationship["parent_column"],
        )

        if relationship_key not in existing_relationship_keys:
            all_relationships.append(relationship)

    return {
        "schema_name": schema_name,
        "total_tables": len(tables),
        "relationships": all_relationships,
        "tables": tables,
    }