# The trust score must be low when every value in the result is empty.
from backend.text_to_sql.trust import assess_trust

EMPTY = {"rows": [{"current_year": None, "last_year": None, "change": None}], "columns": ["current_year", "last_year", "change"]}


def test_all_empty_result_is_low_trust():
    trust = assess_trust({"execution_result": EMPTY,
                          "sanity": {"warnings": ["The query returned only NULL: probably no rows matched the filters."]}})
    assert trust["level"] == "low"
    assert any("Every value in the result is empty" in r["detail"] for r in trust["reasons"])


def test_some_values_present_is_not_penalised():
    result = {"rows": [{"year": 2018, "orders": None}], "columns": ["year", "orders"]}
    trust = assess_trust({"execution_result": result})
    assert trust["score"] == 100


def test_no_rows_or_odd_rows_not_penalised():
    assert assess_trust({"execution_result": {"rows": []}})["score"] == 100
    assert assess_trust({"execution_result": {"rows": [[None, None]]}})["score"] == 100
