# backend/alerting/metrics.py
#
# What an alert watches: a SQL query whose result is either a time
# series (a date column + a number column, e.g. orders per day) or one
# number (e.g. orders still waiting to ship). No AI here: suggested
# numbers come from the schema, the same way the dashboard finds them.

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.engine import Engine

from backend import dialects
from backend.alerting.detect import GRAINS, fill_gaps, infer_grain

MAX_ROWS = 5000
MAX_SUGGESTIONS = 12
GRAIN_WORDS = {"day": "day", "week": "week", "month": "month", "quarter": "quarter", "year": "year"}


class MetricError(ValueError):
    """The query result cannot be watched (no number, or no date and many rows)."""


# ------------------------------------------------------------
# suggested numbers (no AI)
# ------------------------------------------------------------

def suggestions(engine: Engine, schema: Dict[str, Any], grain: str = "day") -> List[Dict[str, Any]]:
    """Numbers worth watching: records per day, total and average amounts per day."""
    from backend import dashboard
    from backend.investigation.engine import col_sql

    grain = grain if grain in GRAINS else "day"
    with dialects.using(engine):
        sizes = dashboard.table_sizes(engine, schema)
        tables = {t["table_name"]: t for t in schema.get("tables", [])}
        out: List[Dict[str, Any]] = []
        for fact in sorted(tables, key=lambda name: -sizes.get(name, 0)):
            if not sizes.get(fact) or len(out) >= MAX_SUGGESTIONS:
                continue
            graph = dashboard._graph(schema, fact)
            if graph is None:
                continue
            # the table's own date, or its parent's (order items -> the order's date)
            near = sorted((t for t in graph.paths if len(graph.paths[t]) <= 1), key=lambda t: len(graph.paths[t]))
            date_at = next(((t, d) for t in near if (d := dashboard._event_date(tables[t]))), None)
            if not date_at:
                continue
            period = dialects.period(grain, col_sql(*date_at))
            source = graph.from_clause([date_at[0]])
            where = f"{col_sql(*date_at)} IS NOT NULL"
            noun = dashboard._label(fact)

            def add(key, name, how, value_sql, fill):
                body = (f"{period} AS {dialects.quote('period')}, {value_sql} AS {dialects.quote('value')} "
                        f"FROM {source} WHERE {where} GROUP BY {period} ORDER BY 1 DESC")
                out.append({"key": key, "name": name, "description": how, "grain": grain, "fill_gaps": fill,
                            "sql": dialects.select_limited(body, MAX_ROWS)})

            add(f"count-{fact}", f"{noun.capitalize()} per {grain}",
                f"Number of {noun} records, by {dashboard._label(date_at[1])}", "COUNT(*)", True)
            for measure in dashboard._measures(tables[fact])[:2]:
                add(f"sum-{fact}-{measure}", f"Total {dashboard._label(measure)} per {grain}",
                    f"Sum of {fact}.{measure}", f"SUM({col_sql(fact, measure)})", True)
                add(f"avg-{fact}-{measure}", f"Average {dashboard._label(measure)} per {grain}",
                    f"Average of {fact}.{measure}", f"AVG({col_sql(fact, measure)})", False)
    return out[:MAX_SUGGESTIONS]


# ------------------------------------------------------------
# query result -> points
# ------------------------------------------------------------

def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        return None if math.isnan(number) else number
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _when(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, (bool, int, float, Decimal)):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    if len(text) < 6 or not text[:4].isdigit():
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "").replace("T", " ")[:26]).replace(tzinfo=None)
    except ValueError:
        pass
    for pattern in ("%Y-%m", "%Y/%m/%d", "%Y/%m"):
        try:
            return datetime.strptime(text[:10], pattern)
        except ValueError:
            continue
    return None


def _share(rows: List[Dict[str, Any]], column: str, test) -> float:
    values = [row.get(column) for row in rows if row.get(column) is not None]
    return sum(1 for v in values if test(v) is not None) / len(values) if values else 0.0


def to_points(columns: List[str], rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
    """(points oldest first, single). single: the query gives one number, not a series."""
    if not rows:
        return [], False
    columns = list(columns or rows[0].keys())
    sample = rows[:200]
    date_column = next((c for c in columns if _share(sample, c, _when) >= 0.8), None)
    numbers = [c for c in columns if c != date_column and _share(sample, c, _number) >= 0.8]
    if not numbers:
        raise MetricError("The result has no number to watch.")
    value_column = numbers[-1] if date_column is None and len(numbers) > 1 else numbers[0]
    if date_column is None:
        if len(rows) != 1:
            raise MetricError("The result needs a date column (like orders per day) or exactly one number.")
        value = _number(rows[0].get(value_column))
        return ([] if value is None else [{"t": None, "v": value}]), True
    totals: Dict[datetime, float] = {}
    for row in rows:
        when, value = _when(row.get(date_column)), _number(row.get(value_column))
        if when is not None and value is not None:
            totals[when] = totals.get(when, 0.0) + value
    return [{"t": t, "v": v} for t, v in sorted(totals.items())], False


def read_series(sql: str, engine: Engine, fill: bool = False) -> Dict[str, Any]:
    """Run the metric's SQL (read-only, safety-checked) and turn it into points."""
    from backend.text_to_sql.query_executor import execute_query
    from backend.text_to_sql.sql_safety import check_sql_safety

    safety = check_sql_safety(sql)
    if not safety.get("safe"):
        raise MetricError(f"This query is not allowed: {safety.get('reason') or 'not a read-only SELECT'}.")
    with dialects.using(engine):
        result = execute_query(sql, engine, max_rows=MAX_ROWS)
    if not result.get("success"):
        raise MetricError(f"The query failed: {str(result.get('error') or 'unknown error')[:300]}")
    points, single = to_points(result.get("columns") or [], result.get("rows") or [])
    grain = None if single else infer_grain([p["t"] for p in points])
    if fill and not single:
        points = fill_gaps(points, grain)
    return {"points": points, "single": single, "grain": grain}
