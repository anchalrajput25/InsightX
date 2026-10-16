from backend.text_to_sql.sql_corrector import correct_sql


question = """
Show the top 5 customers by number of orders.
"""


failed_sql = """
SELECT
    c.customer_id,
    COUNT(o.order_id) AS order_count
FROM olist_customers_dataset c
JOIN olist_orders_dataset o
    ON c.customer_id = o.customer_id
GROUP BY c.customer_id
ORDER BY order_count DESC
LIMITT 5;
"""


error_message = """
PostgreSQL syntax error:
syntax error at or near "LIMITT"
"""


schema = {
    "olist_customers_dataset": {
        "columns": [
            "customer_id",
            "customer_unique_id",
            "customer_zip_code_prefix",
            "customer_city",
            "customer_state",
        ]
    },
    "olist_orders_dataset": {
        "columns": [
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
        ]
    },
}


print("=" * 70)
print("SQL ERROR CORRECTION TEST")
print("=" * 70)


result = correct_sql(
    question=question,
    failed_sql=failed_sql,
    error_message=error_message,
    schema=schema,
)


print("\nSUCCESS:", result["success"])

print("\nCORRECTED SQL:")
print(result["sql"])


if result["error"]:

    print("\nERROR:")
    print(result["error"])