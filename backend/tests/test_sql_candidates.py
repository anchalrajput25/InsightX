# Multiple SQL candidates with execution-based self-consistency.

import time

import pytest

from backend.text_to_sql.sql_candidates import run_parallel, select_candidate


# ============================================================
# PARALLEL CALLS
# ============================================================

def test_parallel_waits_for_primary_and_bounds_the_others():
    def primary():
        time.sleep(0.05)
        return "p"

    def boom():
        raise RuntimeError("rate limited")

    def slow():
        time.sleep(2)
        return "late"

    started = time.monotonic()
    results = run_parallel([primary, boom, slow, lambda: "fast"], wait_s=0.2)
    assert time.monotonic() - started < 1.5
    assert results[0] == "p"
    assert isinstance(results[1], RuntimeError)
    assert results[2] is None  # timed out, ignored
    assert results[3] == "fast"


# ============================================================
# SELECTION (scripted evaluation, no database)
# ============================================================

def ok(columns, *rows):
    return {"status": "ok", "result": {
        "success": True, "columns": list(columns), "row_count": len(rows),
        "rows": [dict(zip(columns, row)) for row in rows],
    }}


def scripted(outcomes):
    calls = []

    def evaluate(sql, engine, max_rows):
        calls.append(sql)
        return outcomes[sql]
    return evaluate, calls


def cands(*sqls):
    return [{"index": i, "sql": sql} for i, sql in enumerate(sqls)]


def test_majority_result_wins_over_the_primary():
    evaluate, _ = scripted({
        "q0": ok(["n"], (10,)),
        "q1": ok(["total"], (12,)),
        "q2": ok(["cnt"], (12,)),
    })
    outcome = select_candidate(cands("q0", "q1", "q2"), None, 100, evaluate)
    assert outcome["winner"]["index"] == 1
    assert outcome["selection"]["agreement"] == pytest.approx(2 / 3, abs=1e-3)
    assert outcome["selection"]["clusters"] == [2, 1]
    assert outcome["execution"]["rows"] == [{"total": 12}]


def test_extra_columns_still_agree_and_ties_go_to_the_primary():
    evaluate, _ = scripted({
        "q0": ok(["city"], ("lisboa",)),
        "q1": ok(["city", "customers"], ("lisboa", 40)),
        "q2": ok(["city"], ("porto",)),
        "q3": ok(["town"], ("porto",)),
    })
    outcome = select_candidate(cands("q0", "q1", "q2", "q3"), None, 100, evaluate)
    assert outcome["winner"]["index"] == 0
    assert outcome["selection"]["clusters"] == [2, 2]


def test_identical_sql_runs_once_but_votes_twice():
    evaluate, calls = scripted({
        "SELECT 1": ok(["a"], (1,)),
        "SELECT 2": ok(["a"], (2,)),
    })
    outcome = select_candidate(
        [{"index": 0, "sql": "SELECT 2"}, {"index": 1, "sql": "select   1"}, {"index": 2, "sql": "SELECT 1"}],
        None, 100,
        lambda sql, engine, max_rows: evaluate("SELECT 1" if "1" in sql else "SELECT 2", engine, max_rows),
    )
    assert len(calls) == 2
    assert outcome["winner"]["index"] == 1
    assert outcome["selection"]["unique_sql"] == 2 and outcome["selection"]["executed_ok"] == 3


def test_non_empty_results_beat_an_empty_majority():
    evaluate, _ = scripted({
        "q0": ok(["n"]),
        "q1": ok(["n"]),
        "q2": ok(["n"], (5,)),
    })
    assert select_candidate(cands("q0", "q1", "q2"), None, 100, evaluate)["winner"]["index"] == 2


def test_failed_candidates_do_not_vote_and_none_ok_gives_no_winner():
    evaluate, _ = scripted({
        "q0": {"status": "invalid", "error": "column x does not exist"},
        "q1": ok(["n"], (3,)),
        "q2": {"status": "unsafe", "error": "DELETE"},
    })
    outcome = select_candidate(cands("q0", "q1", "q2"), None, 100, evaluate)
    assert outcome["winner"]["index"] == 1
    statuses = {item["index"]: item["status"] for item in outcome["summary"]}
    assert statuses == {0: "invalid", 1: "ok", 2: "unsafe"}

    evaluate, _ = scripted({"q0": {"status": "failed", "error": "timeout"}})
    outcome = select_candidate(cands("q0"), None, 100, evaluate)
    assert outcome["winner"] is None and outcome["execution"] is None


# ============================================================
# REAL EVALUATION (database)
# ============================================================

def test_real_candidates_pass_the_safety_gate_and_validation(engine):
    outcome = select_candidate(cands(
        "SELECT customer_city, COUNT(*) FROM itest_customers GROUP BY 1 ORDER BY 2 DESC LIMIT 1",
        "DELETE FROM itest_customers",
        "SELECT customer_town FROM itest_customers",
        "WITH c AS (SELECT customer_city AS city, COUNT(*) AS n FROM itest_customers GROUP BY customer_city) "
        "SELECT city FROM c ORDER BY n DESC LIMIT 1",
    ), engine, 100)
    statuses = {item["index"]: item["status"] for item in outcome["summary"]}
    assert statuses == {0: "ok", 1: "unsafe", 2: "invalid", 3: "ok"}
    assert outcome["winner"]["index"] == 0
    assert outcome["selection"]["agreement"] == 1.0
    assert outcome["execution"]["rows"][0]["customer_city"] == "sao paulo"
