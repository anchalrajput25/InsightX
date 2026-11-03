# backend/whatif/builder.py
#
# Deterministic SQL for a verified Scenario. No LLM here.
#
# The baseline is the metric exactly as written. The scenario is the same
# aggregate over the same rows with the levers applied inside it, so both
# come from one query and are always compared on identical data:
#
#   column levers   scale / shift / set / cap / floor rewrite a column,
#                   only for the rows of their segment:
#                       CASE WHEN <segment> THEN price * 1.1 ELSE price END
#                   Several levers on one column stack in order.
#   value levers    the same on the metric's per-row value (SUM/AVG
#                   argument), e.g. "delivery 2 days faster" on
#                   AVG(delivered - purchased)
#   row weights     volume (+20% rows), price elasticity and exclusions
#                   weight each row: SUM(v * w), SUM(w) for COUNT(*),
#                   SUM(v * w) / SUM(w) for AVG. Excluded rows weigh 0.
#   ratios          (COUNT(DISTINCT ...), SUM(a)/SUM(b), ...) accept column
#                   levers and exclusions (excluded rows become NULL, which
#                   every aggregate ignores); volume does not apply.
#
# Joins come from SchemaGraph (many-to-one only), so a breakdown or a
# segment column from another table never duplicates fact rows.

from typing import Dict, List, Optional, Sequence, Tuple

import sqlglot
from sqlglot import exp

from backend import dialects
from backend.investigation.engine import col_sql
from backend.whatif.spec import Lever, Scenario, WhatIfError


def _parse(sql: str) -> exp.Expression:
    return sqlglot.parse_one(sql, read=dialects.sqlglot_name())


def _sql(node: exp.Expression) -> str:
    return node.sql(dialect=dialects.sqlglot_name())


def _num(value: float) -> str:
    return repr(float(value))


def _changed(lever: Lever, current: str) -> str:
    """The new value of a column (or of the per-row value) under one lever."""
    if lever.type == "scale":
        return f"({current}) * {_num(1 + lever.percent / 100.0)}"
    if lever.type == "shift":
        return f"({current}) + {_num(lever.amount)}"
    if lever.type == "set":
        return f"CASE WHEN ({current}) IS NULL THEN NULL ELSE {_num(lever.value)} END"
    if lever.type == "cap":
        return f"CASE WHEN ({current}) > {_num(lever.limit)} THEN {_num(lever.limit)} ELSE ({current}) END"
    if lever.type == "floor":
        return f"CASE WHEN ({current}) < {_num(lever.limit)} THEN {_num(lever.limit)} ELSE ({current}) END"
    raise WhatIfError(f"{lever.type} does not change a value.")


def _only_for(lever: Lever, new: str, current: str) -> str:
    return f"CASE WHEN {lever.segment.sql()} THEN {new} ELSE {current} END" if lever.segment else new


def _weight(lever: Lever, factor: float) -> str:
    if lever.segment:
        return f"CASE WHEN {lever.segment.sql()} THEN {_num(factor)} ELSE 1.0 END"
    return _num(factor)


def scenario_expression(scenario: Scenario, levers: Optional[Sequence[Lever]] = None) -> str:
    """The metric with `levers` applied (all of the scenario's by default)."""
    levers = list(scenario.levers if levers is None else levers)
    metric = scenario.metric

    columns: Dict[Tuple[str, str], str] = {}
    value_levers: List[Lever] = []
    weights: List[str] = []
    exclusions: List[str] = []
    for lever in levers:
        if lever.type == "exclude":
            exclusions.append(lever.segment.sql())
            continue
        if lever.type == "volume":
            weights.append(_weight(lever, max(0.0, 1 + lever.percent / 100.0)))
            continue
        if lever.column:
            key = (lever.table, lever.column)
            current = columns.get(key, col_sql(*key))
            columns[key] = _only_for(lever, _changed(lever, current), current)
        else:
            value_levers.append(lever)
        if lever.type == "scale" and lever.elasticity:
            # elasticity e: a p% change in this column changes the rows by e*p%
            weights.append(_weight(lever, max(0.0, 1 + lever.elasticity * lever.percent / 100.0)))

    tree = _parse(metric.expression)
    for node in list(tree.find_all(exp.Column)):
        key = (node.table, node.name)
        if key in columns:
            node.replace(exp.Paren(this=_parse(columns[key])))

    if metric.kind == "general":
        if value_levers or weights:
            raise WhatIfError("Row-volume and per-row value changes need a SUM, COUNT or AVG metric.")
        if exclusions:
            excluded = " OR ".join(f"({item})" for item in exclusions)
            # Excluded rows become NULL for every column the metric reads
            # (the list is taken first, so inserted CASEs are not re-wrapped).
            for node in list(tree.find_all(exp.Column)):
                node.replace(_parse(f"CASE WHEN {excluded} THEN NULL ELSE {_sql(node)} END"))
            for star in list(tree.find_all(exp.Star)):
                if isinstance(star.parent, exp.Count):
                    star.replace(_parse(f"CASE WHEN {excluded} THEN NULL ELSE 1 END"))
        return _sql(tree)

    if exclusions:
        weights.append(f"CASE WHEN {' OR '.join(f'({item})' for item in exclusions)} THEN 0.0 ELSE 1.0 END")
    weight = " * ".join(f"({item})" for item in weights)

    value = None if metric.kind == "count_star" else _sql(tree.this)
    original_value = value
    for lever in value_levers:
        value = _only_for(lever, _changed(lever, value), value)

    if metric.kind == "count_star":
        return f"SUM({weight})" if weight else _sql(tree)
    if metric.kind == "count":
        if not weight:
            return _sql(tree)
        return f"SUM(CASE WHEN ({original_value}) IS NOT NULL THEN {weight} ELSE 0.0 END)"
    if metric.kind == "sum":
        return f"SUM(({value}) * {weight})" if weight else f"SUM({value})"
    # avg
    if not weight:
        return f"AVG({value})"
    return (f"SUM(({value}) * {weight}) / "
            f"NULLIF(SUM(CASE WHEN ({value}) IS NOT NULL THEN {weight} ELSE 0.0 END), 0)")


def from_where(scenario: Scenario, extra_tables: Optional[List[str]] = None) -> Tuple[str, str]:
    return scenario.graph.from_clause(scenario.tables(extra_tables)), scenario.where()


def totals_sql(scenario: Scenario, expressions: Sequence[str]) -> str:
    """Baseline (b), every scenario expression (s0, s1, ...) and the row count (n)."""
    source, where = from_where(scenario)
    columns = ", ".join(f"{item} AS s{index}" for index, item in enumerate(expressions))
    return f"SELECT {scenario.metric.expression} AS b, {columns}, COUNT(*) AS n FROM {source} WHERE {where}"


def breakdown_sql(scenario: Scenario, expression: str) -> str:
    table, column = scenario.breakdown
    key = col_sql(table, column)
    source, where = from_where(scenario, [table])
    return (f"SELECT {key} AS k, {scenario.metric.expression} AS b, {expression} AS s, COUNT(*) AS n "
            f"FROM {source} WHERE {where} GROUP BY {key}")


def series_sql(scenario: Scenario, expression: Optional[str]) -> str:
    """Metric per period (p), the latest timestamp in it (last), baseline (b) and scenario (s)."""
    table, column = scenario.time
    stamp = col_sql(table, column)
    period = dialects.period(scenario.grain, stamp)
    source, where = from_where(scenario, [table])
    scenario_column = f", {expression} AS s" if expression else ""
    return (f"SELECT {period} AS p, MAX({stamp}) AS last_seen, {scenario.metric.expression} AS b{scenario_column}, "
            f"COUNT(*) AS n FROM {source} WHERE {stamp} IS NOT NULL AND {where} "
            f"GROUP BY {period} ORDER BY {period}")
