# SQLite and SQL Server support.
#
# SQLite tests always run (a temporary .db file). SQL Server tests need
# a disposable SQL Server database and are skipped otherwise:
#     INSIGHTX_TEST_MSSQL_URL=mssql+pymssql://sa:pass@127.0.0.1:1433/insightx_test

import os
import uuid
from datetime import datetime

import pytest
import sqlglot
from fastapi.encoders import jsonable_encoder
from sqlalchemy import (Column, DateTime, ForeignKey, Integer, MetaData, Numeric, String, Table, create_engine,
                        func, insert, select, text)

from backend import dialects
from backend.text_to_sql.sql_safety import check_sql_safety

MSSQL_ENV = "INSIGHTX_TEST_MSSQL_URL"


def safety(kind, sql):
    with dialects.using(kind):
        return check_sql_safety(sql)


# ---------------------------------------------------------------
# Without a database
# ---------------------------------------------------------------

def test_sql_server_sql_is_safe():
    for sql in (
        "SELECT TOP 5 [g].[name], COUNT(*) AS n FROM [ztest_genre] AS g GROUP BY g.name ORDER BY n DESC",
        "SELECT FORMAT(sale_date, 'yyyy-MM') AS m, SUM(amount) FROM dbo.ztest_sales GROUP BY FORMAT(sale_date, 'yyyy-MM')",
        "SELECT name FROM ztest_genre WHERE name = N'Rock' -- comment",
        "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
        "SELECT name FROM ztest_genre ORDER BY name OFFSET 0 ROWS FETCH NEXT 5 ROWS ONLY",
    ):
        assert safety(dialects.MSSQL, sql)["safe"], (sql, safety(dialects.MSSQL, sql)["errors"])


def test_sql_server_dangerous_sql_is_blocked():
    for sql in (
        "EXEC xp_cmdshell 'dir'",
        "SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x;', 'SELECT 1')",
        "SELECT * FROM OPENQUERY(other, 'SELECT 1')",
        "WAITFOR DELAY '00:01:00'",
        "SELECT * INTO copy_table FROM ztest_sales",
        "SELECT name FROM sys.sql_logins",
        "SELECT * FROM master.dbo.sysdatabases",
        "SELECT 1; DROP TABLE ztest_sales",
        "SHUTDOWN",
        "DBCC CHECKDB",
    ):
        assert not safety(dialects.MSSQL, sql)["safe"], sql


def test_sqlite_sql_is_safe_and_dangerous_sql_is_blocked():
    assert safety(dialects.SQLITE, "SELECT strftime('%Y-%m', sale_date) AS m, SUM(amount) FROM ztest_sales GROUP BY 1")["safe"]
    assert safety(dialects.SQLITE, "SELECT name FROM main.ztest_genre LIMIT 3")["safe"]
    for sql in (
        "ATTACH DATABASE '/etc/passwd' AS x",
        "PRAGMA writable_schema = ON",
        "SELECT load_extension('evil')",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM other.ztest_sales",
    ):
        assert not safety(dialects.SQLITE, sql)["safe"], sql


def test_prompts_are_adapted_per_database():
    with dialects.using(dialects.MSSQL):
        prompt = dialects.adapt_text("Write one PostgreSQL statement.")
        assert prompt.startswith("Write one SQL Server statement.") and "DATABASE ENGINE: Microsoft SQL Server" in prompt
    with dialects.using(dialects.SQLITE):
        assert "DATABASE ENGINE: SQLite" in dialects.adapt_text("Write one PostgreSQL statement.")


def test_positional_group_by_is_rewritten_for_sql_server():
    sql = "SELECT YEAR(d) AS y, COUNT(*) AS n FROM t GROUP BY 1 ORDER BY 1"
    with dialects.using(dialects.MSSQL):
        fixed = dialects.prepare_sql(sql)
    assert "GROUP BY YEAR(d)" in fixed and "GROUP BY 1" not in fixed
    assert dialects.prepare_sql(sql) == sql  # PostgreSQL: unchanged


# ---------------------------------------------------------------
# With a database
# ---------------------------------------------------------------

metadata = MetaData()
genre = Table("ztest_genre", metadata, Column("genre_id", Integer, primary_key=True, autoincrement=False),
              Column("name", String(40)))
sales = Table("ztest_sales", metadata, Column("sale_id", Integer, primary_key=True, autoincrement=False),
              Column("genre_id", Integer, ForeignKey("ztest_genre.genre_id")), Column("title", String(60)),
              Column("sale_date", DateTime), Column("amount", Numeric(10, 2)))


def fill(engine):
    metadata.drop_all(engine)
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(genre), [{"genre_id": 1, "name": "Rock"}, {"genre_id": 2, "name": "Jazz"},
                                           {"genre_id": 3, "name": "Pop"}])
        connection.execute(insert(sales), [
            {"sale_id": g, "genre_id": 1 + (g % 5) % 3, "title": f"song {g}",
             "sale_date": datetime(2024, 1 + (g * 3 // 31) % 12, 1 + (g * 3) % 28, 10), "amount": g}
            for g in range(1, 121)
        ])


@pytest.fixture(scope="module", params=[dialects.SQLITE, dialects.MSSQL])
def db(request, tmp_path_factory):
    from backend.database import create_database_engine

    if request.param == dialects.SQLITE:
        path = tmp_path_factory.mktemp("sqlite") / "music.db"
        fill(create_engine(f"sqlite:///{path.as_posix()}"))
        engine = create_database_engine(dialects.SQLITE, host="", port=0, database=str(path), username="",
                                        password="")
        yield engine
        engine.dispose()
        return
    url = os.getenv(MSSQL_ENV)
    if not url:
        pytest.skip(f"{MSSQL_ENV} is not set; skipping SQL Server tests.")
    writer = create_engine(url)
    fill(writer)
    engine = create_database_engine(dialects.MSSQL, host=writer.url.host, port=writer.url.port or 1433,
                                    database=writer.url.database, username=writer.url.username,
                                    password=writer.url.password)
    yield engine
    engine.dispose()
    metadata.drop_all(writer)
    writer.dispose()


def test_queries_run_read_only(db):
    from backend.text_to_sql.query_executor import execute_query

    with dialects.using(db):
        result = execute_query(sql="SELECT COUNT(*) AS n FROM ztest_sales", engine=db)
        assert result["success"] and result["rows"][0]["n"] == 120
        assert not execute_query(sql="DELETE FROM ztest_sales", engine=db)["success"]
    with db.connect() as connection:
        assert connection.execute(select(func.count()).select_from(sales)).scalar() == 120


def test_writes_are_refused_by_the_database_itself(db):
    from backend.text_to_sql.query_executor import read_only_connection

    if dialects.kind(db) == dialects.MSSQL:
        pytest.skip("SQL Server has no read-only transaction; the safety check and a rollback protect it.")
    with pytest.raises(Exception):
        with read_only_connection(db) as connection:
            connection.exec_driver_sql("DELETE FROM ztest_sales")
    with db.connect() as connection:
        assert connection.execute(select(func.count()).select_from(sales)).scalar() == 120


def test_validation_catches_unknown_tables(db):
    from backend.text_to_sql.sql_validator import postgres_explain_validation as explain_query

    with dialects.using(db):
        assert explain_query("SELECT COUNT(*) FROM ztest_sales", db)["valid"]
        assert not explain_query("SELECT COUNT(*) FROM no_such_table", db)["valid"]


def test_every_time_grain_runs(db):
    from backend.text_to_sql.query_executor import execute_query

    with dialects.using(db):
        for grain in ("day", "week", "month", "quarter", "year"):
            period = dialects.period(grain, "sale_date", db)
            result = execute_query(sql=f"SELECT {period} AS p, COUNT(*) AS n FROM ztest_sales GROUP BY 1 ORDER BY 1",
                                   engine=db)
            assert result["success"] and result["rows"][0]["p"] is not None, (grain, result.get("error"))
        result = execute_query(
            sql=f"SELECT {dialects.period('month', 'sale_date', db)} AS p, COUNT(*) AS n FROM ztest_sales "
                f"WHERE {dialects.period('month', 'sale_date', db)} IN "
                f"({dialects.timestamp_literal('2024-02-01 00:00:00', db)}) GROUP BY 1",
            engine=db)
        assert result["success"] and len(result["rows"]) == 1, result.get("error")
        seconds = dialects.seconds_between(dialects.timestamp_literal("2024-01-02", db),
                                           dialects.timestamp_literal("2024-01-01", db), db)
        assert execute_query(sql=f"SELECT {seconds} AS s", engine=db)["rows"][0]["s"] == 86400


def test_profile_finds_tables_and_links(db):
    from backend.schema_profiler import get_database_schema

    profile = get_database_schema(db)
    names = {table["table_name"] for table in profile["tables"]}
    assert {"ztest_sales", "ztest_genre"} <= names
    assert any(r["child_table"] == "ztest_sales" and r["parent_table"] == "ztest_genre"
               for r in profile["relationships"])


def test_charts_work(db):
    from backend.text_to_sql.chart_context import chart_context
    from backend.text_to_sql.query_executor import execute_query

    with dialects.using(db):
        sql = "SELECT SUM(s.amount) AS total FROM ztest_sales AS s"
        execution = execute_query(sql=sql, engine=db)
        context = jsonable_encoder(chart_context(sql, execution, db))
        assert context["kind"] == "trend" and len(context["rows"]) == 12, context
        assert sum(row["total"] for row in context["rows"]) == pytest.approx(float(execution["rows"][0]["total"]))

        top = "TOP 1 " if dialects.kind(db) == dialects.MSSQL else ""
        limit = "" if top else " LIMIT 1"
        sql = (f"SELECT {top}g.name, SUM(s.amount) AS total FROM ztest_sales s JOIN ztest_genre g "
               f"ON g.genre_id = s.genre_id GROUP BY g.name ORDER BY total DESC{limit}")
        context = jsonable_encoder(chart_context(sql, execute_query(sql=sql, engine=db), db))
        assert context["kind"] == "top" and len(context["rows"]) == 3, context


def test_misspelled_value_gets_a_suggestion(db):
    from backend.text_to_sql.value_grounding import check_filter_values

    with dialects.using(db):
        ast = sqlglot.parse_one(
            "SELECT COUNT(*) FROM ztest_sales s JOIN ztest_genre g ON g.genre_id = s.genre_id WHERE g.name = 'Rok'",
            read=dialects.sqlglot_name(),
        )
        report = check_filter_values(ast, db)
        assert report["issues"] and report["issues"][0]["suggestions"] == ["Rock"]
        ast = sqlglot.parse_one("SELECT COUNT(*) FROM ztest_genre g WHERE g.name = 'rock'", read=dialects.sqlglot_name())
        report = check_filter_values(ast, db)
    assert not report["issues"] or report["issues"][0]["suggestions"] == ["Rock"]


def test_full_question(db, fake_llms, keyword_retrieval):
    from backend.schema_profiler import get_database_schema
    from backend.text_to_sql import agent
    from backend.text_to_sql.agent import run_text_to_sql
    from backend.text_to_sql.schema_context import cache_schema_from_profile

    cache_schema_from_profile(db, get_database_schema(db))
    seen = []
    top = "TOP 1 " if dialects.kind(db) == dialects.MSSQL else ""
    limit = "" if top else " LIMIT 1"

    def generator(prompt):
        seen.append(dialects.current())
        return (f"STATUS: SUCCESS\nSQL:\nSELECT {top}g.name, SUM(s.amount) AS total FROM ztest_sales s "
                f"JOIN ztest_genre g ON g.genre_id = s.genre_id GROUP BY g.name ORDER BY total DESC{limit}\n"
                "CONFIDENCE: High")

    fake_llms(generator=generator, linker=lambda prompt: "Final Relevant Schema:\nztest_sales(amount)")
    agent._memory_saver = None
    thread = str(uuid.uuid4())
    result = run_text_to_sql("Which genre sells the most?", db, thread)
    assert result["status"] == "SUCCESS", result.get("error")
    assert result["execution_result"]["rows"][0]["name"] in {"Rock", "Jazz", "Pop"}
    assert seen and set(seen) == {dialects.kind(db)}
    assert dialects.current() == dialects.POSTGRES
    assert agent.get_thread_state(db, thread).get("question") == "Which genre sells the most?"


def test_investigation_runs(db, keyword_retrieval):
    import json

    from langchain_core.messages import AIMessage

    from backend.investigation.service import run_investigation

    class Scripted:
        def __init__(self, *responses):
            self.responses = list(responses)

        def invoke(self, prompt):
            return AIMessage(content=self.responses.pop(0))

    plan = {"metric_name": "sales", "metric_expression": "SUM(ztest_sales.amount)", "fact_table": "ztest_sales",
            "time_column": "ztest_sales.sale_date", "grain": "month", "direction": "increase",
            "dimensions": ["ztest_genre.name"], "filters": None}
    narrator = Scripted(json.dumps({"summary": "Sales changed.", "drivers": [], "hypotheses": [], "next_steps": []}))
    result = run_investigation("Why did sales go up?", db, planner_model=Scripted(json.dumps(plan)),
                               narrative_model=narrator)
    assert result["status"] == "SUCCESS", result
    assert result["queries"] and result["drivers"]


def test_connect_endpoints(db):
    from fastapi.testclient import TestClient

    from backend import main

    with TestClient(main.app) as client:
        if dialects.kind(db) == dialects.SQLITE:
            with open(db.url.database, "rb") as handle:
                response = client.post("/connect-sqlite", files={"file": ("music.db", handle)})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["source_type"] == "sqlite" and "music.db" in body["message"]
            saved = main.POSTGRES_SESSIONS[body["connection_id"]]["database"]
            assert os.path.exists(saved)

            # After a restart the uploaded file is opened again.
            main.POSTGRES_SESSIONS.pop(body["connection_id"])["engine"].dispose()
            assert main.get_connection_session(body["connection_id"])["source_type"] == "sqlite"

            assert client.delete(f"/disconnect-postgres/{body['connection_id']}").status_code == 200
            assert not os.path.exists(saved)

            bad = client.post("/connect-sqlite", files={"file": ("notes.db", b"just some text, not a database")})
            assert bad.status_code == 400
            return

        url = db.url
        form = {"host": url.host, "port": str(url.port or 1433), "database": url.database,
                "username": url.username, "password": url.password, "db_type": "sqlserver"}
        response = client.post("/connect-postgres", data=form)
        assert response.status_code == 200, response.text
        assert response.json()["message"] == "SQL Server connected successfully."
        assert any(t["table_name"] == "ztest_sales" for t in response.json()["schema"]["tables"])
        bad = client.post("/connect-postgres", data={**form, "password": "wrong-password"})
        assert bad.status_code == 401 and "wrong-password" not in bad.text
        assert client.post("/connect-postgres", data={**form, "db_type": "oracle"}).status_code == 400
