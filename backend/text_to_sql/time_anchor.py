# backend/text_to_sql/time_anchor.py
#
# "This year", "last month", ... on data that stopped years ago.
#
# The SQL model reads "this year" as the calendar year of today's date
# (CURRENT_DATE). On a database whose rows end in, say, 2018 that gives
# empty results. When the question uses a relative period and the
# latest date in the linked tables is well before today, this adds a
# short note to the analytical plan that anchors those words to the end
# of the data instead ("this year" = 2018, "last year" = 2017).
#
# Read-only: one MAX() per date column, cached for a few minutes. Any
# failure means "no note", never a failed question.

import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import column as sql_column, func, select, table as sql_table
from sqlalchemy.engine import Engine

RELATIVE_TIME_RE = re.compile(
    r"\b(this|current|last|previous|past|prior|recent)\s+(year|month|quarter|week|day)s?\b"
    r"|\b(last|past|previous)\s+\d+\s+(years?|months?|quarters?|weeks?|days?)\b"
    r"|\b(today|yesterday|recently|lately|year[\s-]to[\s-]date|ytd|so\s+far\s+this)\b"
    r"|\b(year|month|quarter)\s+over\s+(year|month|quarter)\b|\b(yoy|mom|qoq)\b"
    r"|\b(is\s+saal|pichle\s+saal|is\s+mahine|pichle\s+mahine)\b",
    flags=re.IGNORECASE,
)
_DATE_TYPES = ("date", "time")  # DATE, TIMESTAMP, DATETIME, TIMESTAMPTZ ...
_DATE_NAMES = re.compile(r"(date|time|_at$|_on$|timestamp|created|updated|purchase|delivered)", re.IGNORECASE)
STALE_AFTER_DAYS = 45
MAX_COLUMNS = 8
CACHE_SECONDS = 600

_cache: Dict[Tuple[str, str, str], Tuple[float, Optional[date]]] = {}
_lock = threading.Lock()


def uses_relative_time(question: str) -> bool:
    return bool(RELATIVE_TIME_RE.search(question or ""))


def _to_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m"):
        try:
            return datetime.strptime(text[: len(datetime.now().strftime(fmt))], fmt).date()
        except ValueError:
            continue
    return None


def date_columns(state: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(table, column) pairs that hold dates, from the linked tables first."""
    tables = (state.get("schema") or {}).get("tables") or []
    linked = set((state.get("linked_schema") or {}).get("tables") or [])
    ordered = [t for t in tables if t.get("table_name") in linked] or tables
    found: List[Tuple[str, str]] = []
    for table in ordered:
        for col in table.get("columns") or []:
            name, kind = col.get("column_name") or "", str(col.get("data_type") or "").lower()
            typed = any(word in kind for word in _DATE_TYPES)
            textual = ("char" in kind or "text" in kind) and _DATE_NAMES.search(name)
            if name and (typed or textual):
                found.append((table["table_name"], name))
            if len(found) >= MAX_COLUMNS:
                return found
    return found


def _latest(engine: Engine, table_name: str, column_name: str) -> Optional[date]:
    key = (str(engine.url), table_name, column_name)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    value: Optional[date] = None
    try:
        with engine.connect() as connection:
            raw = connection.execute(select(func.max(sql_column(column_name))).select_from(sql_table(table_name))).scalar()
        value = _to_date(raw)
    except Exception:
        value = None
    with _lock:
        _cache[key] = (now, value)
    return value


def latest_data_date(state: Dict[str, Any], engine: Engine) -> Tuple[Optional[date], str]:
    best: Optional[date] = None
    where = ""
    today = date.today()
    for table_name, column_name in date_columns(state):
        value = _latest(engine, table_name, column_name)
        if value and value <= today + timedelta(days=1) and (best is None or value > best):
            best, where = value, f"{table_name}.{column_name}"
    return best, where


def time_note(question: str, state: Dict[str, Any], engine: Engine) -> str:
    """The anchoring note, or "" when it is not needed."""
    if not uses_relative_time(question):
        return ""
    latest, where = latest_data_date(state, engine)
    if latest is None or (date.today() - latest).days <= STALE_AFTER_DAYS:
        return ""
    year, month = latest.year, latest.month
    previous_month = (latest.replace(day=1) - timedelta(days=1))
    return (
        "\n\nTIME ANCHOR (the data is historical):\n"
        f"The latest date in this data is {latest.isoformat()} ({where}), long before today. "
        "Read relative periods against the end of the data, not today's date: "
        f"'this year' = {year}, 'last year' = {year - 1}, "
        f"'this month' = {year}-{month:02d}, 'last month' = {previous_month.year}-{previous_month.month:02d}, "
        f"'last N days/months' = the N days/months ending {latest.isoformat()}. "
        "Do not use CURRENT_DATE, NOW() or GETDATE(); write these periods as fixed dates. "
        "Do not ask for clarification about these periods."
    )


def with_time_note(state: Dict[str, Any], engine: Engine) -> Dict[str, Any]:
    """A copy of the state whose analytical plan carries the time anchor (if needed)."""
    try:
        question = f"{state.get('question') or ''} {state.get('rewritten_query') or ''}"
        note = time_note(question, state, engine)
    except Exception:
        note = ""
    if not note:
        return state
    return {**state, "decomposition": f"{state.get('decomposition') or ''}{note}"}
