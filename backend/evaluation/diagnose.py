# backend/evaluation/diagnose.py
#
# Why did a case fail? Compares the predicted SQL with the expected SQL
# structurally (sqlglot) and combines that with the pipeline outcome,
# so a report says "wrong filter value" or "missed a table in
# retrieval" instead of only "values differ".
#
# Categories, checked in this order (the first that applies is the
# primary category; all that apply are returned as signals):
#
#   pipeline_error    the agent stopped with an error (stage given)
#   clarification     the agent asked a question instead of answering
#   timeout           a stage or the database timed out
#   retrieval_miss    a table the answer needs was not retrieved
#   missing_table     the SQL does not use a table the answer needs
#   wrong_join        extra tables joined (often duplicates rows)
#   wrong_filter      different filter values or conditions
#   wrong_aggregation different aggregate functions (SUM vs AVG, ...)
#   wrong_grouping    different GROUP BY columns
#   wrong_limit       different LIMIT / OFFSET
#   wrong_order       right rows, wrong order (when order matters)
#   missing_columns   fewer columns than the expected answer
#   wrong_calculation the result columns are computed differently
#                     (e.g. COUNT(*) + 1, price instead of price * qty)
#   wrong_values      none of the above explains the difference
#
# Heuristic by design: it points at the most likely cause for a human
# to confirm, it does not prove it.

from collections import Counter
from typing import Any, Dict, List, Optional, Set

import sqlglot
from sqlglot import exp
from backend import dialects

CATEGORIES = (
    "pipeline_error", "clarification", "timeout", "retrieval_miss", "missing_table", "wrong_join",
    "wrong_filter", "wrong_aggregation", "wrong_grouping", "wrong_limit", "wrong_order",
    "missing_columns", "wrong_calculation", "wrong_values",
)

DESCRIPTIONS = {
    "pipeline_error": "The pipeline stopped with an error",
    "clarification": "Asked for clarification instead of answering",
    "timeout": "A model call or query timed out",
    "retrieval_miss": "Schema retrieval missed a needed table",
    "missing_table": "SQL does not use a needed table",
    "wrong_join": "Joined extra tables",
    "wrong_filter": "Different filter values or conditions",
    "wrong_aggregation": "Different aggregate function",
    "wrong_grouping": "Different grouping",
    "wrong_limit": "Different LIMIT / OFFSET",
    "wrong_order": "Right rows, wrong order",
    "missing_columns": "Missing result columns",
    "wrong_calculation": "Different calculation in the result columns",
    "wrong_values": "Values differ (cause not identified)",
}


def _parse(sql: str) -> Optional[exp.Expression]:
    if not sql or not sql.strip():
        return None
    try:
        return sqlglot.parse_one(sql, read=dialects.sqlglot_name())
    except Exception:
        return None


def _tables(tree: exp.Expression) -> Set[str]:
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    return {table.name.lower() for table in tree.find_all(exp.Table) if table.name.lower() not in ctes}


def _filter_literals(tree: exp.Expression) -> Counter:
    values: Counter = Counter()
    for clause in list(tree.find_all(exp.Where)) + list(tree.find_all(exp.Having)):
        for literal in clause.find_all(exp.Literal):
            values[str(literal.this).strip()] += 1
    # FILTER (WHERE ...) inside aggregates counts as a filter too
    for clause in tree.find_all(exp.Filter):
        for literal in clause.find_all(exp.Literal):
            values[str(literal.this).strip()] += 1
    return values


def _filter_columns(tree: exp.Expression) -> Set[str]:
    columns: Set[str] = set()
    for clause in list(tree.find_all(exp.Where)) + list(tree.find_all(exp.Having)) + list(tree.find_all(exp.Filter)):
        columns |= {column.name.lower() for column in clause.find_all(exp.Column)}
    return columns


def _aggregates(tree: exp.Expression) -> Counter:
    found: Counter = Counter()
    for node in tree.find_all(exp.AggFunc):
        name = type(node).__name__.upper()
        if node.find(exp.Distinct):
            name += " DISTINCT"
        found[name] += 1
    return found


def _group_columns(tree: exp.Expression) -> Set[str]:
    columns: Set[str] = set()
    for group in tree.find_all(exp.Group):
        select = group.parent if isinstance(group.parent, exp.Select) else None
        for item in group.expressions:
            # GROUP BY 1, 2 refers to the SELECT list by position
            if isinstance(item, exp.Literal) and item.is_int and select is not None:
                position = int(item.this) - 1
                if 0 <= position < len(select.expressions):
                    item = select.expressions[position]
            columns |= {column.name.lower() for column in item.find_all(exp.Column)}
            if isinstance(item, exp.Column):
                columns.add(item.name.lower())
    return columns


def _projections(tree: exp.Expression) -> Counter:
    """Outer SELECT expressions without aliases or table qualifiers, lower-cased."""
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return Counter()
    found: Counter = Counter()
    for expression in select.expressions:
        node = expression.this if isinstance(expression, exp.Alias) else expression
        node = node.copy()
        for column in node.find_all(exp.Column):
            column.set("table", None)
        found[node.sql(dialect=dialects.sqlglot_name()).lower()] += 1
    return found


def _limits(tree: exp.Expression) -> List[str]:
    values = []
    for node in list(tree.find_all(exp.Limit)) + list(tree.find_all(exp.Offset)):
        target = node.expression if node.expression is not None else node.this
        values.append(f"{type(node).__name__.lower()} {target.sql() if target is not None else ''}".strip())
    return sorted(values)


def _describe_counter_diff(expected: Counter, predicted: Counter) -> str:
    missing = sorted((expected - predicted).elements())
    extra = sorted((predicted - expected).elements())
    parts = []
    if missing:
        parts.append("expected " + ", ".join(missing))
    if extra:
        parts.append("got " + ", ".join(extra))
    return "; ".join(parts)


def diagnose(
    *,
    status: str,
    error_stage: str = "",
    error: str = "",
    expected_sql: str = "",
    predicted_sql: str = "",
    comparison_reason: str = "",
    retrieval_recall: Optional[float] = None,
    expected_tables: Optional[List[str]] = None,
    retrieved_tables: Optional[List[str]] = None,
    order_only: bool = False,
) -> Dict[str, Any]:
    """{"category": str, "detail": str, "signals": [{"category", "detail"}, ...]}"""
    signals: List[Dict[str, str]] = []

    def add(category: str, detail: str = "") -> None:
        signals.append({"category": category, "detail": detail})

    text = f"{error} {comparison_reason}".lower()
    if "timeout" in text or "timed out" in text or "canceling statement" in text:
        add("timeout", (error or comparison_reason)[:200])
    if status == "NEEDS_CLARIFICATION":
        add("clarification", "")
    elif status not in ("SUCCESS", ""):
        add("pipeline_error", f"stage: {error_stage or 'unknown'}" + (f" ({error[:160]})" if error else ""))

    if retrieval_recall is not None and retrieval_recall < 1.0:
        missed = sorted(set(expected_tables or []) - {t.lower() for t in retrieved_tables or []})
        add("retrieval_miss", "not retrieved: " + ", ".join(missed))

    expected_tree, predicted_tree = _parse(expected_sql), _parse(predicted_sql)
    if expected_tree is not None and predicted_tree is not None:
        expected_tables_set, predicted_tables_set = _tables(expected_tree), _tables(predicted_tree)
        if expected_tables_set - predicted_tables_set:
            add("missing_table", "not used: " + ", ".join(sorted(expected_tables_set - predicted_tables_set)))
        if predicted_tables_set - expected_tables_set:
            add("wrong_join", "extra: " + ", ".join(sorted(predicted_tables_set - expected_tables_set)))

        expected_literals, predicted_literals = _filter_literals(expected_tree), _filter_literals(predicted_tree)
        expected_filter_cols, predicted_filter_cols = _filter_columns(expected_tree), _filter_columns(predicted_tree)
        if expected_literals != predicted_literals or expected_filter_cols != predicted_filter_cols:
            detail = _describe_counter_diff(expected_literals, predicted_literals)
            if not detail and expected_filter_cols != predicted_filter_cols:
                detail = (f"filtered on {', '.join(sorted(predicted_filter_cols)) or 'nothing'}, expected "
                          f"{', '.join(sorted(expected_filter_cols)) or 'no filter'}")
            add("wrong_filter", detail)

        expected_aggs, predicted_aggs = _aggregates(expected_tree), _aggregates(predicted_tree)
        if expected_aggs != predicted_aggs:
            add("wrong_aggregation", _describe_counter_diff(expected_aggs, predicted_aggs))

        expected_group, predicted_group = _group_columns(expected_tree), _group_columns(predicted_tree)
        if expected_group != predicted_group:
            add("wrong_grouping", f"grouped by {', '.join(sorted(predicted_group)) or 'nothing'}, expected "
                                  f"{', '.join(sorted(expected_group)) or 'no grouping'}")

        expected_projection, predicted_projection = _projections(expected_tree), _projections(predicted_tree)
        if expected_projection != predicted_projection and not (expected_aggs != predicted_aggs):
            missing = sorted((expected_projection - predicted_projection).elements())
            extra = sorted((predicted_projection - expected_projection).elements())
            if missing and extra:  # only extra columns is fine (lenient match)
                add("wrong_calculation", f"expected {', '.join(missing)[:120]}; got {', '.join(extra)[:120]}")

        if _limits(expected_tree) != _limits(predicted_tree):
            add("wrong_limit", f"{' '.join(_limits(predicted_tree)) or 'no limit'}, expected "
                               f"{' '.join(_limits(expected_tree)) or 'no limit'}")

    if order_only:
        add("wrong_order", "same rows in a different order")
    if "missing columns" in comparison_reason:
        add("missing_columns", comparison_reason)

    if not signals:
        add("wrong_values", comparison_reason)

    primary = min(signals, key=lambda signal: CATEGORIES.index(signal["category"]))
    return {"category": primary["category"], "detail": primary["detail"], "signals": signals}
