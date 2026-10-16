import altair as alt

from chart_builder import (
    AREA,
    BAR,
    LINE,
    METRIC,
    PIE,
    SCATTER,
    compact,
    plan_chart,
    summarize,
    switch,
    to_frame,
)
from chart_view import build_chart


def _plan(rows, question="", columns=None):
    frame = to_frame(rows, columns)
    return frame, plan_chart(frame, question)


def test_single_row_of_numbers_is_number_cards():
    _, plan = _plan([{"total_revenue": "1250000.5", "orders": 830}])
    assert plan.kind == METRIC
    assert plan.y == ["total_revenue", "orders"]


def test_dates_give_a_line_with_trend_summary():
    rows = [
        {"month": "2024-01-01", "revenue": 100},
        {"month": "2024-02-01", "revenue": 150},
        {"month": "2024-03-01", "revenue": 120},
    ]
    frame, plan = _plan(rows)
    assert plan.kind == LINE and plan.x == "month"
    assert set(plan.options) == {LINE, AREA, BAR}
    text = summarize(frame, plan)
    assert "from 100 (2024-01-01) to 120 (2024-03-01)" in text
    assert "up 20%" in text and "Highest: 150 in 2024-02-01" in text


def test_year_column_counts_as_time_and_label_becomes_series():
    rows = [
        {"year": y, "region": r, "sales": y - 2000 + i}
        for y in (2021, 2022, 2023)
        for i, r in enumerate(["North", "South"])
    ]
    _, plan = _plan(rows)
    assert plan.kind == LINE and plan.x == "year" and plan.color == "region"


def test_categories_give_bar_and_share_question_gives_pie():
    rows = [
        {"city": "Delhi", "customers": 50},
        {"city": "Mumbai", "customers": 30},
        {"city": "Pune", "customers": 20},
    ]
    frame, plan = _plan(rows, "Which city has the most customers?")
    assert plan.kind == BAR and PIE in plan.options
    assert summarize(frame, plan) == (
        "Delhi has the highest customers (50, 50% of the total). Lowest: Pune (20)."
    )
    _, plan = _plan(rows, "What is the share of customers by city?")
    assert plan.kind == PIE


def test_id_columns_are_labels_not_measures():
    rows = [{"customer_id": i, "total": i * 10} for i in range(1, 6)]
    _, plan = _plan(rows)
    assert plan.kind == BAR and plan.x == "customer_id" and plan.y == ["total"]


def test_negative_values_never_offer_pie():
    rows = [{"store": "A", "profit": 5}, {"store": "B", "profit": -3}]
    _, plan = _plan(rows, "share of profit")
    assert plan.kind == BAR and PIE not in plan.options


def test_two_numbers_give_scatter():
    rows = [{"price": p, "units": 100 - p} for p in range(1, 11)]
    frame, plan = _plan(rows)
    assert plan.kind == SCATTER
    assert "opposite directions" in summarize(frame, plan)


def test_text_only_or_empty_results_get_no_chart():
    assert _plan([{"name": "a", "email": "x"}])[1] is None
    assert _plan([])[1] is None


def test_many_bars_are_cut_to_top_25():
    rows = [{"product": f"P{i}", "sold": i} for i in range(40)]
    _, plan = _plan(rows)
    assert plan.kind == BAR and plan.horizontal
    assert "top 25 of 40" in plan.note


def test_switch_only_to_offered_kinds():
    rows = [{"city": "A", "n": 1}, {"city": "B", "n": 2}]
    _, plan = _plan(rows)
    assert switch(plan, PIE).kind == PIE
    assert switch(plan, LINE).kind == BAR


def test_compact_numbers():
    assert compact(1250000) == "1.25M"
    assert compact(1500) == "1.5K"
    assert compact(42) == "42"
    assert compact(3.14159) == "3.14"


def test_every_plan_draws_a_valid_chart():
    cases = [
        ([{"month": f"2024-{m:02d}-01", "a": m, "b": m * 2} for m in range(1, 7)], ""),
        ([{"year": 2020 + i, "region": r, "v": i} for i in range(3) for r in "XY"], ""),
        ([{"city": c, "n": i + 1} for i, c in enumerate("ABCD")], "share"),
        ([{"city": c, "kind": k, "n": 1} for c in "AB" for k in "xy"], ""),
        ([{"city": c, "n": 1, "m": 2} for c in "ABC"], ""),
        ([{"x": i, "y": i * i} for i in range(5)], ""),
    ]
    for rows, question in cases:
        frame, plan = _plan(rows, question)
        for kind in plan.options:
            chart = build_chart(frame, switch(plan, kind))
            assert isinstance(chart.to_dict(), dict)


def test_context_sql_widens_only_small_top_level_limits():
    from backend.text_to_sql.chart_context import CHART_ROWS, context_sql

    wider = context_sql(
        "WITH t AS (SELECT c.name, SUM(p.amount) AS total FROM c JOIN p ON p.cid = c.id GROUP BY c.name) "
        "SELECT name, total FROM t ORDER BY total DESC LIMIT 1"
    )
    assert wider.endswith(f"LIMIT {CHART_ROWS + 1}") and "GROUP BY" in wider
    assert context_sql("SELECT city, COUNT(*) FROM c GROUP BY city ORDER BY 2 DESC LIMIT 3").endswith("LIMIT 26")
    # No ORDER BY, no GROUP BY, OFFSET, big LIMIT, UNION: left alone.
    assert context_sql("SELECT city, COUNT(*) FROM c GROUP BY city LIMIT 1") is None
    assert context_sql("SELECT COUNT(*) FROM c ORDER BY 1 LIMIT 1") is None
    assert context_sql("SELECT city, COUNT(*) FROM c GROUP BY city ORDER BY 2 LIMIT 1 OFFSET 2") is None
    assert context_sql("SELECT city, COUNT(*) FROM c GROUP BY city ORDER BY 2 LIMIT 100") is None
    assert context_sql("SELECT a FROM t GROUP BY a UNION SELECT b FROM u GROUP BY b LIMIT 1") is None


def test_top_one_answer_is_highlighted_among_all_categories():
    from chart_builder import with_context

    answer = [{"category": "Sports", "payments": 5314.21}]
    context = {
        "rows": [
            {"category": "Sports", "payments": 5314.21},
            {"category": "Sci-Fi", "payments": 4756.98},
            {"category": "Music", "payments": 3417.72},
        ],
        "columns": ["category", "payments"],
        "picked": 1,
    }
    frame, plan = with_context(answer, context, "Which category earns the most?")
    assert plan.kind == BAR and plan.highlight == ["Sports"]
    assert summarize(frame, plan).startswith("Sports: 5.31K, rank 1 of 3")
    assert "Red: your answer" in plan.note
    spec = build_chart(frame, plan).to_dict()
    assert "condition" in str(spec["encoding"]["color"])


# ---------------------------------------------------------------
# Chart context from the database (needs INSIGHTX_TEST_DATABASE_URL)
# ---------------------------------------------------------------

import pytest
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text


@pytest.fixture
def chart_tables(engine):
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS chart_sales, chart_genre"))
        connection.execute(text("CREATE TABLE chart_genre (genre_id int PRIMARY KEY, name text)"))
        connection.execute(text("INSERT INTO chart_genre VALUES (1, 'Rock'), (2, 'Jazz'), (3, 'Pop')"))
        connection.execute(text(
            "CREATE TABLE chart_sales (sale_id int PRIMARY KEY, genre_id int REFERENCES chart_genre, "
            "title text, sale_date date, last_update timestamp, amount numeric(10,2))"
        ))
        connection.execute(text(
            "INSERT INTO chart_sales SELECT g, 1 + (g % 5) % 3, 'song ' || g, "
            "date '2024-01-01' + (g * 3), now(), g FROM generate_series(1, 120) g"
        ))
    yield engine
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS chart_sales, chart_genre"))


def _context(engine, sql):
    from backend.text_to_sql.chart_context import chart_context
    from backend.text_to_sql.query_executor import execute_query

    execution = execute_query(sql=sql, engine=engine)
    assert execution["success"], execution["error"]
    return execution, jsonable_encoder(chart_context(sql, execution, engine))


def test_single_total_gets_a_monthly_trend(chart_tables):
    execution, context = _context(chart_tables, "SELECT SUM(s.amount) AS total FROM chart_sales AS s")
    assert context["kind"] == "trend" and "sale date" in context["note"]
    assert len(context["rows"]) == 12  # one row per month, not last_update
    rows = jsonable_encoder(execution["rows"])
    from chart_builder import with_context

    frame, plan = with_context(rows, context, "total sales")
    assert plan.kind == LINE and plan.x == "month"
    assert sum(r["total"] for r in context["rows"]) == pytest.approx(float(rows[0]["total"]))


def test_total_without_dates_is_split_through_lookup_table(chart_tables):
    with chart_tables.begin() as connection:
        connection.execute(text("ALTER TABLE chart_sales DROP COLUMN sale_date, DROP COLUMN last_update"))
    _, context = _context(chart_tables, "SELECT COUNT(*) FROM chart_sales")
    assert context["kind"] == "split" and "genre" in context["note"]
    assert {row["genre"] for row in context["rows"]} == {"Rock", "Jazz", "Pop"}


def test_top_pick_that_selects_only_the_name_gets_its_measure(chart_tables):
    sql = (
        "SELECT g.name FROM chart_sales s JOIN chart_genre g ON g.genre_id = s.genre_id "
        "GROUP BY g.name ORDER BY SUM(s.amount) DESC LIMIT 1"
    )
    execution, context = _context(chart_tables, sql)
    assert context["kind"] == "top" and "total_amount" in context["columns"]
    from chart_builder import with_context

    frame, plan = with_context(jsonable_encoder(execution["rows"]), context, "")
    assert plan.kind == BAR and plan.highlight == [execution["rows"][0]["name"]]


def test_profit_without_cost_columns_asks_instead_of_answering():
    from backend.text_to_sql.agent import unsupported_metric

    assert "no cost" in unsupported_metric("What is the profit on DVD rentals", "SELECT SUM(amount) FROM dvd_payment")
    assert unsupported_metric("profit per product", "SELECT SUM(listprice - standardcost) FROM p") == ""
    assert unsupported_metric("total revenue", "SELECT SUM(amount) FROM dvd_payment") == ""
