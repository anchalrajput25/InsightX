# Tests for backend/text_to_sql/time_anchor.py: relative periods on old data.
from datetime import date, timedelta

from sqlalchemy import create_engine, text

from backend.text_to_sql import time_anchor


def _engine(tmp_path, last_day):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE orders (id INTEGER, order_purchase_timestamp TEXT, status TEXT)"))
        c.execute(text("INSERT INTO orders VALUES (1, '2016-09-04 21:15:19', 'ok'), (2, :d, 'ok')"), {"d": last_day})
    return engine


STATE = {
    "question": "Compare order volume this year vs last year",
    "rewritten_query": "",
    "decomposition": "PLAN",
    "schema": {"tables": [{"table_name": "orders", "columns": [
        {"column_name": "id", "data_type": "INTEGER"},
        {"column_name": "order_purchase_timestamp", "data_type": "TEXT"},
        {"column_name": "status", "data_type": "TEXT"}]}]},
    "linked_schema": {"tables": ["orders"]},
}


def test_relative_words():
    assert time_anchor.uses_relative_time("orders this year vs last year")
    assert time_anchor.uses_relative_time("revenue in the last 30 days")
    assert time_anchor.uses_relative_time("YoY growth")
    assert not time_anchor.uses_relative_time("orders in 2017 by state")


def test_old_data_gets_anchor(tmp_path):
    time_anchor._cache.clear()
    out = time_anchor.with_time_note(dict(STATE), _engine(tmp_path, "2018-10-17 17:30:18"))
    assert out["decomposition"].startswith("PLAN")
    assert "'this year' = 2018" in out["decomposition"]
    assert "'last year' = 2017" in out["decomposition"]
    assert "orders.order_purchase_timestamp" in out["decomposition"]


def test_current_data_unchanged(tmp_path):
    time_anchor._cache.clear()
    recent = (date.today() - timedelta(days=3)).isoformat()
    state = dict(STATE)
    assert time_anchor.with_time_note(state, _engine(tmp_path, recent)) is state


def test_no_relative_words_unchanged(tmp_path):
    time_anchor._cache.clear()
    state = {**STATE, "question": "orders in 2017"}
    assert time_anchor.with_time_note(state, _engine(tmp_path, "2018-10-17")) is state


def test_failure_means_no_note(tmp_path):
    time_anchor._cache.clear()
    state = {**STATE, "linked_schema": {"tables": ["missing"]},
             "schema": {"tables": [{"table_name": "missing", "columns": [{"column_name": "created_at", "data_type": "DATE"}]}]}}
    assert time_anchor.with_time_note(state, _engine(tmp_path, "2018-10-17")) is state
