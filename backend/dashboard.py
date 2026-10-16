# backend/dashboard.py
#
# "📈 Dashboard": a basic picture of the whole connected database, and
# charts people ask for in their own words ("a graph of sales by state").
#
#   overview   built from the schema alone, no AI: the size of each
#              table, records per month for the tables that have an
#              event date, totals per month and per category, where
#              the category or the date sits in a joined table. Joins
#              only follow many-to-one links (no double counting).
#   my charts  "I want a graph of X and Y": the text goes through the
#              normal question pipeline once to get SQL; the SQL is
#              saved and re-run read-only each time the dashboard
#              opens, so the chart always shows the current data.
#
# Plugged into the API by one include_router line in backend/main.py.

import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import Column, Float, Index, MetaData, String, Table, Text, delete, insert, select
from sqlalchemy.engine import Engine

from backend import app_db, dialects, telemetry
from backend.auth.deps import Caller, current_caller

OVERVIEW_SECONDS = float(os.getenv("INSIGHTX_DASHBOARD_CACHE_SECONDS", "600"))
MAX_TABLE_COUNTS = 40
MAX_CHARTS = 8
TOP_CATEGORIES = 10
MAX_SAVED = 30

CHART_KINDS = {"bar": "bar", "column": "bar", "line": "line", "trend": "line", "area": "area",
               "pie": "pie", "donut": "pie", "scatter": "scatter"}

_EVENT_DATE = re.compile(r"(order|payment|invoice|rental|sale|transaction|purchase|ship|creat|start|date|time|_at$)", re.I)
_SKIP_DATE = re.compile(r"(update|modif|birth|hire|required|expir|end_|due|valid|rowguid|sell_end|discontinu|limit|estimated)", re.I)
_MEASURE = re.compile(r"(price|amount|value|total|payment|revenue|sales|freight|cost|quantity|qty|profit|fee|charge)", re.I)
_ID_NAME = re.compile(r"(^id$|_id$|^id_|_key$|code$|zip|postal|phone|email|_prefix$|uuid|guid)", re.I)
_GROUP_NAME = re.compile(r"(state|city|country|region|category|status|type|genre|rating|segment|method|gender|channel|class|group|department|brand|language)", re.I)
_FREE_TEXT = re.compile(r"(address|name$|^name|description|comment|message|title|note|text|url|street|phone|email)", re.I)
_NUMBER_TYPE = ("int", "numeric", "decimal", "double", "real", "float", "money")
_DATE_TYPE = ("date", "time")


# ============================================================
# OVERVIEW (no AI)
# ============================================================

def _kind(column: Dict[str, Any]) -> str:
    data_type = str(column.get("data_type", "")).lower()
    if any(word in data_type for word in _DATE_TYPE):
        return "date"
    if any(word in data_type for word in _NUMBER_TYPE):
        return "number"
    return "text"


def _label(name: str) -> str:
    return str(name).replace("_", " ").strip()


def _run(sql: str, engine: Engine, max_rows: int = 500) -> Optional[Dict[str, Any]]:
    from backend.text_to_sql.query_executor import execute_query
    from backend.text_to_sql.sql_safety import check_sql_safety

    if not check_sql_safety(sql).get("safe"):
        return None
    result = execute_query(sql, engine, max_rows=max_rows)
    return result if result.get("success") else None


def _graph(schema: Dict[str, Any], table: str):
    from backend.investigation.engine import SchemaGraph

    try:
        return SchemaGraph(schema, table)
    except Exception:
        return None


def _event_date(table: Dict[str, Any]) -> Optional[str]:
    dates = [c["column_name"] for c in table.get("columns", []) if _kind(c) == "date"
             and not _SKIP_DATE.search(c["column_name"])]
    dates.sort(key=lambda name: 0 if _EVENT_DATE.search(name) else 1)
    return dates[0] if dates else None


def _categories(table: Dict[str, Any]) -> List[str]:
    """Short label columns to group by (state, category, status), best first."""
    found = []
    for column in table.get("columns", []):
        name = column.get("column_name", "")
        if _kind(column) != "text" or _ID_NAME.search(name) or column.get("unique") \
                or (_FREE_TEXT.search(name) and not _GROUP_NAME.search(name)):
            continue
        distinct = column.get("distinct_count")
        if distinct is not None and not 3 <= distinct <= 200:
            continue
        found.append((0 if _GROUP_NAME.search(name) else 1, name))
    return [name for _, name in sorted(found)]


def _measures(table: Dict[str, Any]) -> List[str]:
    return [c["column_name"] for c in table.get("columns", [])
            if _kind(c) == "number" and _MEASURE.search(c["column_name"]) and not _ID_NAME.search(c["column_name"])
            and not (c.get("distinct_count") is not None and c["distinct_count"] <= 2)]


def table_sizes(engine: Engine, schema: Dict[str, Any]) -> Dict[str, int]:
    sizes = {}
    for table in schema.get("tables", [])[:MAX_TABLE_COUNTS]:
        name = table["table_name"]
        result = _run(f"SELECT COUNT(*) AS n FROM {dialects.quote(name)}", engine, 1)
        if result and result.get("rows"):
            sizes[name] = int(list(result["rows"][0].values())[0] or 0)
    return sizes


def overview_specs(schema: Dict[str, Any], sizes: Dict[str, int]) -> List[Dict[str, Any]]:
    """The charts to draw: title, how it is computed, and its SQL."""
    from backend.investigation.engine import col_sql

    tables = {t["table_name"]: t for t in schema.get("tables", [])}
    by_size = sorted(tables, key=lambda name: -sizes.get(name, 0))
    specs: List[Dict[str, Any]] = []
    seen = set()

    limit = MAX_CHARTS

    def add(spec: Dict[str, Any]) -> None:
        if spec["key"] not in seen and len(specs) < min(limit, MAX_CHARTS):
            seen.add(spec["key"])
            specs.append(spec)

    # Tables people count: the biggest ones with an event date (orders,
    # payments, rentals). For each: records per month, then totals and
    # breakdowns through the tables it links to.
    graphs = {name: _graph(schema, name) for name in by_size if sizes.get(name)}
    graphs = {name: graph for name, graph in graphs.items() if graph is not None}
    # tables with a date first (orders before a lookup list), biggest first
    facts = sorted(graphs, key=lambda name: (not any(_event_date(tables[t]) for t in graphs[name].paths),
                                             -sizes.get(name, 0)))
    for fact in facts[:6]:
        graph = graphs[fact]
        reach = list(graph.paths)
        limit = len(specs) + 4  # at most 4 charts per table, so several tables show
        date = next(((t, d) for t in reach if (d := _event_date(tables[t]))), None)
        if date:
            period = dialects.period("month", col_sql(*date))
            add({
                "key": f"count-month-{fact}", "kind": "line",
                "title": f"{_label(fact).capitalize()} per month",
                "subtitle": f"Number of {_label(fact)} records by {_label(date[1])}",
                "sql": (f"SELECT {period} AS month, COUNT(*) AS {dialects.quote('records')} "
                        f"FROM {graph.from_clause([date[0]])} WHERE {col_sql(*date)} IS NOT NULL "
                        f"GROUP BY 1 ORDER BY 1"),
            })
            for measure in _measures(tables[fact])[:1]:
                add({
                    "key": f"sum-month-{fact}-{measure}", "kind": "line",
                    "title": f"Total {_label(measure)} per month",
                    "subtitle": f"Sum of {fact}.{measure} by {_label(date[1])}",
                    "sql": (f"SELECT {period} AS month, SUM({col_sql(fact, measure)}) AS "
                            f"{dialects.quote('total_' + measure)} FROM {graph.from_clause([date[0]])} "
                            f"WHERE {col_sql(*date)} IS NOT NULL GROUP BY 1 ORDER BY 1"),
                })
        # breakdowns: a category in this table or a linked one (customer state, product category)
        for table in sorted(reach, key=lambda t: (t == fact, len(graph.paths[t]))):
            for category in _categories(tables[table])[:1]:
                label = dialects.cast_text(col_sql(table, category))
                body = (f"{label} AS {dialects.quote(category)}, COUNT(*) AS {dialects.quote(fact + '_count')} "
                        f"FROM {graph.from_clause([table])} WHERE {col_sql(table, category)} IS NOT NULL "
                        f"GROUP BY {label} ORDER BY 2 DESC")
                add({
                    "key": f"count-by-{table}.{category}", "kind": "bar",
                    "title": f"{_label(fact).capitalize()} by {_label(category)}",
                    "subtitle": f"Top {TOP_CATEGORIES} by number of {_label(fact)} records",
                    "sql": dialects.select_limited(body, TOP_CATEGORIES),
                })
                for measure in _measures(tables[fact])[:1]:
                    body = (f"{label} AS {dialects.quote(category)}, SUM({col_sql(fact, measure)}) AS "
                            f"{dialects.quote('total_' + measure)} FROM {graph.from_clause([table])} "
                            f"WHERE {col_sql(table, category)} IS NOT NULL GROUP BY {label} ORDER BY 2 DESC")
                    add({
                        "key": f"sum-by-{table}.{category}-{fact}.{measure}", "kind": "bar",
                        "title": f"Total {_label(measure)} by {_label(category)}",
                        "subtitle": f"Top {TOP_CATEGORIES} by sum of {fact}.{measure}",
                        "sql": dialects.select_limited(body, TOP_CATEGORIES),
                    })
        if len(specs) >= MAX_CHARTS:
            break
    return specs


def build_overview(engine: Engine, schema: Dict[str, Any]) -> Dict[str, Any]:
    with dialects.using(engine):
        sizes = table_sizes(engine, schema)
        charts = []
        for spec in overview_specs(schema, sizes):
            result = _run(spec["sql"], engine)
            if result and result.get("rows"):
                charts.append({**spec, "columns": result.get("columns") or [], "rows": result["rows"]})
    largest = sorted(sizes.items(), key=lambda item: -item[1])
    return {
        "tables": len(schema.get("tables", [])),
        "links": len(schema.get("relationships", [])),
        "columns": sum(len(t.get("columns", [])) for t in schema.get("tables", [])),
        "rows": sum(sizes.values()),
        "table_sizes": [{"table": name, "rows": rows} for name, rows in largest[:15]],
        "charts": charts,
        "built_at": time.time(),
    }


_overview_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_overview_lock = threading.Lock()


# ============================================================
# CHARTS PEOPLE ASK FOR
# ============================================================

def requested_kind(text: str) -> str:
    """'pie chart of ...' -> 'pie'; '' when the text names no chart type."""
    for word, kind in CHART_KINDS.items():
        if re.search(rf"\b{word}\b", text or "", re.I):
            return kind
    return ""


metadata = MetaData()

saved_charts = Table(
    "insightx_dashboard_charts", metadata,
    Column("id", String(32), primary_key=True),
    Column("owner", String(120), nullable=False),
    Column("database", String(255), nullable=False),
    Column("ts", Float, nullable=False),
    Column("request", Text, nullable=False),
    Column("kind", String(20), nullable=False, default=""),
    Column("sql", Text, nullable=False),
    Column("answer", Text, nullable=False, default=""),
    Index("ix_insightx_dashboard_owner", "owner", "database", "ts"),
)


class ChartStore:
    def __init__(self, url: Optional[str] = None):
        self.engine: Engine = app_db.store_engine(url, "INSIGHTX_DASHBOARD_DB_URL", "dashboard.db")
        self._lock = threading.Lock()
        metadata.create_all(self.engine)

    def add(self, owner: str, database: str, request: str, kind: str, sql: str, answer: str = "") -> str:
        chart_id = uuid.uuid4().hex
        with self._lock, self.engine.begin() as connection:
            connection.execute(insert(saved_charts).values(
                id=chart_id, owner=owner, database=database, ts=time.time(), request=request[:1000],
                kind=kind, sql=sql, answer=answer[:2000]))
            stale = connection.execute(
                select(saved_charts.c.ts).where(saved_charts.c.owner == owner, saved_charts.c.database == database)
                .order_by(saved_charts.c.ts.desc()).offset(MAX_SAVED).limit(1)).scalar()
            if stale is not None:
                connection.execute(delete(saved_charts).where(
                    saved_charts.c.owner == owner, saved_charts.c.database == database, saved_charts.c.ts <= stale))
        return chart_id

    def list(self, owner: str, database: str) -> List[Dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(saved_charts).where(saved_charts.c.owner == owner, saved_charts.c.database == database)
                .order_by(saved_charts.c.ts.desc())).fetchall()
        return [{k: v for k, v in dict(row._mapping).items() if k != "owner"} for row in rows]

    def delete(self, owner: str, chart_id: str) -> bool:
        with self._lock, self.engine.begin() as connection:
            result = connection.execute(delete(saved_charts).where(
                saved_charts.c.owner == owner, saved_charts.c.id == chart_id))
        return bool(result.rowcount)


_store: Optional[ChartStore] = None
_store_lock = threading.Lock()


def get_store() -> ChartStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = ChartStore()
        return _store


def set_store(store: Optional[ChartStore]) -> None:
    """Tests use this to swap in a temporary store."""
    global _store
    with _store_lock:
        _store = store


def _live(chart: Dict[str, Any], engine: Engine) -> Dict[str, Any]:
    with dialects.using(engine):
        result = _run(chart["sql"], engine)
    if result is None:
        return {**chart, "columns": [], "rows": [], "error": "This chart's query could not run on the current data."}
    return {**chart, "columns": result.get("columns") or [], "rows": result.get("rows") or []}


# ============================================================
# API
# ============================================================

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _main():
    from backend import main  # imported late: main includes this router

    return main


class ChartRequest(BaseModel):
    connection_id: str
    request: str = Field(min_length=1, max_length=1000)


@router.get("/overview")
def overview(connection_id: str, refresh: bool = False, caller: Caller = Depends(current_caller)):
    """A basic picture of the whole database (no AI)."""
    main = _main()
    session = main.get_connection_session(connection_id, caller)
    engine = session["engine"]
    key = f"{main._chat_list_owner(caller)}|{connection_id}"
    with _overview_lock:
        cached = _overview_cache.get(key)
    if cached and not refresh and time.time() - cached[0] < OVERVIEW_SECONDS:
        return cached[1]
    from backend.text_to_sql.schema_context import get_agent_schema

    with dialects.using(engine):
        schema = get_agent_schema(engine)
    data = jsonable_encoder({**build_overview(engine, schema), "database": main._database_label(session)})
    with _overview_lock:
        _overview_cache[key] = (time.time(), data)
    return data


@router.get("/charts")
def my_charts(connection_id: str, caller: Caller = Depends(current_caller)):
    """Charts this person asked for on this database, re-run on the current data."""
    main = _main()
    session = main.get_connection_session(connection_id, caller)
    charts = get_store().list(main._chat_list_owner(caller), main._database_label(session))
    return {"charts": jsonable_encoder([_live(chart, session["engine"]) for chart in charts])}


@router.post("/charts")
def make_chart(body: ChartRequest, caller: Caller = Depends(current_caller)):
    """'I want a graph of X and Y': get the data with the normal pipeline, keep the chart."""
    main = _main()
    user_id = main._start_question(caller)
    session = main.get_connection_session(body.connection_id, caller)
    engine = session["engine"]
    question = body.request.strip()
    with telemetry.collect():
        with main.ai_request(user_id=user_id):
            result = main.run_text_to_sql(question=question, thread_id=f"dashboard-{uuid.uuid4()}", engine=engine)
    sql = str(result.get("generated_sql") or "").strip()
    if str(result.get("status") or "").upper() != "SUCCESS" or not sql:
        detail = result.get("clarification") or result.get("error") or "Could not get data for this chart."
        raise HTTPException(status_code=422, detail=str(detail)[:500])
    execution = result.get("execution_result") or {}
    chart = {"request": question, "kind": requested_kind(question), "sql": sql,
             "answer": str(result.get("answer") or "")}
    chart["id"] = get_store().add(main._chat_list_owner(caller), main._database_label(session),
                                  question, chart["kind"], sql, chart["answer"])
    chart.update({"ts": time.time(), "columns": execution.get("columns") or [], "rows": execution.get("rows") or []})
    return jsonable_encoder(chart)


@router.delete("/charts/{chart_id}")
def remove_chart(chart_id: str, caller: Caller = Depends(current_caller)):
    if not get_store().delete(_main()._chat_list_owner(caller), chart_id):
        raise HTTPException(status_code=404, detail="Chart not found.")
    return {"deleted": 1}
