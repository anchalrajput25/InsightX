# Trust layer: claim verification, join fan-out detection, trust score
# and their wiring into the agent. Synthetic data, several domains.

import uuid

import pytest
from sqlalchemy import text

from backend.text_to_sql.trust import (
    assess_trust,
    check_fanout,
    check_result_sanity,
    extract_numbers,
    fanout_probes,
    verify_claims,
)


def result(columns, *rows, **extra):
    return {"success": True, "columns": list(columns), "rows": [dict(zip(columns, r)) for r in rows],
            "row_count": len(rows), **extra}


# ============================================================
# CLAIM VERIFICATION
# ============================================================

def test_number_extraction_handles_formats():
    found = {(n["value"], n["decimals"], n["percent"], n["scale"]) for n in extract_numbers(
        "Revenue was 1,234,567.89 (about 1.2 million), up 12.5% from 15.5k; ranked #3."
    )}
    assert (1234567.89, 2, False, 1.0) in found
    assert (1.2, 1, False, 1e6) in found
    assert (12.5, 1, True, 1.0) in found
    assert (15.5, 1, False, 1e3) in found


def test_numbers_from_the_result_are_verified_with_rounding():
    res = result(["city", "customers", "avg_score", "share"], ("sao paulo", 15540, 4.0864, 0.1563))
    answer = "**sao paulo** has the most customers: 15,540 (about 15.5 thousand). Average score 4.09, a 15.6% share."
    check = verify_claims(answer, "Which city has the most customers?", res)
    assert check["status"] == "verified", check
    assert check["unverified_numbers"] == []


def test_invented_numbers_and_values_are_flagged():
    res = result(["category", "revenue"], ("toys", 1200.0), ("books", 800.0))
    answer = "**toys** leads with 1,200, which is 400 more than **garden** and 60% of the total."
    check = verify_claims(answer, "Top categories by revenue", res)
    assert check["status"] == "unverified"
    assert set(check["unverified_numbers"]) == {"400", "60%"}
    assert check["unverified_values"] == ["garden"]


def test_numbers_from_the_question_dates_and_lists_are_allowed():
    res = result(["month", "orders"], ("2017-11-01", 7544), ("2018-01-01", 7269))
    answer = "1. November 2017: 7,544 orders\n2. January 2018: 7,269 orders\nThese are the top 2 months."
    assert verify_claims(answer, "Top 2 months by orders", res)["status"] == "verified"


def test_no_rows_means_nothing_to_verify():
    assert verify_claims("No matching records.", "q", result(["n"]))["status"] == "not_applicable"


# ============================================================
# JOIN FAN-OUT
# ============================================================

SCHEMA = {
    "tables": [
        {"table_name": "invoices", "primary_key": ["invoice_id"], "columns": [
            {"column_name": "invoice_id"}, {"column_name": "shipping_fee"}, {"column_name": "client_id"}]},
        {"table_name": "invoice_lines", "primary_key": [], "columns": [
            {"column_name": "invoice_id"}, {"column_name": "amount"}]},
        {"table_name": "clients", "primary_key": [], "columns": [
            {"column_name": "client_id", "unique": True}, {"column_name": "region"}]},
    ],
}


def test_probes_target_aggregates_of_keyed_tables_only():
    probes = fanout_probes(
        "SELECT c.region, SUM(i.shipping_fee), SUM(l.amount), COUNT(*) FROM invoices i "
        "JOIN invoice_lines l ON l.invoice_id = i.invoice_id JOIN clients c ON c.client_id = i.client_id "
        "WHERE c.region <> 'x' GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
        SCHEMA,
    )
    # invoice_lines has no single key and COUNT(*) is not checked
    assert [probe["table"] for probe in probes] == ["invoices"]
    sql = probes[0]["sql"]
    assert "COUNT(DISTINCT i.invoice_id)" in sql and "WHERE" in sql
    assert "GROUP BY" not in sql and "LIMIT" not in sql
    assert fanout_probes("SELECT SUM(shipping_fee) FROM invoices", SCHEMA) == []
    assert fanout_probes("SELECT SUM(DISTINCT i.shipping_fee) FROM invoices i JOIN clients c USING (client_id)", SCHEMA) == []


@pytest.fixture
def fan_schema(engine):
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS itest_invoice_lines, itest_invoices"))
        connection.execute(text(
            "CREATE TABLE itest_invoices AS SELECT g AS invoice_id, 10.0 AS shipping_fee FROM generate_series(1, 50) g"
        ))
        connection.execute(text(
            "CREATE TABLE itest_invoice_lines AS SELECT 1 + g % 50 AS invoice_id, 2.0 AS amount "
            "FROM generate_series(1, 200) g"
        ))
    from backend.schema_profiler import get_database_schema
    from backend.text_to_sql.schema_context import build_agent_schema, cache_schema_from_profile

    profile = get_database_schema(engine)
    cache_schema_from_profile(engine, profile)
    yield build_agent_schema(profile)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS itest_invoice_lines, itest_invoices"))


FANOUT_SQL = ("SELECT SUM(i.shipping_fee) AS fees FROM itest_invoices i "
              "JOIN itest_invoice_lines l ON l.invoice_id = i.invoice_id")
FIXED_SQL = "SELECT SUM(shipping_fee) AS fees FROM itest_invoices"


def test_fanout_is_confirmed_on_real_data(engine, fan_schema):
    findings = check_fanout(FANOUT_SQL, fan_schema, engine)
    assert len(findings) == 1
    assert findings[0]["factor"] == 4.0 and findings[0]["distinct_keys"] == 50
    assert "double counting" in findings[0]["message"]
    assert check_fanout(FIXED_SQL, fan_schema, engine) == []
    sanity = check_result_sanity(FANOUT_SQL, result(["fees"], (2000.0,)), fan_schema, engine)
    assert sanity["fanout"] and sanity["warnings"] == []


def test_null_only_and_truncated_results_are_warned():
    sanity = check_result_sanity("", result(["avg"], (None,), truncated=True), {}, None)
    assert len(sanity["warnings"]) == 2


# ============================================================
# TRUST SCORE
# ============================================================

def test_trust_levels_follow_the_evidence():
    clean = assess_trust({
        "claim_check": {"status": "verified", "numbers": [{"text": "5", "verified": True}],
                        "unverified_numbers": [], "unverified_values": []},
        "sql_selection": {"executed_ok": 3, "clusters": [3]},
    })
    assert clean["level"] == "high" and clean["score"] == 100

    shaky = assess_trust({
        "claim_check": {"status": "verified", "numbers": [], "unverified_numbers": [], "unverified_values": []},
        "sql_selection": {"executed_ok": 3, "clusters": [1, 1, 1]},
        "correction_attempts": 1,
    })
    assert shaky["level"] == "medium" and shaky["score"] == 60

    bad = assess_trust({
        "claim_check": {"unverified_numbers": ["400"], "unverified_values": []},
        "sanity": {"fanout": [{"message": "double counting"}], "warnings": []},
    })
    assert bad["level"] == "low" and bad["score"] == 15
    assert {reason["signal"] for reason in bad["reasons"]} == {"answer", "sql"}


# ============================================================
# AGENT INTEGRATION
# ============================================================

def generator_output(sql):
    return f"STATUS: SUCCESS\nSQL:\n{sql}\nCONFIDENCE: High"


def ask(engine, question):
    from backend.text_to_sql.agent import run_text_to_sql
    return run_text_to_sql(question=question, engine=engine, thread_id=str(uuid.uuid4()))


def test_fanout_sends_sql_to_correction(engine, fan_schema, fake_llms, keyword_retrieval):
    seen = {}

    def corrector(prompt):
        seen["prompt"] = "\n".join(getattr(m, "content", str(m)) for m in prompt) if isinstance(prompt, list) else str(prompt)
        return FIXED_SQL

    llms = fake_llms(generator=[generator_output(FANOUT_SQL)], corrector=corrector,
                     answer=lambda prompt: "Total shipping fees are 500.")
    result_state = ask(engine, "What are the total shipping fees of all invoices?")
    assert result_state["status"] == "SUCCESS"
    assert llms["corrector"].calls == 1 and "double counting" in seen["prompt"]
    assert result_state["generated_sql"] == FIXED_SQL
    assert result_state["execution_result"]["rows"][0]["fees"] == 500
    assert result_state["trust"]["level"] == "high"  # -10 for the correction only
    assert result_state["sanity"]["fanout"] == []


def test_hallucinated_answer_is_regenerated(engine, fan_schema, fake_llms, keyword_retrieval):
    answers = ["Total shipping fees are 2,000, about 40 per invoice.", "Total shipping fees are 500."]
    llms = fake_llms(generator=[generator_output(FIXED_SQL)], answer=answers)
    result_state = ask(engine, "What are the total shipping fees of all invoices?")
    assert llms["answer"].calls == 2
    assert result_state["answer"] == "Total shipping fees are 500."
    assert result_state["claim_check"]["status"] == "verified" and result_state["claim_check"]["regenerated"]
    assert result_state["trust"]["level"] == "high"


def test_unfixable_hallucination_lowers_trust(engine, fan_schema, fake_llms, keyword_retrieval):
    llms = fake_llms(generator=[generator_output(FIXED_SQL)], answer=lambda prompt: "Fees were 999 last year.")
    result_state = ask(engine, "What are the total shipping fees of all invoices?")
    assert llms["answer"].calls == 2
    assert result_state["claim_check"]["unverified_numbers"] == ["999"]
    assert result_state["trust"]["level"] == "low"
