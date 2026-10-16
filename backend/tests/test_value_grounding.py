# Filter-value check against stored data (real PostgreSQL).

import pytest

from backend.text_to_sql.sql_safety import parse_sql
from backend.text_to_sql.value_grounding import check_filter_values, find_text_filters


def test_find_text_filters_handles_eq_in_and_aliases():
    ast = parse_sql(
        "SELECT * FROM itest_customers c WHERE c.customer_city = 'X' "
        "AND 'Y' = customer_id AND c.customer_city IN ('A', 'B') AND price > 5"
    )
    assert find_text_filters(ast) == [
        ("c", "customer_city", "X"),
        (None, "customer_id", "Y"),
        ("c", "customer_city", "A"),
        ("c", "customer_city", "B"),
    ]


@pytest.mark.parametrize(
    "sql, issue_values, suggestion",
    [
        ("SELECT 1 FROM itest_customers WHERE customer_city = 'São Paulo'", ["São Paulo"], "sao paulo"),
        ("SELECT 1 FROM itest_customers WHERE customer_city = 'SAO PAULO'", ["SAO PAULO"], "sao paulo"),
        ("SELECT 1 FROM itest_customers WHERE customer_city = 'curitba'", ["curitba"], "curitiba"),
        ("SELECT 1 FROM itest_customers c WHERE c.customer_city IN ('sao paulo', 'Rio de Janeiro')",
         ["Rio de Janeiro"], "rio de janeiro"),
    ],
)
def test_mismatched_values_get_stored_suggestions(engine, sql, issue_values, suggestion):
    result = check_filter_values(parse_sql(sql), engine)
    assert [issue["value"] for issue in result["issues"]] == issue_values
    assert suggestion in result["issues"][0]["suggestions"]


def test_exact_and_absent_values(engine):
    exact = check_filter_values(parse_sql("SELECT 1 FROM itest_customers WHERE customer_city = 'sao paulo'"), engine)
    assert exact == {"issues": [], "unmatched": []}

    absent = check_filter_values(parse_sql("SELECT 1 FROM itest_customers WHERE customer_city = 'Atlantis'"), engine)
    assert absent["issues"] == []
    assert [item["value"] for item in absent["unmatched"]] == ["Atlantis"]


def test_non_text_columns_are_not_checked(engine):
    result = check_filter_values(parse_sql("SELECT 1 FROM itest_orders WHERE price = '12.00'"), engine)
    assert result == {"issues": [], "unmatched": []}
