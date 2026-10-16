# backend/text_to_sql/value_grounding.py
#
# Deterministic check that literal filter values in generated SQL
# actually occur in the data.
#
# Why: an LLM may write WHERE city = 'São Paulo' when the column stores
# 'sao paulo'. The SQL is valid, executes, and silently returns 0 rows,
# which is then reported as a confident (wrong) answer. This module
# catches such filters before execution and proposes the stored values
# that were most likely meant, so the correction step can fix the SQL.
#
# Only equality / IN filters on text columns of physical tables are
# checked. A value with no similar stored value is treated as a
# legitimate filter (the answer will be "no matching records").

import difflib
import logging
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlglot import exp

from backend import dialects
from backend.config import query_settings
from backend.text_to_sql.query_executor import read_only_connection

logger = logging.getLogger("insightx.value_grounding")

TEXT_TYPES = {
    "text", "character varying", "character", "citext",
    # MySQL
    "varchar", "char", "tinytext", "mediumtext", "longtext", "enum",
    # SQL Server, SQLite
    "nvarchar", "nchar", "ntext", "clob", "string",
}

# Bounds so the check stays cheap on large databases.
MAX_FILTERS_CHECKED = 10
MAX_SUGGESTIONS = 3
FUZZY_SAMPLE_VALUES = 5000

# Accent folding done inside PostgreSQL without extensions.
_ACCENTED = "áàâãäåéèêëíìîïóòôõöúùûüçñýÿÁÀÂÃÄÅÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑÝ"
_PLAIN = "aaaaaaeeeeiiiiooooouuuucnyyaaaaaaeeeeiiiiooooouuuucny"


@dataclass
class ValueIssue:
    table: str
    column: str
    value: str
    suggestions: List[str] = field(default_factory=list)

    def message(self) -> str:
        options = ", ".join(f"'{value}'" for value in self.suggestions)
        return (
            f"Filter {self.column} = '{self.value}' matches no rows: that value does not "
            f"occur in {self.table}.{self.column}. Stored values that look intended: "
            f"{options}. Use the stored value exactly."
        )


def _fold(value: str) -> str:
    """Lower-case and strip accents (Python side, for fuzzy matching)."""
    normalized = unicodedata.normalize("NFKD", str(value))
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower().strip()


def _identifier(node: exp.Expression) -> str:
    """Identifier as PostgreSQL resolves it: unquoted names fold to lower case."""
    identifier = node.this if isinstance(node.this, exp.Identifier) else None
    name = node.name
    if dialects.current() != dialects.POSTGRES:
        return name  # names keep their case; lookups compare case-insensitively
    return name if identifier is not None and identifier.quoted else name.lower()


def _string_literal(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal) and node.is_string:
        return node.this
    return None


def _physical_tables(ast: exp.Expression) -> Dict[str, Tuple[str, str]]:
    """Map alias/name -> (schema, table) for physical tables (not CTEs)."""
    cte_names = {cte.alias_or_name.lower() for cte in ast.find_all(exp.CTE) if cte.alias_or_name}
    tables: Dict[str, Tuple[str, str]] = {}
    for table in ast.find_all(exp.Table):
        if not table.name or table.name.lower() in cte_names:
            continue
        schema = (table.db or dialects.current_schema() or query_settings.allowed_schemas[0]).lower()
        name = _identifier(table)
        tables[table.alias_or_name.lower()] = (schema, name)
        tables.setdefault(name.lower(), (schema, name))
    return tables


def find_text_filters(ast: exp.Expression) -> List[Tuple[Optional[str], str, str]]:
    """
    (table qualifier or None, column, literal) for every
    `column = 'literal'` and `column IN ('a', 'b')` predicate.
    """
    filters = []

    for node in ast.find_all(exp.EQ):
        for column, other in ((node.left, node.right), (node.right, node.left)):
            literal = _string_literal(other)
            if isinstance(column, exp.Column) and literal is not None:
                filters.append((column.table.lower() or None, _identifier(column), literal))

    for node in ast.find_all(exp.In):
        column = node.this
        if not isinstance(column, exp.Column):
            continue
        for item in node.expressions:
            literal = _string_literal(item)
            if literal is not None:
                filters.append((column.table.lower() or None, _identifier(column), literal))

    # Deduplicate, keep order.
    seen, unique = set(), []
    for item in filters:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _quote(identifier: str) -> str:
    return dialects.quote(identifier)


def _column_type(connection, schema: str, table: str, column: str) -> Optional[str]:
    if dialects.current() in (dialects.MSSQL, dialects.SQLITE):
        inspector = inspect(connection)
        names = {name.lower(): name for name in inspector.get_table_names(schema=schema)}
        if table.lower() not in names:
            return None
        for info in inspector.get_columns(names[table.lower()], schema=schema):
            if info["name"].lower() == column.lower():
                # "VARCHAR(40)" -> "varchar", "NVARCHAR COLLATE ..." -> "nvarchar"
                return str(info["type"]).split("(")[0].split()[0].lower()
        return None
    if dialects.current() == dialects.MYSQL:
        row = connection.execute(
            text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE LOWER(table_schema) = LOWER(:schema) AND LOWER(table_name) = LOWER(:table) "
                "AND LOWER(column_name) = LOWER(:column)"
            ),
            {"schema": schema, "table": table, "column": column},
        ).first()
        return str(row[2]).lower() if row else None
    return connection.execute(
        text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table AND column_name = :column"
        ),
        {"schema": schema, "table": table, "column": column},
    ).scalar()


def _resolve_table(
    connection,
    qualifier: Optional[str],
    column: str,
    tables: Dict[str, Tuple[str, str]],
) -> Optional[Tuple[str, str, str]]:
    """(schema, table, data_type) owning the column, if unambiguous."""
    if qualifier:
        if qualifier not in tables:
            return None
        schema, table = tables[qualifier]
        data_type = _column_type(connection, schema, table, column)
        return (schema, table, data_type) if data_type else None

    owners = []
    for schema, table in set(tables.values()):
        data_type = _column_type(connection, schema, table, column)
        if data_type:
            owners.append((schema, table, data_type))
    return owners[0] if len(owners) == 1 else None


def _suggestions(connection, qualified: str, column_sql: str, value: str) -> List[str]:
    # 1. Same value ignoring case and accents (e.g. 'São Paulo' -> 'sao paulo').
    if dialects.current() in (dialects.MYSQL, dialects.MSSQL):
        # The default MySQL / SQL Server collations already ignore case
        # (and on MySQL, accents).
        rows = connection.execute(
            text(dialects.select_limited(f"{column_sql} FROM {qualified} WHERE {column_sql} = :value",
                                         MAX_SUGGESTIONS, distinct=True)),
            {"value": value.strip()},
        ).scalars().all()
    elif dialects.current() == dialects.SQLITE:
        rows = connection.execute(
            text(dialects.select_limited(f"{column_sql} FROM {qualified} WHERE lower({column_sql}) = lower(:value)",
                                         MAX_SUGGESTIONS, distinct=True)),
            {"value": value.strip()},
        ).scalars().all()
    else:
        rows = connection.execute(
            text(
                f"SELECT DISTINCT {column_sql} FROM {qualified} "
                f"WHERE translate(lower({column_sql}), :accented, :plain) "
                f"    = translate(lower(:value), :accented, :plain) "
                f"LIMIT {MAX_SUGGESTIONS}"
            ),
            {"accented": _ACCENTED, "plain": _PLAIN, "value": value.strip()},
        ).scalars().all()
    if rows:
        return [str(row) for row in rows]

    # 2. Close spellings among a bounded sample of distinct values.
    sample = connection.execute(
        text(dialects.select_limited(f"{column_sql} FROM {qualified} WHERE {column_sql} IS NOT NULL",
                                     FUZZY_SAMPLE_VALUES, distinct=True))
    ).scalars().all()
    folded = {}
    for candidate in sample:
        folded.setdefault(_fold(candidate), str(candidate))
    matches = difflib.get_close_matches(_fold(value), list(folded), n=MAX_SUGGESTIONS, cutoff=0.8)
    return [folded[match] for match in matches]


def check_filter_values(ast: exp.Expression, engine: Engine) -> Dict[str, List]:
    """
    Check literal text filters against the stored data.

    Returns {"issues": [...], "unmatched": [...]}:
      issues    - value absent but similar stored values exist
                  (almost certainly a formatting/spelling mismatch)
      unmatched - value absent and nothing similar (a legitimate
                  filter with no matching rows)
    """
    filters = find_text_filters(ast)[:MAX_FILTERS_CHECKED]
    if not filters:
        return {"issues": [], "unmatched": []}

    tables = _physical_tables(ast)
    issues: List[Dict] = []
    unmatched: List[Dict] = []

    with read_only_connection(engine) as connection:
        for qualifier, column, value in filters:
            owner = _resolve_table(connection, qualifier, column, tables)
            if owner is None:
                continue
            schema, table, data_type = owner
            if data_type not in TEXT_TYPES:
                continue

            qualified = f"{_quote(schema)}.{_quote(table)}"
            column_sql = _quote(column)

            exists = connection.execute(
                text(f"SELECT CASE WHEN EXISTS (SELECT 1 FROM {qualified} WHERE {column_sql} = :value) "
                     "THEN 1 ELSE 0 END"),
                {"value": value},
            ).scalar()
            if exists:
                continue

            suggestions = [s for s in _suggestions(connection, qualified, column_sql, value) if s != value]
            record = ValueIssue(table=table, column=column, value=value, suggestions=suggestions)
            if suggestions:
                issues.append(asdict(record) | {"message": record.message()})
            else:
                unmatched.append(asdict(record) | {
                    "message": f"No rows have {table}.{column} = '{value}' (no similar stored values)."
                })

    return {"issues": issues, "unmatched": unmatched}
