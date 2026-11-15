from backend.text_to_sql.sql_safety import check_sql_safety


def run_test(name: str, sql: str):
    print("\n" + "=" * 70)
    print(name)
    print("=" * 70)

    result = check_sql_safety(sql)

    print("STATUS :", result["status"])
    print("SAFE   :", result["safe"])
    print("REASON :", result["reason"])

    if result["errors"]:
        print("ERRORS:")

        for error in result["errors"]:
            print(" -", error)

    if result["warnings"]:
        print("WARNINGS:")

        for warning in result["warnings"]:
            print(" -", warning)


# Safe

run_test(
    "SAFE SELECT",
    """
    SELECT customer_id, customer_city
    FROM olist_customers_dataset
    LIMIT 10;
    """
)


# Safe complex query

run_test(
    "SAFE COMPLEX QUERY",
    """
    WITH customer_orders AS (
        SELECT
            customer_id,
            COUNT(*) AS order_count
        FROM olist_orders_dataset
        GROUP BY customer_id
    ),
    ranked AS (
        SELECT
            customer_id,
            order_count,
            RANK() OVER (
                ORDER BY order_count DESC
            ) AS ranking
        FROM customer_orders
    )
    SELECT
        customer_id,
        order_count,
        ranking
    FROM ranked
    ORDER BY ranking
    LIMIT 10;
    """
)


# Unsafe insert

run_test(
    "UNSAFE INSERT",
    """
    INSERT INTO customers
    VALUES (1, 'Test');
    """
)


# Unsafe update

run_test(
    "UNSAFE UPDATE",
    """
    UPDATE customers
    SET customer_city = 'Delhi';
    """
)


# Unsafe DELETE

run_test(
    "UNSAFE DELETE",
    """
    DELETE FROM customers;
    """
)


# Unsafe drop

run_test(
    "UNSAFE DROP",
    """
    DROP TABLE customers;
    """
)


# Unsafe truncate

run_test(
    "UNSAFE TRUNCATE",
    """
    TRUNCATE TABLE customers;
    """
)


# Unsafe multiple statements

run_test(
    "UNSAFE MULTIPLE STATEMENTS",
    """
    SELECT * FROM customers;
    DROP TABLE customers;
    """
)


# Unsafe grant

run_test(
    "UNSAFE GRANT",
    """
    GRANT SELECT ON customers TO public;
    """
)