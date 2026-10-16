# backend/investigation/engine.py
#
# Deterministic "why did the metric change?" analysis. No LLM here:
# every number comes from read-only SQL and plain arithmetic, so the
# narrative layered on top can be checked against these facts.
#
#   1. trend      metric per period, normal period-over-period variation
#   2. headline   current vs baseline period, change, how unusual it is
#   3. drivers    per dimension, each segment's contribution to the change
#                 - SUM / COUNT  : additive, contributions sum to the change
#                 - AVG          : mix effect (segment weights shifted) +
#                                  rate effect (segment values changed)
#                 - other ratios : per-segment change only
#   4. volume     SUM = records x value per record: which one moved?
#   5. drill-down top segment of the best dimension, broken down by the
#                 next dimension (heatmap)
#   6. signals    other numbers, time gaps and status shares that changed
#                 overall and inside the top segment (hypothesis evidence)
#
# Joins only follow many-to-one relationships away from the fact table
# (to a unique key), so no join can multiply fact rows (no fan-out).

import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.engine import Engine

from backend import dialects
from backend.text_to_sql.query_executor import execute_query
from backend.text_to_sql.sql_safety import check_sql_safety

GRAINS = ("day", "week", "month", "quarter", "year")
MAX_SEGMENTS_SHOWN = 8
MAX_DIMENSIONS = 8
MAX_JOIN_DEPTH = 3
PARTIAL_PERIOD_RATIO = 0.5
MAX_SIGNAL_COLUMNS = 10
MAX_TIME_GAPS = 4
MAX_STATUS_COLUMNS = 3
MAX_STATUS_VALUES = 12
MIN_SEGMENT_SHARE_FOR_RATE = 0.01


class InvestigationError(ValueError):
    pass


# ============================================================
# SCHEMA GRAPH: many-to-one joins away from the fact table
# ============================================================

def _q(identifier: str) -> str:
    return dialects.quote(identifier)


def col_sql(table: str, column: str) -> str:
    return f"{_q(table)}.{_q(column)}"


def _is_key_column(table: Dict[str, Any], column_name: str) -> bool:
    if column_name in (table.get("primary_key") or []) and len(table.get("primary_key") or []) == 1:
        return True
    for column in table.get("columns", []):
        if column.get("column_name") == column_name:
            return bool(column.get("unique"))
    return False


class SchemaGraph:
    """Tables reachable from the fact table through N:1 joins only."""

    def __init__(self, schema: Dict[str, Any], fact_table: str):
        self.schema = schema
        self.tables = {t["table_name"]: t for t in schema.get("tables", []) if t.get("table_name")}
        if fact_table not in self.tables:
            raise InvestigationError(f"Table {fact_table!r} does not exist.")
        self.fact = fact_table
        # table -> list of joins from the fact table to reach it
        self.paths: Dict[str, List[Tuple[str, str, str, str]]] = {fact_table: []}
        frontier = [fact_table]
        for _ in range(MAX_JOIN_DEPTH):
            next_frontier = []
            for current in frontier:
                for left_table, left_col, right_table, right_col in self._edges(current):
                    if right_table in self.paths:
                        continue
                    self.paths[right_table] = self.paths[current] + [(left_table, left_col, right_table, right_col)]
                    next_frontier.append(right_table)
            frontier = next_frontier

    def _edges(self, table: str):
        """Joins from `table` to a table whose join column is unique (N:1)."""
        for relation in self.schema.get("relationships", []):
            pairs = [
                (relation.get("source_table"), relation.get("source_column"),
                 relation.get("target_table"), relation.get("target_column")),
                (relation.get("target_table"), relation.get("target_column"),
                 relation.get("source_table"), relation.get("source_column")),
            ]
            for left_table, left_col, right_table, right_col in pairs:
                if left_table != table or right_table not in self.tables or right_table == table:
                    continue
                if _is_key_column(self.tables[right_table], right_col):
                    yield left_table, left_col, right_table, right_col

    def reachable(self, table: str) -> bool:
        return table in self.paths

    def from_clause(self, tables: Sequence[str]) -> str:
        joins: List[Tuple[str, str, str, str]] = []
        for table in tables:
            if table not in self.paths:
                raise InvestigationError(f"{table!r} cannot be joined to {self.fact!r} without duplicating rows.")
            for join in self.paths[table]:
                if join not in joins:
                    joins.append(join)
        parts = [_q(self.fact)]
        for left_table, left_col, right_table, right_col in joins:
            # LEFT JOIN keeps every fact row: totals never depend on the breakdown.
            parts.append(f"LEFT JOIN {_q(right_table)} ON {col_sql(left_table, left_col)} = {col_sql(right_table, right_col)}")
        return " ".join(parts)

    def column(self, table: str, column: str) -> Optional[Dict[str, Any]]:
        for item in self.tables.get(table, {}).get("columns", []):
            if item.get("column_name") == column:
                return item
        return None


# ============================================================
# PLAN
# ============================================================

@dataclass
class Metric:
    name: str
    expression: str                 # SQL aggregate, table-qualified columns
    kind: str                       # additive | average | ratio
    tables: List[str]
    average_argument: str = ""      # for kind=average: SQL inside AVG(...)


@dataclass
class Plan:
    question: str
    fact_table: str
    metric: Metric
    time_table: str
    time_column: str
    grain: str = "month"
    current_period: Optional[str] = None
    baseline_period: Optional[str] = None
    direction: str = "change"       # decrease | increase | change
    dimensions: List[Tuple[str, str]] = field(default_factory=list)
    filters: str = ""
    filter_tables: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# ============================================================
# QUERY HELPERS
# ============================================================

def _run(sql: str, engine: Engine, max_rows: int = 5000) -> Dict[str, Any]:
    safety = check_sql_safety(sql)
    if not safety.get("safe"):
        raise InvestigationError("Generated SQL failed the safety gate: " + "; ".join(safety.get("errors") or []))
    result = execute_query(sql, engine, max_rows=max_rows)
    if not result.get("success"):
        raise InvestigationError(f"Query failed: {str(result.get('error'))[:300]}")
    return result


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, timedelta):
        return value.total_seconds() / 86400.0  # durations are reported in days
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _period_key(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _pct(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


class Investigation:
    def __init__(self, plan: Plan, schema: Dict[str, Any], engine: Engine):
        self.plan = plan
        self.engine = engine
        self.graph = SchemaGraph(schema, plan.fact_table)
        self.queries: List[Dict[str, str]] = []
        for table in [plan.time_table, *plan.metric.tables, *plan.filter_tables]:
            if not self.graph.reachable(table):
                raise InvestigationError(f"{table!r} cannot be joined to {plan.fact_table!r} without duplicating rows.")
        self.time_sql = col_sql(plan.time_table, plan.time_column)
        self.period_sql = dialects.period(plan.grain, self.time_sql)

    # ---------------- helpers ----------------
    def _base_tables(self, extra: Sequence[str] = ()) -> List[str]:
        return list(dict.fromkeys([self.plan.time_table, *self.plan.metric.tables, *self.plan.filter_tables, *extra]))

    def _where(self, periods: Optional[Sequence[str]] = None, extra: str = "") -> str:
        conditions = [f"{self.time_sql} IS NOT NULL"]
        if self.plan.filters:
            conditions.append(f"({self.plan.filters})")
        if periods:
            conditions.append(f"{self.period_sql} IN ({', '.join(dialects.timestamp_literal(p) for p in periods)})")
        if extra:
            conditions.append(extra)
        return " AND ".join(conditions)

    def _query(self, purpose: str, sql: str, max_rows: int = 5000) -> Dict[str, Any]:
        self.queries.append({"purpose": purpose, "sql": sql})
        return _run(sql, self.engine, max_rows=max_rows)

    def _measure_columns(self) -> str:
        metric = self.plan.metric
        parts = [f"{metric.expression} AS value", "COUNT(*) AS n"]
        if metric.kind == "average":
            parts += [f"SUM({metric.average_argument}) AS s", f"COUNT({metric.average_argument}) AS c"]
        return ", ".join(parts)

    # ---------------- 1. trend ----------------
    def trend(self) -> Dict[str, Any]:
        sql = (
            f"SELECT {self.period_sql} AS period, {self._measure_columns()} "
            f"FROM {self.graph.from_clause(self._base_tables())} WHERE {self._where()} "
            f"GROUP BY 1 ORDER BY 1"
        )
        rows = self._query("Metric per period (trend)", sql)["rows"]
        points = [
            {"period": _period_key(row["period"]), "value": _num(row["value"]), "n": int(row["n"] or 0)}
            for row in rows
        ]
        if len(points) < 2:
            raise InvestigationError("Fewer than two periods of data: there is no change to explain.")
        counts = [point["n"] for point in points]
        typical = statistics.median(counts)
        for index, point in enumerate(points):
            edge = index in (0, len(points) - 1)
            point["partial"] = bool(edge and point["n"] < PARTIAL_PERIOD_RATIO * typical)
        return {"points": points}

    def choose_periods(self, points: List[Dict[str, Any]]) -> Tuple[str, str, str]:
        """(baseline, current, reason)"""
        available = {point["period"] for point in points}
        wanted_current = self.plan.current_period
        wanted_baseline = self.plan.baseline_period
        if wanted_current:
            wanted_current = _snap(wanted_current, available)
        if wanted_baseline:
            wanted_baseline = _snap(wanted_baseline, available)
        complete = [point for point in points if not point["partial"] and point["value"] is not None]
        ordered = [point["period"] for point in complete]
        if wanted_current and wanted_baseline:
            return wanted_baseline, wanted_current, "periods named in the question"
        if wanted_current and wanted_current in ordered and ordered.index(wanted_current) > 0:
            return ordered[ordered.index(wanted_current) - 1], wanted_current, "period named in the question vs the period before"
        if len(complete) < 2:
            raise InvestigationError("Not enough complete periods to compare.")

        changes = [
            (complete[i - 1]["period"], complete[i]["period"], complete[i]["value"] - complete[i - 1]["value"])
            for i in range(1, len(complete))
        ]
        latest = changes[-1]
        if self.plan.direction in ("decrease", "increase"):
            sign = -1 if self.plan.direction == "decrease" else 1
            if latest[2] * sign <= 0:
                recent = [change for change in changes[-6:] if change[2] * sign > 0]
                if recent:
                    best = max(recent, key=lambda change: abs(change[2]))
                    return best[0], best[1], (
                        f"the latest change was not a {self.plan.direction}; using the largest recent {self.plan.direction}"
                    )
        return latest[0], latest[1], "latest complete period vs the one before"

    # ---------------- 2. headline ----------------
    def headline(self, points: List[Dict[str, Any]], baseline: str, current: str) -> Dict[str, Any]:
        by_period = {point["period"]: point for point in points}
        old, new = by_period[baseline]["value"], by_period[current]["value"]
        if old is None or new is None:
            missing = " and ".join(period_label(p, self.plan.grain) for p, v in ((baseline, old), (current, new)) if v is None)
            raise InvestigationError(
                f"The metric '{self.plan.metric.name}' has no numeric value for {missing} "
                f"(expression: {self.plan.metric.expression}). Check that the period has data, "
                "or rephrase the metric."
            )
        # Normal variation, robustly: only periods with normal volume
        # (sparse start-up periods produce absurd % changes), median and
        # MAD instead of mean and standard deviation, last 24 changes.
        typical_n = statistics.median([point["n"] for point in points]) or 1
        regular = [point for point in points
                   if not point["partial"] and point["value"] is not None and point["n"] >= 0.2 * typical_n]
        changes = [
            _pct(regular[i]["value"], regular[i - 1]["value"])
            for i in range(1, len(regular))
            if regular[i]["period"] != current
        ]
        changes = [change for change in changes if change is not None][-24:]
        change_pct = _pct(new, old)
        typical = z = None
        if len(changes) >= 3 and change_pct is not None:
            center = statistics.median(changes)
            spread = 1.4826 * statistics.median([abs(c - center) for c in changes])
            typical = statistics.median([abs(c) for c in changes])
            z = (change_pct - center) / spread if spread else None
        if z is None and typical is not None and change_pct is not None and change_pct != statistics.median(changes):
            unusual = "unusual"  # history was perfectly flat, so any change stands out
        elif z is None:
            unusual = "unknown"
        elif abs(z) >= 2:
            unusual = "unusual"
        elif abs(z) >= 1:
            unusual = "notable"
        else:
            unusual = "normal"
        return {
            "metric": self.plan.metric.name,
            "baseline_period": baseline,
            "current_period": current,
            "baseline_label": period_label(baseline, self.plan.grain),
            "current_label": period_label(current, self.plan.grain),
            "baseline": _round(old),
            "current": _round(new),
            "change": _round(None if old is None or new is None else new - old),
            "change_pct": _round(change_pct, 2),
            "typical_change_pct": _round(typical, 2),
            "z": _round(z, 2),
            "unusual": unusual,
            "direction": "decrease" if (new or 0) < (old or 0) else "increase" if (new or 0) > (old or 0) else "flat",
        }

    # ---------------- 3. drivers ----------------
    def breakdown(self, table: str, column: str, baseline: str, current: str, extra_where: str = "") -> Dict[str, Any]:
        segment_sql = f"COALESCE({dialects.cast_text(col_sql(table, column))}, '(missing)')"
        sql = (
            f"SELECT {segment_sql} AS segment, {self.period_sql} AS period, {self._measure_columns()} "
            f"FROM {self.graph.from_clause(self._base_tables([table]))} "
            f"WHERE {self._where([baseline, current], extra_where)} GROUP BY 1, 2"
        )
        rows = self._query(f"Change by {table}.{column}", sql, max_rows=200_000)["rows"]
        return self._contributions(rows, baseline, current, f"{table}.{column}")

    def _contributions(self, rows, baseline: str, current: str, label: str) -> Dict[str, Any]:
        segments: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = "current" if _period_key(row["period"]) == current else "baseline"
            entry = segments.setdefault(str(row["segment"]), {"segment": str(row["segment"])})
            entry[key] = _num(row["value"])
            entry[f"n_{key}"] = int(row["n"] or 0)
            if "s" in row:
                entry[f"s_{key}"] = _num(row["s"]) or 0.0
                entry[f"c_{key}"] = int(row["c"] or 0)

        kind = self.plan.metric.kind
        items = list(segments.values())
        for item in items:
            item.setdefault("baseline", None)
            item.setdefault("current", None)
            item["change_pct"] = _round(_pct(item["current"], item["baseline"]), 2)

        if kind == "additive":
            for item in items:
                item["contribution"] = (item["current"] or 0.0) - (item["baseline"] or 0.0)
        elif kind == "average":
            c0 = sum(item.get("c_baseline", 0) for item in items) or 1
            c1 = sum(item.get("c_current", 0) for item in items) or 1
            for item in items:
                w0, w1 = item.get("c_baseline", 0) / c0, item.get("c_current", 0) / c1
                r0 = item["s_baseline"] / item["c_baseline"] if item.get("c_baseline") else None
                r1 = item["s_current"] / item["c_current"] if item.get("c_current") else None
                overall_r0 = sum(i.get("s_baseline", 0) for i in items) / c0
                mix = (w1 - w0) * (r0 if r0 is not None else overall_r0)
                rate = w1 * (r1 - r0) if (r0 is not None and r1 is not None) else 0.0
                item["mix_effect"], item["rate_effect"] = mix, rate
                item["contribution"] = mix + rate
                item["weight_baseline"], item["weight_current"] = _round(w0), _round(w1)
        else:
            for item in items:
                item["contribution"] = None

        total_change = None
        if kind in ("additive", "average"):
            total_change = sum(item["contribution"] for item in items)
            sign = -1 if total_change < 0 else 1
            # Expected contribution if every segment changed in proportion
            # to its size; the excess is what makes a segment a driver.
            # (A segment holding 97% of the volume "explaining" 72% of the
            # change is not a finding; one holding 11% explaining 59% is.)
            size_key = "c_baseline" if kind == "average" else "baseline"
            total_size = sum(abs(item.get(size_key) or 0) for item in items) or 1.0
            for item in items:
                item["baseline_share_pct"] = _round(abs(item.get(size_key) or 0) / total_size * 100, 2)
                expected = total_change * abs(item.get(size_key) or 0) / total_size
                item["excess"] = item["contribution"] - expected
                item["share_of_change_pct"] = _round(item["contribution"] / total_change * 100, 2) if total_change else None
            items.sort(key=lambda item: -(item["contribution"] * sign))
            # Explanatory power: how much of the change the three most
            # disproportionate segments explain beyond their size, with a
            # penalty for many segments (thousands of small segments add
            # up to large excess from noise alone).
            if total_change:
                leaders = sorted((item["excess"] * sign for item in items), reverse=True)[:3]
                power = min(1.0, max(0.0, sum(value for value in leaders if value > 0) / abs(total_change)))
            else:
                power = 0.0
            score = power / (1 + 0.15 * math.log2(max(len(items), 2)))
        else:
            volume = sum(item.get("n_current", 0) + item.get("n_baseline", 0) for item in items) or 1
            for item in items:
                weight = (item.get("n_current", 0) + item.get("n_baseline", 0)) / volume
                item["impact"] = abs(item["change_pct"] or 0) * weight
            items.sort(key=lambda item: -item["impact"])
            score = (items[0]["impact"] / 100) if items else 0.0

        for item in items:
            for key in ("baseline", "current", "contribution", "mix_effect", "rate_effect", "excess"):
                if key in item:
                    item[key] = _round(item[key])
            for key in [k for k in item if k.startswith(("s_", "c_"))]:
                item.pop(key)

        shown = items[:MAX_SEGMENTS_SHOWN]
        rest = items[MAX_SEGMENTS_SHOWN:]
        other = None
        if rest and kind in ("additive", "average"):
            other = {
                "segment": f"Other ({len(rest)})",
                "contribution": _round(sum(item["contribution"] for item in rest)),
                "share_of_change_pct": _round(sum(item["share_of_change_pct"] or 0 for item in rest), 2),
            }
        if kind in ("additive", "average") and items:
            leader = max(items, key=lambda item: (item.get("excess") or 0) * (-1 if (total_change or 0) < 0 else 1))
        else:
            leader = items[0] if items else None
        result = {
            "dimension": label,
            "leading_segment": leader["segment"] if leader else None,
            "segments_total": len(items),
            "top": shown,
            "other": other,
            "total_change": _round(total_change),
            "score": round(score, 4),
        }
        if kind == "average":
            result["mix_effect"] = _round(sum(item.get("mix_effect") or 0 for item in items))
            result["rate_effect"] = _round(sum(item.get("rate_effect") or 0 for item in items))
        return result

    # ---------------- 4. volume vs value ----------------
    def volume_value(self, headline: Dict[str, Any], points: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        metric = self.plan.metric
        if metric.kind != "additive" or not re.match(r"(?is)^\s*sum\s*\(", metric.expression):
            return None
        by_period = {point["period"]: point for point in points}
        b, c = by_period[headline["baseline_period"]], by_period[headline["current_period"]]
        if not (b["value"] and c["value"] and b["n"] and c["n"]) or b["value"] <= 0 or c["value"] <= 0:
            return None
        total = math.log(c["value"] / b["value"])
        volume = math.log(c["n"] / b["n"])
        per_record = total - volume
        return {
            "records_baseline": b["n"],
            "records_current": c["n"],
            "records_change_pct": _round(_pct(c["n"], b["n"]), 2),
            "value_per_record_baseline": _round(b["value"] / b["n"]),
            "value_per_record_current": _round(c["value"] / c["n"]),
            "value_per_record_change_pct": _round(_pct(c["value"] / c["n"], b["value"] / b["n"]), 2),
            "share_from_records_pct": _round(volume / total * 100, 1) if total else None,
            "share_from_value_per_record_pct": _round(per_record / total * 100, 1) if total else None,
            # which factor carried the change (same direction, larger log change)
            "main_factor": "records" if abs(volume) >= abs(per_record) else "value_per_record",
            "factors_opposed": volume * per_record < 0,
        }

    # ---------------- 5. drill-down ----------------
    def drilldown(self, first: Dict[str, Any], second_dimension: Tuple[str, str], baseline: str, current: str) -> Optional[Dict[str, Any]]:
        if self.plan.metric.kind not in ("additive", "average") or not first["top"]:
            return None
        table1, column1 = first["dimension"].split(".", 1)
        table2, column2 = second_dimension
        top_segments = [item["segment"] for item in first["top"][:6]]
        if first.get("leading_segment") in top_segments:
            top_segments.remove(first["leading_segment"])
            top_segments.insert(0, first["leading_segment"])
        seg1 = f"COALESCE({dialects.cast_text(col_sql(table1, column1))}, '(missing)')"
        seg2 = f"COALESCE({dialects.cast_text(col_sql(table2, column2))}, '(missing)')"
        values = ", ".join("'" + segment.replace("'", "''") + "'" for segment in top_segments)
        sql = (
            f"SELECT {seg1} AS row_segment, {seg2} AS segment, {self.period_sql} AS period, {self._measure_columns()} "
            f"FROM {self.graph.from_clause(self._base_tables([table1, table2]))} "
            f"WHERE {self._where([baseline, current], f'{seg1} IN ({values})')} GROUP BY 1, 2, 3"
        )
        rows = self._query(f"Change by {first['dimension']} x {table2}.{column2}", sql, max_rows=200_000)["rows"]
        by_row: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            by_row.setdefault(str(row["row_segment"]), []).append(row)
        cells, column_totals = [], {}
        for row_segment in top_segments:
            breakdown = self._contributions(by_row.get(row_segment, []), baseline, current, "")
            for item in breakdown["top"]:
                cells.append({"row": row_segment, "column": item["segment"], "contribution": item["contribution"],
                              "baseline": item["baseline"], "current": item["current"]})
                column_totals[item["segment"]] = column_totals.get(item["segment"], 0.0) + abs(item["contribution"] or 0)
        columns = [name for name, _ in sorted(column_totals.items(), key=lambda pair: -pair[1])[:MAX_SEGMENTS_SHOWN]]
        cells = [cell for cell in cells if cell["column"] in columns]
        top_row = top_segments[0]
        inside_top = sorted((cell for cell in cells if cell["row"] == top_row),
                            key=lambda cell: (cell["contribution"] or 0) * (1 if (first["total_change"] or 0) >= 0 else -1),
                            reverse=True)
        return {
            "rows_dimension": first["dimension"],
            "columns_dimension": f"{table2}.{column2}",
            "rows": top_segments,
            "columns": columns,
            "cells": cells,
            "top_segment": top_row,
            "top_inside": inside_top[:3],
        }

    # ---------------- 6. signals ----------------
    def signal_candidates(self) -> List[Dict[str, str]]:
        metric_columns = set(re.findall(r'"([^"]+)"\."([^"]+)"', self.plan.metric.expression))
        metric_columns |= {tuple(match.split(".")) for match in re.findall(r"\b(\w+\.\w+)\b", self.plan.metric.expression)}
        candidates: List[Dict[str, str]] = []
        gaps: List[Dict[str, str]] = []
        statuses: List[Dict[str, Any]] = []
        for table_name in self.graph.paths:
            table = self.graph.tables[table_name]
            temporal = []
            for column in table.get("columns", []):
                name = column.get("column_name")
                data_type = str(column.get("data_type", "")).lower()
                if (table_name, name) in metric_columns or _is_key_column(table, name) or re.search(r"(^|_)(id|key|code)$", name):
                    continue
                if any(word in data_type for word in ("int", "numeric", "double", "real", "float", "decimal")):
                    if column.get("distinct_count") is not None and column["distinct_count"] <= 2:
                        continue
                    candidates.append({"label": f"avg {name}", "table": table_name, "column": name,
                                       "sql": f"AVG({col_sql(table_name, name)})", "kind": "average"})
                elif "date" in data_type or "time" in data_type:
                    temporal.append(name)
                elif column.get("values") and 2 <= len(column["values"]) <= MAX_STATUS_VALUES:
                    statuses.append({"table": table_name, "column": name, "values": column["values"]})
            for i, first in enumerate(temporal):
                for second in temporal[i + 1:]:
                    gaps.append({
                        "label": f"days {first} → {second}", "table": table_name,
                        "sql": (f"AVG({dialects.seconds_between(col_sql(table_name, second), col_sql(table_name, first))}"
                                " / 86400.0)"),
                        "kind": "time_gap",
                        "_priority": 0 if self.plan.time_column in (first, second) and table_name == self.plan.time_table else 1,
                    })
        gaps.sort(key=lambda gap: gap["_priority"])
        for gap in gaps:
            gap.pop("_priority")
        result = candidates[:MAX_SIGNAL_COLUMNS] + gaps[:MAX_TIME_GAPS]
        for status in statuses[:MAX_STATUS_COLUMNS]:
            for value in status["values"]:
                literal = "'" + str(value).replace("'", "''") + "'"
                result.append({
                    "label": f"share {status['column']} = {value}", "table": status["table"], "column": status["column"],
                    "sql": f"AVG(CASE WHEN {col_sql(status['table'], status['column'])} = {literal} THEN 100.0 ELSE 0 END)",
                    "kind": "share",
                })
        return result

    def signals(self, baseline: str, current: str, focus: Optional[Tuple[str, str, str]] = None) -> List[Dict[str, Any]]:
        candidates = self.signal_candidates()
        if not candidates:
            return []
        tables = set(self.graph.paths)
        select = ", ".join(f"{candidate['sql']} AS s{index}" for index, candidate in enumerate(candidates))
        scopes = [("overall", "")]
        if focus:
            table, column, segment = focus
            literal = "'" + segment.replace("'", "''") + "'"
            scopes.append((f"{column} = {segment}",
                           f"COALESCE({dialects.cast_text(col_sql(table, column))}, '(missing)') = {literal}"))
        values: Dict[str, Dict[str, Dict[str, Optional[float]]]] = {}
        for scope, condition in scopes:
            sql = (
                f"SELECT {self.period_sql} AS period, {select} "
                f"FROM {self.graph.from_clause(self._base_tables(sorted(tables)))} "
                f"WHERE {self._where([baseline, current], condition)} GROUP BY 1"
            )
            rows = self._query(f"Related signals ({scope})", sql)["rows"]
            values[scope] = {_period_key(row["period"]): row for row in rows}

        signals = []
        for index, candidate in enumerate(candidates):
            entry = {"signal": candidate["label"], "table": candidate.get("table"), "kind": candidate["kind"]}
            best = 0.0
            for scope, _ in scopes:
                if scope != "overall" and focus and (candidate.get("table"), candidate.get("column")) == focus[:2]:
                    continue  # the segment is defined by this column: trivially flat inside it
                rows = values.get(scope, {})
                old = _num((rows.get(baseline) or {}).get(f"s{index}"))
                new = _num((rows.get(current) or {}).get(f"s{index}"))
                if old is None or new is None:
                    continue
                if candidate["kind"] in ("share", "time_gap"):
                    # points / days: absolute change (a % of a near-zero
                    # gap or share is meaningless: 0.01 -> 1.2 days = +11900%)
                    change = new - old
                else:
                    # symmetric %: bounded (±200%), stable for small baselines
                    scale = (abs(new) + abs(old)) / 2
                    change = (new - old) / scale * 100 if scale else None
                key = "overall" if scope == "overall" else "segment"
                entry[f"{key}_baseline"], entry[f"{key}_current"] = _round(old), _round(new)
                entry[f"{key}_change"] = _round(change, 2)
                if change is not None:
                    best = max(best, abs(change))
            if candidate["kind"] == "share" and max(entry.get("overall_baseline") or 0, entry.get("overall_current") or 0,
                                                    entry.get("segment_baseline") or 0, entry.get("segment_current") or 0) < 1:
                continue  # a status that barely occurs
            # comparable strength across units: shares in points, gaps
            # relative to their size, averages in %
            if candidate["kind"] == "time_gap":
                base = max(abs(entry.get("overall_baseline") or 0), 0.5)
                best = best / base * 100
            entry["strength"] = round(best, 2)
            entry["unit"] = {"share": "points", "time_gap": "days"}.get(candidate["kind"], "%")
            if best > 0:
                signals.append(entry)
        signals.sort(key=lambda entry: -entry["strength"])
        if focus:
            for entry in signals:
                entry["scope"] = scopes[1][0]
        return signals[:8]

    # ---------------- run everything ----------------
    def run(self) -> Dict[str, Any]:
        trend = self.trend()
        baseline, current, reason = self.choose_periods(trend["points"])
        headline = self.headline(trend["points"], baseline, current)
        headline["period_choice"] = reason

        drivers = []
        for table, column in self.plan.dimensions[:MAX_DIMENSIONS]:
            try:
                drivers.append(self.breakdown(table, column, baseline, current))
            except InvestigationError as exc:
                self.plan.notes.append(f"Skipped {table}.{column}: {exc}")
        drivers.sort(key=lambda driver: -driver["score"])
        # The same partition reached under another name (a code and its
        # label, a name and its translation) is shown once.
        unique_drivers, signatures = [], set()
        for driver in drivers:
            signature = (driver["segments_total"],
                         tuple(sorted(round(item.get("contribution") or item.get("change_pct") or 0, 6)
                                      for item in driver["top"])))
            if signature in signatures:
                self.plan.notes.append(f"{driver['dimension']} repeats an earlier breakdown; not shown.")
                continue
            signatures.add(signature)
            unique_drivers.append(driver)
        drivers = unique_drivers

        drilldown = None
        if len(drivers) >= 2:
            second = tuple(drivers[1]["dimension"].split(".", 1))
            drilldown = self.drilldown(drivers[0], second, baseline, current)

        focus = None
        if drivers and drivers[0].get("leading_segment"):
            table, column = drivers[0]["dimension"].split(".", 1)
            focus = (table, column, drivers[0]["leading_segment"])
        signals = self.signals(baseline, current, focus)

        return {
            "plan": {
                "metric": self.plan.metric.name,
                "expression": self.plan.metric.expression,
                "metric_kind": self.plan.metric.kind,
                "fact_table": self.plan.fact_table,
                "time_column": f"{self.plan.time_table}.{self.plan.time_column}",
                "grain": self.plan.grain,
                "filters": self.plan.filters,
                "dimensions": [f"{t}.{c}" for t, c in self.plan.dimensions],
                "notes": self.plan.notes,
            },
            "trend": trend["points"],
            "headline": headline,
            "volume_value": self.volume_value(headline, trend["points"]),
            "drivers": drivers,
            "drilldown": drilldown,
            "signals": signals,
            "queries": self.queries,
        }


def period_label(period: str, grain: str) -> str:
    """'2018-08-01' -> '2018-08' (month), '2018-Q3', '2018', 'week of 2018-08-06'."""
    try:
        value = datetime.fromisoformat(str(period)[:10])
    except ValueError:
        return str(period)
    if grain == "year":
        return f"{value.year}"
    if grain == "quarter":
        return f"{value.year}-Q{(value.month - 1) // 3 + 1}"
    if grain == "month":
        return value.strftime("%Y-%m")
    if grain == "week":
        return f"week of {value.date().isoformat()}"
    return value.date().isoformat()


def _snap(period: str, available: set) -> Optional[str]:
    """Match '2018-08', '2018-08-15' or 'Aug 2018' style input to a period key."""
    text = str(period).strip()
    if text in available:
        return text
    match = re.match(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", text)
    if not match:
        return None
    year, month = match.group(1), match.group(2)
    candidates = sorted(p for p in available if p.startswith(year + (f"-{int(month):02d}" if month else "")))
    return candidates[0] if candidates else None
