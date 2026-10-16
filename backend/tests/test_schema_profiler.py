# Guessed (inferred) table links: on a database that holds several apps
# side by side, a shared column name like customer_id must not link
# tables that the declared foreign keys keep apart.

import logging

import pytest
from sqlalchemy import text

from backend.schema_profiler import declared_groups, get_database_schema

SCHEMA = "itest_profile"


@pytest.fixture
def two_apps(engine):
    with engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        for statement in (
            # app a and app b both have customers 1..20 and orders/invoices pointing at them
            "CREATE TABLE {s}.a_customers (customer_id int PRIMARY KEY, name text)",
            "CREATE TABLE {s}.a_orders (order_id int PRIMARY KEY, customer_id int REFERENCES {s}.a_customers)",
            "CREATE TABLE {s}.b_customers (customer_id int PRIMARY KEY, name text)",
            "CREATE TABLE {s}.b_invoices (invoice_id int PRIMARY KEY, customer_id int REFERENCES {s}.b_customers)",
            "INSERT INTO {s}.a_customers SELECT g, 'a' || g FROM generate_series(1, 20) g",
            "INSERT INTO {s}.b_customers SELECT g, 'b' || g FROM generate_series(1, 30) g",
            "INSERT INTO {s}.a_orders SELECT g, 1 + g % 20 FROM generate_series(1, 50) g",
            "INSERT INTO {s}.b_invoices SELECT g, 1 + g % 30 FROM generate_series(1, 50) g",
            # no foreign keys: links must still be guessed
            "CREATE TABLE {s}.c_users AS SELECT g AS user_id FROM generate_series(1, 10) g",
            "CREATE TABLE {s}.c_events AS SELECT g AS event_id, 1 + g % 10 AS user_id FROM generate_series(1, 40) g",
            # a measurement whose small numbers happen to match ids
            "CREATE TABLE {s}.d_rentals (rental_id int PRIMARY KEY)",
            "INSERT INTO {s}.d_rentals SELECT g FROM generate_series(1, 100) g",
            "CREATE TABLE {s}.d_films AS SELECT g AS film_id, 3 + g % 5 AS rental_duration FROM generate_series(1, 30) g",
            # same name, different types: skipped, not a failed query
            "CREATE TABLE {s}.e_customers AS SELECT 'x' || g AS customer_id FROM generate_series(1, 5) g",
        ):
            connection.execute(text(statement.format(s=SCHEMA)))
    yield engine
    with engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))


def test_guessed_links_respect_declared_groups(two_apps, caplog):
    with caplog.at_level(logging.WARNING):
        profile = get_database_schema(two_apps, schema_name=SCHEMA)
    guessed = {
        (r["child_table"], r["child_column"], r["parent_table"])
        for r in profile["relationships"] if r["relationship_type"] == "inferred"
    }
    assert ("c_events", "user_id", "c_users") in guessed
    assert not [link for link in guessed if {link[0][0], link[2][0]} & {"a", "b"}]
    assert not [link for link in guessed if link[1] == "rental_duration"]
    assert "Relationship check" not in caplog.text
    declared = {(r["child_table"], r["parent_table"]) for r in profile["relationships"]
                if r["relationship_type"] == "actual"}
    assert declared == {("a_orders", "a_customers"), ("b_invoices", "b_customers")}


def test_declared_groups():
    groups = declared_groups([
        {"child_table": "x", "parent_table": "y"},
        {"child_table": "z", "parent_table": "y"},
        {"child_table": "p", "parent_table": "q"},
    ])
    assert groups["x"] == groups["y"] == groups["z"] != groups["p"] == groups["q"]
