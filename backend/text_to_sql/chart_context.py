"""
Context rows for the chart when the answer itself has nothing to compare.

1. "Top N" picks: "Which category earns the most?" is answered with
   ORDER BY ... LIMIT 1. The chart re-runs it with LIMIT 25 so the
   winner is shown next to the others ("top").
2. Single totals: "What is the total revenue?" returns one number. The
   chart re-runs the same total split by month of a date column of the
   queried table ("trend"), or, without a usable date, by a short label
   column such as country or category ("split").

No AI call: the extra SQL is built from the answer's own SQL and goes
through the same read-only safety gate and executor as the answer.
"""

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import sqlglot
from sqlglot import exp
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Engine

from backend.text_to_sql.query_executor import execute_query
from backend.text_to_sql.sql_safety import check_sql_safety, clean_sql
from backend import dialects

logger = logging.getLogger(__name__)

CHART_ROWS = 25
MAX_PICK = 10
MAX_PERIODS = 60
SPLIT_GROUPS = 25

# Date columns that say when the thing happened, best first. Bookkeeping
# dates (last_update, modified) and people's dates (birth, hire) are not
# a useful trend for a business total.
_EVENT_DATE = re.compile(r"(order|payment|invoice|rental|sale|transaction|purchase|ship|creat|start|date)", re.I)
_SKIP_DATE = re.compile(r"(update|modif|birth|hire|required|expir|end_|due|valid|rowguid|sell_end|discontinu)", re.I)
# Short label columns worth splitting a total by, best first.
_SPLIT_NAME = re.compile(
    r"(category|genre|type|status|country|region|territory|state|city|segment|channel|rating|store|department|group|class|kind|method|name)",
    re.I,
)
_TEXT_TYPES = ("CHAR", "TEXT", "STRING", "ENUM")
_DATE_TYPES = ("DATE", "TIME")


def _from(node: exp.Select):
    return node.args.get("from") or node.args.get("from_")


def _small_limit(node: exp.Expression) -> Optional[int]:
    limit = node.args.get("limit")
    if not isinstance(limit, exp.Limit) or node.args.get("offset"):
        return None
    value = limit.expression
    if not isinstance(value, exp.Literal) or value.is_string:
        return None
    try:
        number = int(value.this)
    except ValueError:
        return None
    return number if 1 <= number <= MAX_PICK else None


def context_sql(sql: str) -> Optional[str]:
    """The same query with its small top-level LIMIT widened, or None."""
    sql = clean_sql(sql or "")
    if not sql:
        return None
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialects.sqlglot_name()) if s is not None]
    except Exception:
        return None
    if len(statements) != 1:
        return None
    root = statements[0]
    if not isinstance(root, exp.Select) or not root.args.get("order"):
        return None
    if _small_limit(root) is None:
        return None
    # A single aggregate row (no GROUP BY anywhere) has nothing to widen.
    if not root.find(exp.Group):
        return None
    widened = root.copy().limit(CHART_ROWS + 1)
    if not root.args.get("distinct"):
        _add_missing_columns(widened)
    return widened.sql(dialect=dialects.sqlglot_name())


def _measure_name(node: exp.Expression) -> str:
    column = node.find(exp.Column)
    column_name = column.name if column is not None else ""
    if isinstance(node, exp.Count):
        return "count" if not column_name else f"count_{column_name}"
    for kind, word in ((exp.Sum, "total"), (exp.Avg, "average"), (exp.Max, "max"), (exp.Min, "min")):
        if isinstance(node, kind):
            return f"{word}_{column_name}" if column_name else word
    return "value"


def _add_missing_columns(query: exp.Select) -> None:
    """
    "Which category earns the most?" often selects only the name and
    ranks by SUM(...) in ORDER BY. The chart needs both, so the GROUP BY
    keys and the ranking value are added to the select list when missing.
    """
    projected = {e.alias_or_name for e in query.expressions} | {
        (e.this if isinstance(e, exp.Alias) else e).sql() for e in query.expressions
    }
    group = query.args.get("group")
    added_keys = []
    if group is not None:
        for key in group.expressions:
            if isinstance(key, exp.Literal) or key.sql() in projected:
                continue
            if isinstance(key, exp.Column) and key.name in projected:
                continue
            added_keys.append(key.copy())
    order = query.args.get("order")
    measure = None
    if order is not None and order.expressions:
        first = order.expressions[0].this
        is_alias = isinstance(first, exp.Column) and not first.table and first.name in projected
        if not isinstance(first, exp.Literal) and not is_alias and first.sql() not in projected:
            if first.find(exp.AggFunc):
                measure = exp.alias_(first.copy(), _measure_name(first))
    if added_keys or measure is not None:
        query.set("expressions", added_keys + list(query.expressions) + ([measure] if measure is not None else []))


# ------------------------------------------------------------
# Single totals -> trend or split
# ------------------------------------------------------------

def _single_total(sql: str) -> Optional[Tuple[exp.Select, List[exp.Table]]]:
    """The parsed query if it is one plain total over real tables."""
    sql = clean_sql(sql or "")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialects.sqlglot_name()) if s is not None]
    except Exception:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    root = statements[0]
    if root.args.get("group") or root.args.get("having") or root.args.get("distinct"):
        return None
    if root.args.get("with") or root.find(exp.Window):
        return None
    if not root.expressions or not all(e.find(exp.AggFunc) for e in root.expressions):
        return None
    source = _from(root)
    if source is None:
        return None
    tables = [source.this] + [join.this for join in root.args.get("joins") or []]
    if not all(isinstance(table, exp.Table) for table in tables):
        return None
    return root, tables


def _columns_of(engine: Engine) -> Callable[[str, Optional[str]], List[Dict[str, Any]]]:
    inspector = sa_inspect(engine)
    cache: Dict[Tuple[str, Optional[str]], List[Dict[str, Any]]] = {}

    def columns(table: str, schema: Optional[str]) -> List[Dict[str, Any]]:
        key = (table, schema)
        if key not in cache:
            try:
                cache[key] = inspector.get_columns(table, schema=schema or None)
            except Exception:
                cache[key] = []
        return cache[key]

    return columns


def _type_name(column: Dict[str, Any]) -> str:
    return str(column.get("type") or "").upper()


def pick_date(tables: List[exp.Table], columns) -> Optional[Tuple[str, str]]:
    """(table alias, column) of the best event date, or None."""
    best = None
    for table in tables:
        for column in columns(table.name, table.db or None):
            name = column["name"]
            if not any(t in _type_name(column) for t in _DATE_TYPES) or "INTERVAL" in _type_name(column):
                continue
            if _SKIP_DATE.search(name):
                continue
            score = 0 if _EVENT_DATE.search(name) else 1
            if best is None or score < best[0]:
                best = (score, table.alias_or_name, name)
        if best and best[0] == 0:
            break
    return (best[1], best[2]) if best else None


def pick_split(tables: List[exp.Table], columns) -> List[Tuple[str, str]]:
    """(table alias, column) label columns to try, best first."""
    found = []
    for order, table in enumerate(tables):
        for column in columns(table.name, table.db or None):
            name = column["name"]
            if not any(t in _type_name(column) for t in _TEXT_TYPES):
                continue
            match = _SPLIT_NAME.search(name)
            if not match or re.search(r"(first|last|middle|user|email|phone|address|password|title|description)", name, re.I):
                continue
            found.append((_SPLIT_NAME.pattern.index(match.group(1).lower()), order, table.alias_or_name, name))
    found.sort()
    return [(alias, name) for _, _, alias, name in found]


def trend_sql(root: exp.Select, alias: str, column: str, unit: str) -> str:
    query = root.copy()
    query.set("limit", None)
    query.set("order", None)
    name = dialects.sqlglot_name()
    column_sql = exp.column(column, table=alias).sql(dialect=name)
    period = exp.alias_(sqlglot.parse_one(dialects.period(unit, column_sql), read=name), unit)
    query.set("expressions", [period] + list(query.expressions))
    not_null = exp.Not(this=exp.Is(this=exp.column(column, table=alias), expression=exp.Null()))
    query = query.where(not_null)
    return query.group_by(exp.Literal.number(1)).order_by(exp.Literal.number(1)).limit(MAX_PERIODS + 1).sql(dialect=dialects.sqlglot_name())


def split_sql(root: exp.Select, alias: str, column: str) -> str:
    query = root.copy()
    query.set("limit", None)
    query.set("order", None)
    query.set("expressions", [exp.column(column, table=alias)] + list(query.expressions))
    ordered = exp.Ordered(this=exp.Literal.number(2), desc=True)
    query = query.group_by(exp.Literal.number(1))
    query.set("order", exp.Order(expressions=[ordered]))
    return query.limit(SPLIT_GROUPS + 1).sql(dialect=dialects.sqlglot_name())


def _varies(result: Dict[str, Any]) -> bool:
    measure = result["columns"][-1]
    return len({str(row.get(measure)) for row in result["rows"]}) > 1


def lookup_splits(root: exp.Select, tables: List[exp.Table], columns, engine: Engine) -> List[Tuple[str, str]]:
    """
    Splits through a foreign key to a small lookup table, e.g. tracks by
    genre: JOIN music_genre ON genre_id and group by its name.
    """
    try:
        inspector = sa_inspect(engine)
    except Exception:
        return []
    found = []
    for table in tables[:1]:
        try:
            keys = inspector.get_foreign_keys(table.name, schema=table.db or None)
        except Exception:
            continue
        for key in keys:
            if len(key.get("constrained_columns") or []) != 1:
                continue
            target = key.get("referred_table")
            labels = [
                c["name"] for c in columns(target, key.get("referred_schema"))
                if any(t in _type_name(c) for t in _TEXT_TYPES) and _SPLIT_NAME.search(c["name"])
                and not re.search(r"(first|last|email|phone|address|password|description)", c["name"], re.I)
            ]
            if not labels:
                continue
            match = _SPLIT_NAME.search(target) or _SPLIT_NAME.search(labels[0])
            rank = _SPLIT_NAME.pattern.index(match.group(1).lower())
            found.append((rank, table, key, target, labels[0]))
    found.sort(key=lambda item: item[0])

    splits = []
    for _, table, key, target, label in found[:4]:
        query = root.copy()
        query.set("limit", None)
        query.set("order", None)
        ref = "chart_ref"
        target_table = exp.Table(
            this=exp.to_identifier(target),
            db=exp.to_identifier(key["referred_schema"]) if key.get("referred_schema") else None,
            alias=exp.TableAlias(this=exp.to_identifier(ref)),
        )
        condition = exp.EQ(
            this=exp.column(key["referred_columns"][0], table=ref),
            expression=exp.column(key["constrained_columns"][0], table=table.alias_or_name),
        )
        query = query.join(target_table, on=condition)
        query.set("expressions", [exp.alias_(exp.column(label, table=ref), target.split("_")[-1] if label == "name" else label)] + list(query.expressions))
        query = query.group_by(exp.Literal.number(1))
        query.set("order", exp.Order(expressions=[exp.Ordered(this=exp.Literal.number(2), desc=True)]))
        splits.append((query.limit(SPLIT_GROUPS + 1).sql(dialect=dialects.sqlglot_name()), target.split("_")[-1]))
    return splits


def _run(sql: str, engine: Engine, max_rows: int) -> Optional[Dict[str, Any]]:
    if not check_sql_safety(sql).get("safe"):
        return None
    result = execute_query(sql=sql, engine=engine, max_rows=max_rows)
    if not result.get("success"):
        logger.info("Chart context query failed: %s", result.get("error"))
        return None
    return result


def total_context(sql: str, execution: Dict[str, Any], engine: Engine) -> Dict[str, Any]:
    parsed = _single_total(sql)
    if parsed is None:
        return {}
    root, tables = parsed
    columns = _columns_of(engine)

    date = pick_date(tables, columns)
    if date:
        alias, column = date
        for unit in ("month", "year"):
            wider = trend_sql(root, alias, column, unit)
            result = _run(wider, engine, MAX_PERIODS)
            if result is None:
                break
            if result["truncated"]:
                continue
            if result["row_count"] >= 2:
                return {
                    "kind": "trend",
                    "rows": result["rows"],
                    "columns": result["columns"],
                    "sql": wider,
                    "note": f"The same total split by {unit} of {column.replace('_', ' ')}.",
                }
            break

    candidates = [(split_sql(root, alias, column), column) for alias, column in pick_split(tables, columns)[:3]]
    candidates += lookup_splits(root, tables, columns, engine)
    for wider, column in candidates[:6]:
        result = _run(wider, engine, SPLIT_GROUPS)
        # Only short lists with real differences: splitting by a
        # near-unique name, or into equal parts, says nothing.
        if result and 2 <= result["row_count"] and not result["truncated"] and _varies(result):
            return {
                "kind": "split",
                "rows": result["rows"],
                "columns": result["columns"],
                "sql": wider,
                "note": f"The same total split by {column.replace('_', ' ')}.",
            }
    return {}


def chart_context(
    sql: str,
    execution: Dict[str, Any],
    engine: Engine,
) -> Dict[str, Any]:
    """
    {"kind", "rows", "columns", ...} for the chart, or {} when the answer
    already holds everything worth drawing.
    """
    if not execution or not execution.get("success"):
        return {}
    picked = int(execution.get("row_count") or 0)
    if picked < 1 or picked > MAX_PICK:
        return {}

    if picked == 1:
        total = total_context(sql, execution, engine)
        if total:
            return total

    wider = context_sql(sql)
    if not wider or not check_sql_safety(wider).get("safe"):
        return {}

    result = execute_query(sql=wider, engine=engine, max_rows=CHART_ROWS)
    if not result.get("success") or result.get("row_count", 0) <= picked:
        if not result.get("success"):
            logger.info("Chart context query failed: %s", result.get("error"))
        return {}
    if not set(execution.get("columns") or []) <= set(result.get("columns") or []):
        return {}

    return {
        "kind": "top",
        "rows": result["rows"],
        "columns": result["columns"],
        "picked": picked,
        "truncated": bool(result.get("truncated")),
        "sql": wider,
    }
