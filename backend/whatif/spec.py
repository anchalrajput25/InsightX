# backend/whatif/spec.py
#
# A what-if scenario as data, and its verification against the real
# schema. Nothing from the LLM (or the browser) is trusted as-is:
#
#   metric      one aggregate over columns reachable from the fact table
#               through many-to-one joins only, so no join can multiply
#               rows (same rule and helpers as the investigation planner)
#   levers      each one names a real column (numeric for arithmetic
#               levers), a known lever type and numbers inside safe bounds
#   segments    "which rows a lever applies to": a column, an operator
#               and values. Text values are matched to the stored
#               spelling ("sao paulo" -> "São Paulo"); a value that does
#               not occur is an error with the closest stored values.
#               "the 2 worst sellers" is a rank segment, resolved by SQL
#               to the actual seller ids before the scenario runs.
#
# The spec only ever becomes SQL through builder.py, never by pasting
# LLM text into a query.

import difflib
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlglot import exp
from sqlalchemy.engine import Engine

from backend import dialects
from backend.investigation.engine import InvestigationError, SchemaGraph, _run, col_sql
from backend.investigation.planner import (
    _choose_time_column,
    _dimensions,
    _is_dimension,
    _qualify_expression,
    _resolve,
    _split,
)


class WhatIfError(ValueError):
    pass


LEVER_TYPES = ("scale", "shift", "set", "cap", "floor", "exclude", "volume")
COLUMN_LEVERS = ("scale", "shift", "set", "cap", "floor")
OPS = ("=", "!=", "in", "not in", ">", ">=", "<", "<=")
GRAINS = ("day", "week", "month", "quarter", "year")
MAX_LEVERS = 6
MAX_VALUES = 50
MAX_RANK_N = 50
MAX_STORED_VALUES = 5000
PERCENT_RANGE = (-100.0, 1000.0)
ELASTICITY_RANGE = (-10.0, 10.0)

_NUMERIC_TYPE = re.compile(r"int|real|double|float|numeric|decimal|money|number|serial", re.I)
_TEXT_TYPE = re.compile(r"char|text|string|clob|uuid|enum", re.I)


# ============================================================
# DATA
# ============================================================

def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


@dataclass
class Segment:
    """The rows a lever applies to."""
    table: str
    column: str
    op: str = "in"
    values: List[Any] = field(default_factory=list)
    rank: str = ""          # top | bottom (resolved into values)
    n: int = 0

    @property
    def reference(self) -> str:
        return f"{self.table}.{self.column}"

    def sql(self) -> str:
        column = col_sql(self.table, self.column)
        if self.op in ("in", "="):
            if len(self.values) == 1:
                return f"{column} = {_literal(self.values[0])}"
            return f"{column} IN ({', '.join(_literal(v) for v in self.values)})"
        if self.op in ("not in", "!="):
            if len(self.values) == 1:
                return f"{column} <> {_literal(self.values[0])}"
            return f"{column} NOT IN ({', '.join(_literal(v) for v in self.values)})"
        return f"{column} {self.op} {_literal(self.values[0])}"

    def label(self, metric_name: str = "") -> str:
        shown = ", ".join(str(v) for v in self.values[:3]) + (f" +{len(self.values) - 3} more" if len(self.values) > 3 else "")
        if self.rank:
            worst_best = "top" if self.rank == "top" else "bottom"
            return f"{worst_best} {self.n} {self.column} by {metric_name or 'the metric'} ({shown})"
        if self.op in ("in", "="):
            return f"{self.column} = {shown}" if len(self.values) == 1 else f"{self.column} in ({shown})"
        if self.op in ("not in", "!="):
            return f"{self.column} not {shown}" if len(self.values) == 1 else f"{self.column} not in ({shown})"
        return f"{self.column} {self.op} {shown}"

    def to_spec(self) -> Dict[str, Any]:
        if self.rank:
            return {"column": self.reference, "rank": self.rank, "n": self.n}
        return {"column": self.reference, "op": self.op, "values": list(self.values)}


@dataclass
class Lever:
    type: str
    table: str = ""                 # empty: the metric's value per row
    column: str = ""
    percent: float = 0.0            # scale, volume
    amount: float = 0.0             # shift
    value: float = 0.0              # set
    limit: float = 0.0              # cap, floor
    elasticity: float = 0.0         # scale: % change in rows per 1% change
    segment: Optional[Segment] = None

    @property
    def target(self) -> str:
        return f"{self.table}.{self.column}" if self.column else "value per row"

    @property
    def parameter(self) -> str:
        return {"scale": "percent", "volume": "percent", "shift": "amount", "set": "value",
                "cap": "limit", "floor": "limit"}.get(self.type, "")

    def with_parameter(self, number: float) -> "Lever":
        import dataclasses

        return dataclasses.replace(self, **{self.parameter: float(number)})

    def label(self, metric_name: str = "") -> str:
        where = f" for {self.segment.label(metric_name)}" if self.segment else ""
        name = self.column or "value per row"
        if self.type == "scale":
            text = f"{name} {self.percent:+g}%{where}"
            return text + (f" (elasticity {self.elasticity:g})" if self.elasticity else "")
        if self.type == "shift":
            return f"{name} {self.amount:+g}{where}"
        if self.type == "set":
            return f"{name} set to {self.value:g}{where}"
        if self.type == "cap":
            return f"{name} at most {self.limit:g}{where}"
        if self.type == "floor":
            return f"{name} at least {self.limit:g}{where}"
        if self.type == "volume":
            return f"{self.percent:+g}% rows{where}"
        return f"remove {self.segment.label(metric_name) if self.segment else 'rows'}"

    def assumption(self, fact: str) -> str:
        where = f" ({self.segment.label()})" if self.segment else ""
        if self.type == "scale" and self.elasticity:
            return (f"Assumes each 1% change in {self.target} changes the number of {fact} rows by "
                    f"{self.elasticity:g}% (elasticity {self.elasticity:g}: your assumption, not measured).")
        if self.type == "scale":
            return f"Assumes the number of {fact} rows stays the same when {self.target} changes (demand unchanged)."
        if self.type == "exclude":
            return f"Assumes the removed rows{where} are lost, not won back by other segments."
        if self.type == "volume":
            return f"Assumes the extra or missing rows{where} look like the current ones."
        return f"Assumes only {self.target}{where} changes; everything else stays as it is."

    def to_spec(self) -> Dict[str, Any]:
        item: Dict[str, Any] = {"type": self.type}
        if self.type in COLUMN_LEVERS:
            item["column"] = self.target if self.column else None
        if self.parameter:
            item[self.parameter] = getattr(self, self.parameter)
        if self.type == "scale":
            item["elasticity"] = self.elasticity
        if self.segment:
            item["where"] = self.segment.to_spec()
        return item


@dataclass
class Metric:
    name: str
    expression: str                 # SQL aggregate, quoted table.column refs
    kind: str                       # sum | count | count_star | avg | general
    tables: List[str]
    tree: Any = None


@dataclass
class Scenario:
    graph: SchemaGraph
    metric: Metric
    levers: List[Lever]
    time: Optional[Tuple[str, str]] = None
    grain: str = "month"
    filters: str = ""
    filter_tables: List[str] = field(default_factory=list)
    breakdown: Optional[Tuple[str, str]] = None
    forecast_periods: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def fact(self) -> str:
        return self.graph.fact

    def tables(self, extra: Optional[List[str]] = None) -> List[str]:
        """Every table a query needs: fact, metric, filters, lever and segment columns."""
        names = [self.fact, *self.metric.tables, *self.filter_tables]
        for lever in self.levers:
            if lever.column:
                names.append(lever.table)
            if lever.segment:
                names.append(lever.segment.table)
        names += extra or []
        return list(dict.fromkeys(names))

    def where(self) -> str:
        return f"({self.filters})" if self.filters else "1 = 1"

    def assumptions(self) -> List[str]:
        sentences = [lever.assumption(self.fact) for lever in self.levers]
        sentences.append("Every number comes from read-only SQL on your live data; nothing in the database is changed.")
        return list(dict.fromkeys(sentences))

    def to_spec(self) -> Dict[str, Any]:
        def ref(pair: Optional[Tuple[str, str]]) -> Optional[str]:
            return f"{pair[0]}.{pair[1]}" if pair else None

        return {
            "metric_name": self.metric.name,
            "metric_expression": _unquote(self.metric.expression),
            "fact_table": self.fact,
            "time_column": ref(self.time),
            "grain": self.grain,
            "filters": _unquote(self.filters) or None,
            "breakdown": ref(self.breakdown),
            "levers": [lever.to_spec() for lever in self.levers],
            "forecast_periods": self.forecast_periods,
        }


def _unquote(sql: str) -> str:
    """'"orders"."price"' -> 'orders.price' for display and editing."""
    return re.sub(r'[`"\[]([^`"\]]+)[`"\]]', r"\1", sql or "")


# ============================================================
# VERIFICATION
# ============================================================

def _number(value: Any, what: str, low: Optional[float] = None, high: Optional[float] = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise WhatIfError(f"{what} must be a number (got {value!r}).") from None
    if math.isnan(number) or math.isinf(number):
        raise WhatIfError(f"{what} must be a finite number.")
    if low is not None and number < low or high is not None and number > high:
        raise WhatIfError(f"{what} must be between {low:g} and {high:g} (got {number:g}).")
    return number


def _column_type(graph: SchemaGraph, table: str, column: str) -> str:
    return str((graph.column(table, column) or {}).get("data_type") or "")


def _require_reachable(graph: SchemaGraph, tables: List[str], what: str) -> None:
    for table in tables:
        if not graph.reachable(table):
            raise WhatIfError(f"{what} uses {table}, which cannot be joined to {graph.fact} without "
                              "duplicating rows (it is not a one-to-one or many-to-one join away).")


def _locate(graph: SchemaGraph, reference: Any, what: str) -> Tuple[str, str]:
    table, column = _split(reference)
    located = _resolve(graph, table, column) if column else None
    if located is not None and not graph.reachable(located[0]):
        # The named table has the column but is not joinable; another reachable table may have it.
        located = _resolve(graph, "", column)
    if located is None or not graph.reachable(located[0]):
        raise WhatIfError(
            f"{what} {reference!r} is not a column of {graph.fact} or of a table joined to it one-to-one/many-to-one."
        )
    return located


def _fold(text: Any) -> str:
    normal = unicodedata.normalize("NFKD", str(text))
    return "".join(ch for ch in normal if not unicodedata.combining(ch)).casefold().strip()


def _stored_values(engine: Engine, table: str, column: str) -> List[Any]:
    column_ref = col_sql(table, column)
    sql = dialects.select_limited(f"{column_ref} AS v FROM {dialects.quote(table)} WHERE {column_ref} IS NOT NULL",
                                  MAX_STORED_VALUES, distinct=True)
    return [row["v"] for row in _run(sql, engine, max_rows=MAX_STORED_VALUES)["rows"]]


def _match_values(engine: Optional[Engine], table: str, column: str, values: List[Any], notes: List[str]) -> List[Any]:
    """Each asked-for text value as it is actually stored."""
    if engine is None:
        return values
    stored = _stored_values(engine, table, column)
    exact = {str(v): v for v in stored}
    folded: Dict[str, Any] = {}
    for value in stored:
        folded.setdefault(_fold(value), value)
    matched = []
    for value in values:
        text = str(value)
        if text in exact:
            matched.append(exact[text])
        elif _fold(text) in folded:
            found = folded[_fold(text)]
            notes.append(f"{column}: using the stored value {found!r} for {text!r}.")
            matched.append(found)
        else:
            close = difflib.get_close_matches(_fold(text), list(folded), n=3, cutoff=0.6)
            hint = f" Closest stored values: {', '.join(repr(folded[c]) for c in close)}." if close else ""
            raise WhatIfError(f"{text!r} does not occur in {table}.{column}.{hint}")
    return list(dict.fromkeys(matched))


def _rank_values(scenario_graph: SchemaGraph, metric: Metric, filters: str, filter_tables: List[str],
                 table: str, column: str, rank: str, n: int, engine: Optional[Engine]) -> List[Any]:
    """The top / bottom n values of a column by the metric (on the baseline)."""
    if engine is None:
        raise WhatIfError("A ranked segment needs the database to find its members.")
    column_ref = col_sql(table, column)
    tables = list(dict.fromkeys([scenario_graph.fact, *metric.tables, *filter_tables, table]))
    where = f"{column_ref} IS NOT NULL" + (f" AND ({filters})" if filters else "")
    order = "DESC" if rank == "top" else "ASC"
    sql = dialects.select_limited(
        f"{column_ref} AS k, {metric.expression} AS m FROM {scenario_graph.from_clause(tables)} "
        f"WHERE {where} GROUP BY {column_ref} ORDER BY {metric.expression} {order}", n)
    rows = _run(sql, engine, max_rows=n)["rows"]
    if not rows:
        raise WhatIfError(f"No {column} values found to rank.")
    return [row["k"] for row in rows]


def _segment(raw: Any, graph: SchemaGraph, metric: Metric, filters: str, filter_tables: List[str],
             engine: Optional[Engine], notes: List[str]) -> Optional[Segment]:
    if raw in (None, "", {}):
        return None
    if not isinstance(raw, dict):
        raise WhatIfError("A segment must look like {\"column\": \"table.column\", \"op\": \"=\", \"values\": [...]}.")
    table, column = _locate(graph, raw.get("column"), "Segment column")
    data_type = _column_type(graph, table, column)

    rank = str(raw.get("rank") or "").strip().lower()
    if rank:
        if rank in ("worst", "lowest", "smallest"):
            rank = "bottom"
        if rank in ("best", "highest", "largest"):
            rank = "top"
        if rank not in ("top", "bottom"):
            raise WhatIfError(f"Segment rank must be top or bottom (got {rank!r}).")
        n = int(_number(raw.get("n") or 1, "Segment size n", 1, MAX_RANK_N))
        values = _rank_values(graph, metric, filters, filter_tables, table, column, rank, n, engine)
        return Segment(table, column, "in", values, rank=rank, n=n)

    op = str(raw.get("op") or "in").strip().lower()
    if op == "==":
        op = "="
    if op == "<>":
        op = "!="
    if op not in OPS:
        raise WhatIfError(f"Segment operator must be one of {', '.join(OPS)} (got {op!r}).")
    values = raw.get("values")
    if values is None:
        values = raw.get("value")
    values = values if isinstance(values, list) else [values]
    values = [v for v in values if v is not None and str(v).strip() != ""]
    if not values:
        raise WhatIfError(f"The segment on {column} has no values.")
    if len(values) > MAX_VALUES:
        raise WhatIfError(f"A segment can list at most {MAX_VALUES} values.")
    if op in (">", ">=", "<", "<=") and len(values) != 1:
        raise WhatIfError(f"{op} compares with exactly one value.")

    if _NUMERIC_TYPE.search(data_type):
        values = [_number(v, f"Value for {column}") for v in values]
        values = [int(v) if float(v).is_integer() and "int" in data_type.lower() else v for v in values]
    elif op in ("=", "!=", "in", "not in") and (_TEXT_TYPE.search(data_type) or not data_type):
        values = _match_values(engine, table, column, [str(v) for v in values], notes)
    else:
        values = [str(v) for v in values]
    return Segment(table, column, op, values)


def _metric(name: str, expression: str, graph: SchemaGraph) -> Metric:
    try:
        sql, tables, tree = _qualify_expression(expression, graph, "metric")
    except InvestigationError as exc:
        raise WhatIfError(str(exc)) from exc
    if not tree.find(exp.AggFunc):
        raise WhatIfError("The metric must aggregate (SUM, COUNT, AVG, ...).")
    if tree.find(exp.Window):
        raise WhatIfError("The metric cannot use window functions.")
    distinct = tree.find(exp.Distinct) is not None
    if isinstance(tree, exp.Sum) and not distinct:
        kind = "sum"
    elif isinstance(tree, exp.Count) and isinstance(tree.this, exp.Star):
        kind = "count_star"
    elif isinstance(tree, exp.Count) and not distinct:
        kind = "count"
    elif isinstance(tree, exp.Avg) and not distinct:
        kind = "avg"
    else:
        kind = "general"
    return Metric(name=name or "metric", expression=sql, kind=kind, tables=tables or [graph.fact], tree=tree)


def _lever(raw: Any, graph: SchemaGraph, metric: Metric, filters: str, filter_tables: List[str],
           engine: Optional[Engine], notes: List[str]) -> Lever:
    if not isinstance(raw, dict):
        raise WhatIfError("Each lever must be an object with a \"type\".")
    kind = str(raw.get("type") or "").strip().lower()
    aliases = {"multiply": "scale", "percent": "scale", "change": "scale", "add": "shift", "remove": "exclude",
               "drop": "exclude", "filter_out": "exclude", "max": "cap", "min": "floor", "rows": "volume"}
    kind = aliases.get(kind, kind)
    if kind not in LEVER_TYPES:
        raise WhatIfError(f"Unknown lever type {raw.get('type')!r}. Use one of: {', '.join(LEVER_TYPES)}.")
    lever = Lever(type=kind)

    if kind in COLUMN_LEVERS:
        reference = raw.get("column")
        if reference in (None, "", "value", "@value", "value per row"):
            if metric.kind not in ("sum", "avg"):
                raise WhatIfError(f"A {kind} lever without a column changes the value per row, "
                                  "which needs a SUM or AVG metric. Name the column to change.")
        else:
            lever.table, lever.column = _locate(graph, reference, "Lever column")
            data_type = _column_type(graph, lever.table, lever.column)
            if data_type and not _NUMERIC_TYPE.search(data_type):
                raise WhatIfError(f"{lever.target} is {data_type}, not a number, so it cannot be changed by arithmetic.")

    if kind in ("scale", "volume"):
        lever.percent = _number(raw.get("percent", raw.get("pct")), f"The {kind} percent", *PERCENT_RANGE)
    if kind == "shift":
        lever.amount = _number(raw.get("amount"), "The shift amount")
    if kind == "set":
        lever.value = _number(raw.get("value"), "The new value")
    if kind in ("cap", "floor"):
        lever.limit = _number(raw.get("limit", raw.get("value")), f"The {kind} limit")
    if kind == "scale" and raw.get("elasticity") not in (None, ""):
        lever.elasticity = _number(raw.get("elasticity"), "Elasticity", *ELASTICITY_RANGE)

    lever.segment = _segment(raw.get("where") or raw.get("segment"), graph, metric, filters, filter_tables, engine, notes)
    if kind == "exclude" and lever.segment is None:
        raise WhatIfError("An exclude lever needs a segment: which rows to remove.")
    if metric.kind == "general" and (kind == "volume" or lever.elasticity or (kind in COLUMN_LEVERS and not lever.column)):
        raise WhatIfError("This metric is a ratio or a distinct count, so row-volume changes cannot be applied "
                          "to it. Change a column instead, or use a SUM, COUNT or AVG metric.")
    return lever


def verify_scenario(raw: Dict[str, Any], schema: Dict[str, Any], engine: Optional[Engine] = None,
                    need_time: bool = False) -> Scenario:
    if not isinstance(raw, dict) or not raw.get("metric_expression"):
        raise WhatIfError("Could not tell which number to change. Name the metric (for example revenue or orders).")
    notes: List[str] = []
    tables = {t.get("table_name") for t in schema.get("tables", [])}

    fact = _split(raw.get("fact_table"))[1] or ""
    if fact not in tables:
        match = re.search(r"\b(\w+)\.\w+", str(raw["metric_expression"]))
        fact = match.group(1) if match and match.group(1) in tables else ""
    if not fact:
        raise WhatIfError("Could not identify the table the metric is computed from.")
    try:
        graph = SchemaGraph(schema, fact)
    except InvestigationError as exc:
        raise WhatIfError(str(exc)) from exc

    metric = _metric(str(raw.get("metric_name") or ""), str(raw["metric_expression"]), graph)
    unreachable = [table for table in metric.tables if not graph.reachable(table)]
    if unreachable:
        # The named fact table cannot reach the metric's columns: the metric's own table is the fact.
        notes.append(f"{fact} does not hold the metric's rows; using {unreachable[0]} as the main table.")
        graph = SchemaGraph(schema, unreachable[0])
        metric = _metric(metric.name, str(raw["metric_expression"]), graph)
        _require_reachable(graph, metric.tables, "The metric")

    filters, filter_tables = "", []
    raw_filters = raw.get("filters")
    if isinstance(raw_filters, (dict, list)) and raw_filters:
        # Structured filters (same shape as a lever's "where"), all of them must hold.
        conditions = [_segment(item, graph, metric, "", [], engine, notes)
                      for item in (raw_filters if isinstance(raw_filters, list) else [raw_filters])]
        conditions = [item for item in conditions if item is not None]
        filters = " AND ".join(f"({item.sql()})" for item in conditions)
        filter_tables = list(dict.fromkeys(item.table for item in conditions))
    elif raw_filters and str(raw_filters).strip().lower() not in ("null", "none", ""):
        try:
            filters, filter_tables, tree = _qualify_expression(str(raw_filters), graph, "filter")
        except InvestigationError as exc:
            raise WhatIfError(str(exc)) from exc
        if tree.find(exp.AggFunc):
            raise WhatIfError("A filter cannot contain an aggregate.")
    _require_reachable(graph, filter_tables, "The filter")

    levers_raw = raw.get("levers") or []
    if not isinstance(levers_raw, list):
        raise WhatIfError("levers must be a list.")
    if len(levers_raw) > MAX_LEVERS:
        raise WhatIfError(f"At most {MAX_LEVERS} changes can be combined in one scenario.")
    levers = [_lever(item, graph, metric, filters, filter_tables, engine, notes) for item in levers_raw]

    grain = str(raw.get("grain") or "month").lower()
    if grain not in GRAINS:
        notes.append(f"Unknown period {grain!r}; using month.")
        grain = "month"
    periods = int(_number(raw.get("forecast_periods") or 0, "Forecast periods", 0, 36))

    time = None
    if raw.get("time_column") or need_time or periods:
        try:
            time = _choose_time_column(graph, raw.get("time_column"), notes)
        except InvestigationError as exc:
            if need_time or periods:
                raise WhatIfError(str(exc)) from exc

    used = [(lever.table, lever.column) for lever in levers if lever.column]
    used += re.findall(r'"([^"]+)"\."([^"]+)"', metric.expression)
    breakdown = None
    if raw.get("breakdown"):
        located = _resolve(graph, *_split(raw["breakdown"]))
        if located and graph.reachable(located[0]) and _is_dimension(graph, *located):
            breakdown = located
        else:
            notes.append(f"Breakdown {raw['breakdown']!r} is not a category column; picked another.")
    if breakdown is None:
        # A segment column is the most telling breakdown (where the change happens).
        for lever in levers:
            if lever.segment and _is_dimension(graph, lever.segment.table, lever.segment.column):
                breakdown = (lever.segment.table, lever.segment.column)
                break
    if breakdown is None:
        candidates = _dimensions(graph, [], used + ([time] if time else []), [])
        breakdown = candidates[0] if candidates else None

    return Scenario(graph=graph, metric=metric, levers=levers, time=time, grain=grain, filters=filters,
                    filter_tables=filter_tables, breakdown=breakdown, forecast_periods=periods, notes=notes)
