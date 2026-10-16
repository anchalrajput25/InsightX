# MySQL / MariaDB support.
#
# The pure-Python checks always run. Database tests need a disposable
# MySQL database and are skipped otherwise:
#     INSIGHTX_TEST_MYSQL_URL=mysql+pymysql://user:pass@127.0.0.1:3306/insightx_test

import os
import uuid

import pytest
from fastapi.encoders import jsonable_encoder
from sqlalchemy import create_engine, text

from backend import dialects
from backend.text_to_sql.sql_safety import check_sql_safety

MYSQL_ENV = "INSIGHTX_TEST_MYSQL_URL"


def mysql_safety(sql):
    with dialects.using("mysql"):
        return check_sql_safety(sql)


# ---------------------------------------------------------------
# Without a database
# ---------------------------------------------------------------

def test_mysql_sql_with_backticks_and_functions_is_safe():
    for sql in (
        "SELECT `g`.`name`, COUNT(*) AS n FROM `mtest_genre` AS g GROUP BY g.name ORDER BY n DESC LIMIT 5",
        "SELECT DATE_FORMAT(sale_date, '%Y-%m') AS m, SUM(amount) FROM mtest_sales GROUP BY 1",
        "SELECT CONCAT(first, ' ', last) FROM people WHERE note = 'it\\'s fine' # comment",
        "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    ):
        assert mysql_safety(sql)["safe"], (sql, mysql_safety(sql)["errors"])


def test_mysql_dangerous_sql_is_blocked():
    for sql in (
        "SELECT SLEEP(100)",
        "SELECT BENCHMARK(100000000, MD5('a'))",
        "SELECT LOAD_FILE('/etc/passwd')",
        "SELECT * FROM mtest_sales INTO OUTFILE '/tmp/x.csv'",
        "SELECT user, authentication_string FROM mysql.user",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM other_db.customers",
        "SELECT 1; DROP TABLE mtest_sales",
        "SELECT GET_LOCK('a', 10)",
    ):
        assert not mysql_safety(sql)["safe"], sql


def test_mysql_period_and_prompt_adaptation():
    assert dialects.period("month", "d") == "date_trunc('month', d)"
    with dialects.using("mysql"):
        assert "DATE_FORMAT(d, '%Y-%m-01')" in dialects.period("month", "d")
        prompt = dialects.adapt_text("Write one PostgreSQL statement.")
        assert prompt.startswith("Write one MySQL statement.") and "DATABASE ENGINE: MySQL" in prompt
        assert dialects.adapt_text("Summarise these rows.") == "Summarise these rows."
    assert dialects.adapt_text("Write one PostgreSQL statement.") == "Write one PostgreSQL statement."


# ---------------------------------------------------------------
# With a MySQL database
# ---------------------------------------------------------------

@pytest.fixture(scope="module")
def mysql_engine():
    url = os.getenv(MYSQL_ENV)
    if not url:
        pytest.skip(f"{MYSQL_ENV} is not set; skipping MySQL tests.")
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS mtest_sales, mtest_genre"))
        connection.execute(text("CREATE TABLE mtest_genre (genre_id INT PRIMARY KEY, name VARCHAR(40))"))
        connection.execute(text("INSERT INTO mtest_genre VALUES (1, 'Rock'), (2, 'Jazz'), (3, 'Pop')"))
        connection.execute(text(
            "CREATE TABLE mtest_sales (sale_id INT PRIMARY KEY, genre_id INT, title VARCHAR(60), "
            "sale_date DATETIME, amount DECIMAL(10,2), "
            "FOREIGN KEY (genre_id) REFERENCES mtest_genre (genre_id))"
        ))
        rows = [
            {"id": g, "genre": 1 + (g % 5) % 3, "title": f"song {g}",
             "day": f"2024-{1 + (g * 3 // 31) % 12:02d}-{1 + (g * 3) % 28:02d} 10:00:00", "amount": g}
            for g in range(1, 121)
        ]
        connection.execute(
            text("INSERT INTO mtest_sales VALUES (:id, :genre, :title, :day, :amount)"), rows
        )
    yield engine
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS mtest_sales, mtest_genre"))
    engine.dispose()


def test_queries_run_read_only_with_a_time_limit(mysql_engine):
    from backend.text_to_sql.query_executor import execute_query

    with dialects.using(mysql_engine):
        result = execute_query(sql="SELECT COUNT(*) AS n FROM mtest_sales", engine=mysql_engine)
        assert result["success"] and result["rows"][0]["n"] == 120
        blocked = execute_query(sql="DELETE FROM mtest_sales", engine=mysql_engine)
        assert not blocked["success"]
    with mysql_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM mtest_sales")).scalar() == 120


def test_safety_allows_the_connected_database_by_name(mysql_engine):
    database = mysql_engine.url.database
    with dialects.using(mysql_engine):
        assert check_sql_safety(f"SELECT COUNT(*) FROM `{database}`.mtest_sales")["safe"]
        assert not check_sql_safety("SELECT COUNT(*) FROM mysql.user")["safe"]


def test_every_time_grain_runs(mysql_engine):
    with mysql_engine.connect() as connection:
        for grain in ("day", "week", "month", "quarter", "year"):
            sql = (f"SELECT {dialects.period(grain, 'sale_date', mysql_engine)} AS p, COUNT(*) "
                   "FROM mtest_sales GROUP BY 1 ORDER BY 1")
            rows = connection.execute(text(sql)).fetchall()
            assert rows and rows[0][0] is not None, grain
        seconds = dialects.seconds_between("'2024-01-02'", "'2024-01-01'", mysql_engine)
        assert connection.execute(text(f"SELECT {seconds}")).scalar() == 86400


def test_profile_uses_the_database_as_schema(mysql_engine):
    from backend.schema_profiler import get_database_schema

    profile = get_database_schema(mysql_engine)
    assert profile["schema_name"] == mysql_engine.url.database
    names = {table["table_name"] for table in profile["tables"]}
    assert {"mtest_sales", "mtest_genre"} <= names
    assert any(
        r["child_table"] == "mtest_sales" and r["parent_table"] == "mtest_genre"
        for r in profile["relationships"]
    )


def test_charts_work_on_mysql(mysql_engine):
    from backend.text_to_sql.chart_context import chart_context
    from backend.text_to_sql.query_executor import execute_query

    with dialects.using(mysql_engine):
        sql = "SELECT SUM(s.amount) AS total FROM mtest_sales AS s"
        execution = execute_query(sql=sql, engine=mysql_engine)
        context = jsonable_encoder(chart_context(sql, execution, mysql_engine))
        assert context["kind"] == "trend" and len(context["rows"]) == 12
        assert sum(row["total"] for row in context["rows"]) == pytest.approx(float(execution["rows"][0]["total"]))

        sql = ("SELECT g.name, SUM(s.amount) AS total FROM mtest_sales s JOIN mtest_genre g "
               "ON g.genre_id = s.genre_id GROUP BY g.name ORDER BY total DESC LIMIT 1")
        context = jsonable_encoder(chart_context(sql, execute_query(sql=sql, engine=mysql_engine), mysql_engine))
        assert context["kind"] == "top" and len(context["rows"]) == 3


def test_misspelled_value_gets_a_suggestion(mysql_engine):
    import sqlglot

    from backend.text_to_sql.value_grounding import check_filter_values

    with dialects.using(mysql_engine):
        ast = sqlglot.parse_one(
            "SELECT COUNT(*) FROM mtest_sales s JOIN mtest_genre g ON g.genre_id = s.genre_id WHERE g.name = 'Rok'",
            read="mysql",
        )
        report = check_filter_values(ast, mysql_engine)
    assert report["issues"] and report["issues"][0]["suggestions"] == ["Rock"]


def test_full_question_on_mysql(mysql_engine, fake_llms, keyword_retrieval):
    from backend.schema_profiler import get_database_schema
    from backend.text_to_sql import agent
    from backend.text_to_sql.agent import run_text_to_sql
    from backend.text_to_sql.schema_context import cache_schema_from_profile

    cache_schema_from_profile(mysql_engine, get_database_schema(mysql_engine))
    seen = []

    def generator(prompt):
        seen.append(dialects.current())
        return (
            "STATUS: SUCCESS\nSQL:\nSELECT `g`.`name`, SUM(s.amount) AS total FROM mtest_sales s "
            "JOIN mtest_genre g ON g.genre_id = s.genre_id GROUP BY g.name ORDER BY total DESC LIMIT 1\n"
            "CONFIDENCE: High"
        )

    fake_llms(generator=generator, linker=lambda prompt: "Final Relevant Schema:\nmtest_sales(amount)")
    agent._memory_saver = None
    thread = str(uuid.uuid4())
    result = run_text_to_sql("Which genre sells the most?", mysql_engine, thread)
    assert result["status"] == "SUCCESS", result.get("error")
    assert result["execution_result"]["rows"][0]["name"] in {"Rock", "Jazz", "Pop"}
    assert seen and set(seen) == {dialects.MYSQL}
    assert dialects.current() == dialects.POSTGRES  # reset after the question

    # The conversation is remembered for the next question.
    state = agent.get_thread_state(mysql_engine, thread)
    assert state.get("question") == "Which genre sells the most?"


def test_connect_endpoint_accepts_mysql(mysql_engine):
    from fastapi.testclient import TestClient

    from backend.main import app

    url = mysql_engine.url
    form = {
        "host": url.host, "port": str(url.port or 3306), "database": url.database,
        "username": url.username, "password": url.password, "db_type": "mysql",
    }
    with TestClient(app) as client:
        response = client.post("/connect-postgres", data=form)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source_type"] == "mysql" and body["message"] == "MySQL connected successfully."
        bad = client.post("/connect-postgres", data={**form, "password": "wrong-password"})
        assert bad.status_code == 401 and "wrong-password" not in bad.text
