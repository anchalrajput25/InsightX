# backend/investigation/planner.py
#
# Turns "why did revenue drop in August?" into a verified Plan.
#
# The LLM proposes the metric (an aggregate SQL expression), the fact
# table, the time column, the periods, breakdown dimensions and
# filters. Nothing is trusted: every column must exist, every table
# must be reachable from the fact table through many-to-one joins (so
# breakdowns can never double-count), the metric must aggregate, and
# the trend query is validated (EXPLAIN + stored-value check) before
# any analysis runs. Missing parts are filled deterministically.

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import sqlglot
from sqlglot import exp
from sqlalchemy.engine import Engine

from backend.investigation.engine import (
    GRAINS,
    MAX_DIMENSIONS,
    InvestigationError,
    Metric,
    Plan,
    SchemaGraph,
    col_sql,
)
from backend.llm_service import get_llm
from backend import dialects

llm = get_llm(job="investigation_plan")

WHY_RE = re.compile(
    r"\b(why|reason|reasons|cause[ds]?|caus(?:e|ing)|explain|driv(?:er|ers|ing|en)|what happened|root cause|"
    r"how come|kyu|kyun|kyon|kyo|kaise)\b",
    flags=re.IGNORECASE,
)
CHANGE_RE = re.compile(
    r"\b(drop(?:ped|ping|s)?|dip\w*|decreas\w*|declin\w*|fell|fall\w*|down|lower|low|less|fewer|"
    r"los[st]\w*|increas\w*|ris(?:e|en|ing)|rose|grow\w*|grew|up|higher|high|spik\w*|jump\w*|chang\w*|"
    r"poor\w*|bad|worse|weak\w*|slow\w*|leav\w*|churn\w*|kam|zyada|jyada|gir\w*|badh\w*|ghat\w*)\b",
    flags=re.IGNORECASE,
)
# "How can I improve sales?" asks what to do about a metric: the same
# investigation (latest period vs the one before) answers it.
IMPROVE_RE = re.compile(
    r"\b(?:how\s+(?:can|could|do|should|to|would)\b.{0,40}?\b(?:improve|increase|boost|grow|raise|reduce|fix|recover)"
    r"|ways?\s+to\s+(?:improve|increase|boost|grow|raise|reduce)"
    r"|kaise\s+(?:badh|sudhar|improve|increase)\w*"
    r"|(?:improve|increase|boost)\w*\s+(?:my\s+|our\s+|the\s+)?(?:sales|revenue|orders|profit|customers)\b)",
    flags=re.IGNORECASE,
)
MAX_DIMENSION_CARDINALITY = 5000

PROMPT = """
You plan a root-cause investigation on a PostgreSQL database. The user
asks why a metric changed, or how to improve it (then compare the latest
full period with the one before it). Use ONLY the tables and columns below.

Return ONLY one JSON object:
{{
  "metric_name": "<short name, e.g. revenue>",
  "metric_expression": "<ONE SQL aggregate over table.column references, e.g. SUM(t.price) or COUNT(t.id) or AVG(t.score)>",
  "fact_table": "<the table whose rows the metric aggregates>",
  "time_column": "<table.column with the date/time of each fact row>",
  "grain": "day|week|month|quarter|year",
  "current_period": "<YYYY-MM-DD start of the period asked about, or null>",
  "baseline_period": "<YYYY-MM-DD start of the period to compare with, or null>",
  "direction": "decrease|increase|change",
  "dimensions": ["<table.column categories that could explain the change>", ...],
  "filters": "<SQL condition the question restricts to, or null>"
}}
Rules: write columns as table.column; no subqueries; filters use stored
values exactly as listed; prefer month unless the question implies
otherwise; give 3-6 dimensions (categories, regions, channels, types).

QUESTION: {question}

SCHEMA:
{schema}
"""


def looks_like_investigation(question: str) -> bool:
    """'Why did X drop?' style questions (routing hint for the UI/API)."""
    question = question or ""
    return (bool(WHY_RE.search(question)) and bool(CHANGE_RE.search(question))) or bool(IMPROVE_RE.search(question))


# ============================================================
# SCHEMA CONTEXT FOR THE LLM
# ============================================================

def describe_for_planning(schema: Dict[str, Any], question: str) -> str:
    from backend.text_to_sql.schema_focus import build_schema_focus

    focus = build_schema_focus(schema, [question], question, top_k=8)
    tables = focus["focused_schema"].get("tables", [])
    lines = []
    for table in tables:
        lines.append(f"TABLE {table['table_name']}")
        for column in table.get("columns", []):
            entry = f"  - {column.get('column_name')} {column.get('data_type', '')}"
            if column.get("stored_values"):
                entry += "  values: " + ", ".join(repr(v) for v in column["stored_values"][:8])
            lines.append(entry)
    relationships = focus["focused_schema"].get("relationships") or []
    if relationships:
        lines.append("RELATIONSHIPS")
        lines += [f"  - {r['source_table']}.{r['source_column']} = {r['target_table']}.{r['target_column']}"
                  for r in relationships]
    return "\n".join(lines)[:20000]


# ============================================================
# VERIFICATION
# ============================================================

def _split(reference: Any) -> Tuple[str, str]:
    text = str(reference or "").strip().strip('"')
    parts = [part.strip().strip('"') for part in text.split(".")]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", parts[0] if parts else ""


def _column_exists(graph: SchemaGraph, table: str, column: str) -> bool:
    return graph.column(table, column) is not None


def _resolve(graph: SchemaGraph, table: str, column: str) -> Optional[Tuple[str, str]]:
    """Locate a column: as written, else the one reachable table that has it."""
    if table and _column_exists(graph, table, column):
        return table, column
    # graph.paths is ordered by distance from the fact table, so the
    # first owner is the nearest (aliases like "pr." are not table names).
    owners = [name for name in graph.paths if _column_exists(graph, name, column)]
    if table:
        owners = [name for name in owners if name.lower() == table.lower()] or owners
    return (owners[0], column) if owners else None


def _qualify_expression(text: str, graph: SchemaGraph, what: str) -> Tuple[str, List[str], exp.Expression]:
    """Parse, check columns (reachable, existing) and rewrite them as "table"."column"."""
    try:
        tree = sqlglot.parse_one(text, read=dialects.sqlglot_name())
    except Exception as exc:
        raise InvestigationError(f"The {what} is not valid SQL: {str(exc).splitlines()[0]}") from exc
    if tree.find(exp.Select) or tree.find(exp.Subquery):
        raise InvestigationError(f"The {what} cannot contain a subquery.")
    tables: List[str] = []
    for column in list(tree.find_all(exp.Column)):
        located = _resolve(graph, column.table, column.name)
        if located is None:
            raise InvestigationError(
                f"The {what} uses {column.sql()}, which does not exist in a table that can be joined "
                f"to {graph.fact} without duplicating rows."
            )
        table, name = located
        if table not in tables:
            tables.append(table)
        column.replace(exp.column(name, table=table, quoted=True))
    for table_node in tree.find_all(exp.Table):
        raise InvestigationError(f"The {what} cannot reference a table directly ({table_node.sql()}).")
    tree = _intervals_to_days(tree, graph)
    return tree.sql(dialect=dialects.sqlglot_name()), tables, tree


def _column_type(graph: SchemaGraph, node: exp.Expression) -> str:
    column = node if isinstance(node, exp.Column) else node.find(exp.Column)
    if column is None or not isinstance(node, (exp.Column, exp.Cast, exp.Paren)):
        return ""
    if isinstance(node, exp.Cast):
        return node.to.sql().lower()
    for item in graph.tables.get(column.table, {}).get("columns", []):
        if item.get("column_name") == column.name:
            return str(item.get("data_type", "")).lower()
    return ""


def _intervals_to_days(tree: exp.Expression, graph: SchemaGraph) -> exp.Expression:
    """timestamp - timestamp is an INTERVAL in SQL, not a number: express it in days."""
    for sub in list(tree.find_all(exp.Sub)):
        if sub.find_ancestor(exp.Extract):
            continue
        left, right = _column_type(graph, sub.left), _column_type(graph, sub.right)
        # date - date is already a number of days on PostgreSQL (not on MySQL).
        if "time" in left or "time" in right or dialects.current() == dialects.MYSQL:
            if ("date" in left or "time" in left) and ("date" in right or "time" in right):
                name = dialects.sqlglot_name()
                seconds = dialects.seconds_between(sub.left.sql(dialect=name), sub.right.sql(dialect=name))
                days = sqlglot.parse_one(f"{seconds} / 86400.0", read=name)
                sub.replace(days)
    return tree


def _metric(name: str, expression: str, graph: SchemaGraph) -> Metric:
    sql, tables, tree = _qualify_expression(expression, graph, "metric")
    aggregates = list(tree.find_all(exp.AggFunc))
    if not aggregates:
        raise InvestigationError("The metric must aggregate (SUM, COUNT, AVG, ...).")
    if isinstance(tree, (exp.Sum, exp.Count)) and not tree.find(exp.Distinct):
        kind, argument = "additive", ""
    elif isinstance(tree, exp.Avg):
        kind, argument = "average", tree.this.sql(dialect=dialects.sqlglot_name())
    else:
        kind, argument = "ratio", ""
    if not tables:
        tables = [graph.fact]
    return Metric(name=name or "metric", expression=sql, kind=kind, tables=tables, average_argument=argument)


def _is_temporal(column: Optional[Dict[str, Any]]) -> bool:
    data_type = str((column or {}).get("data_type", "")).lower()
    return "date" in data_type or "time" in data_type


def _choose_time_column(graph: SchemaGraph, proposed: Any, notes: List[str]) -> Tuple[str, str]:
    table, column = _split(proposed)
    located = _resolve(graph, table, column) if column else None
    if located and _is_temporal(graph.column(*located)):
        return located
    for table_name in graph.paths:  # fact table first, then N:1 tables
        for item in graph.tables[table_name].get("columns", []):
            if _is_temporal(item):
                if proposed:
                    notes.append(f"Time column {proposed!r} was not usable; using {table_name}.{item['column_name']}.")
                return table_name, item["column_name"]
    raise InvestigationError(f"No date/time column is reachable from {graph.fact}.")


def _is_dimension(graph: SchemaGraph, table: str, column: str) -> bool:
    item = graph.column(table, column) or {}
    data_type = str(item.get("data_type", "")).lower()
    if _is_temporal(item) or re.search(r"(^|_)(id|key)$", column):
        return False
    # Locations codes, contact details, coordinates and free text are not
    # useful breakdowns, whatever their cardinality.
    if re.search(r"(zip|postal|postcode|prefix|phone|fax|email|mail|address|street|url|lat|lng|lon|"
                 r"latitude|longitude|comment|message|description|title|note)", column.lower()):
        return False
    distinct = item.get("distinct_count")
    # Unique per fact row = an identifier. In a lookup table reached by a
    # many-to-one join, the label column is naturally unique.
    if item.get("unique") and table == graph.fact:
        return False
    if any(word in data_type for word in ("char", "text", "bool")):
        return distinct is None or 1 < distinct <= MAX_DIMENSION_CARDINALITY
    # small integer codes (ratings, levels) are categorical too
    return isinstance(distinct, int) and 1 < distinct <= 12


def _dimensions(graph: SchemaGraph, proposed: List[Any], used: List[Tuple[str, str]], notes: List[str]) -> List[Tuple[str, str]]:
    # Join keys of the N:1 paths duplicate the column they join from.
    join_keys = {(right_table, right_col) for path in graph.paths.values() for _, _, right_table, right_col in path}
    used = list(used) + sorted(join_keys)
    chosen: List[Tuple[str, str]] = []
    for reference in proposed or []:
        located = _resolve(graph, *_split(reference))
        if located and located not in chosen and located not in used and _is_dimension(graph, *located):
            chosen.append(located)
        elif reference:
            table_name, column_name = _split(reference)
            if located is None and any(_column_exists(graph, t, column_name) for t in graph.tables):
                reason = (f"its table can only be joined to {graph.fact} through a one-to-many join, "
                          f"which would count {graph.fact} rows more than once")
            elif located is None:
                reason = "the column does not exist"
            else:
                reason = "it is not a categorical column"
            notes.append(f"Breakdown {column_name!r} skipped: {reason}.")
    # Fill with categorical columns: low cardinality first, fact table first.
    extra = []
    for depth, table_name in enumerate(graph.paths):
        for item in graph.tables[table_name].get("columns", []):
            key = (table_name, item.get("column_name"))
            if key in chosen or key in used or not _is_dimension(graph, *key):
                continue
            extra.append((item.get("distinct_count") or 10**6, depth, key))
    for _, _, key in sorted(extra):
        if len(chosen) >= MAX_DIMENSIONS:
            break
        chosen.append(key)
    # A lookup table joined ON a chosen dimension's value only relabels
    # it (code -> name, name -> translation): keep the readable label.
    relabelled = set()
    for table, _ in chosen:
        path = graph.paths.get(table) or []
        if path:
            left_table, left_col, _, _ = path[-1]
            if (left_table, left_col) in chosen and table != left_table:
                relabelled.add((left_table, left_col))
    for key in relabelled:
        notes.append(f"{key[0]}.{key[1]} is shown through its label in a lookup table.")
    return [key for key in chosen if key not in relabelled][:MAX_DIMENSIONS]


def verify_plan(raw: Dict[str, Any], schema: Dict[str, Any], question: str, engine: Optional[Engine] = None) -> Plan:
    if not isinstance(raw, dict) or not raw.get("metric_expression"):
        raise InvestigationError("Could not identify which metric to investigate. Name the metric (e.g. revenue, orders).")
    notes: List[str] = []
    tables = {t.get("table_name") for t in schema.get("tables", [])}

    fact = _split(raw.get("fact_table"))[1] or ""
    if fact not in tables:
        # the table of the metric's first column
        match = re.search(r"\b(\w+)\.\w+", str(raw["metric_expression"]))
        fact = match.group(1) if match and match.group(1) in tables else ""
    if not fact:
        raise InvestigationError("Could not identify the table the metric is computed from.")
    graph = SchemaGraph(schema, fact)

    metric = _metric(str(raw.get("metric_name") or ""), str(raw["metric_expression"]), graph)
    time_table, time_column = _choose_time_column(graph, raw.get("time_column"), notes)

    filters, filter_tables = "", []
    if raw.get("filters") and str(raw["filters"]).strip().lower() not in ("null", "none", ""):
        filters, filter_tables, tree = _qualify_expression(str(raw["filters"]), graph, "filter")
        if tree.find(exp.AggFunc):
            raise InvestigationError("A filter cannot contain an aggregate.")

    grain = str(raw.get("grain") or "month").lower()
    if grain not in GRAINS:
        notes.append(f"Unknown period {grain!r}; using month.")
        grain = "month"
    direction = str(raw.get("direction") or "change").lower()
    if direction not in ("decrease", "increase", "change"):
        direction = "change"

    # The time column and the metric's own columns are never breakdowns
    # (an average broken down by its own values is circular).
    used = [(time_table, time_column)] + [
        (table, column)
        for table, column in re.findall(r'"([^"]+)"\."([^"]+)"', metric.expression)
    ]
    plan = Plan(
        question=question,
        fact_table=fact,
        metric=metric,
        time_table=time_table,
        time_column=time_column,
        grain=grain,
        current_period=_date_or_none(raw.get("current_period")),
        baseline_period=_date_or_none(raw.get("baseline_period")),
        direction=direction,
        dimensions=_dimensions(graph, raw.get("dimensions") or [], used, notes),
        filters=filters,
        filter_tables=filter_tables,
        notes=notes,
    )

    if engine is not None:
        # Dry-run the trend query: EXPLAIN + stored-value check of literals.
        from backend.text_to_sql.sql_validator import validate_sql

        tables_needed = list(dict.fromkeys([time_table, *metric.tables, *filter_tables]))
        where = f"{col_sql(time_table, time_column)} IS NOT NULL" + (f" AND ({filters})" if filters else "")
        sql = (f"SELECT {dialects.period(grain, col_sql(time_table, time_column))}, {metric.expression} "
               f"FROM {graph.from_clause(tables_needed)} WHERE {where} GROUP BY 1")
        validation = validate_sql(sql, engine=engine)
        if not validation.get("valid"):
            raise InvestigationError("The investigation query is not valid: " + "; ".join(validation.get("errors") or [])[:500])
    return plan


def _date_or_none(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text if re.match(r"^\d{4}(-\d{1,2})?(-\d{1,2})?$", text) else None


def plan_investigation(question: str, schema: Dict[str, Any], engine: Optional[Engine] = None, model=None) -> Tuple[Plan, str]:
    """(verified plan, raw LLM output)."""
    messages = PROMPT.format(question=question.strip(), schema=describe_for_planning(schema, question))
    response = (model or llm).invoke(messages)
    raw = str(getattr(response, "content", response))
    parsed = _parse(raw)
    return verify_plan(parsed, schema, question, engine), raw


def _parse(raw: str) -> Optional[Dict[str, Any]]:
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL | re.IGNORECASE)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    decoder = json.JSONDecoder()
    for start in [m.start() for m in re.finditer(r"\{", text)]:
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "metric_expression" in value:
            return value
    return None
