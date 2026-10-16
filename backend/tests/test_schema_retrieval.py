# Retrieval that scales: column-level hybrid index, value index,
# join-path planning and the focused schema given to the LLM.
#
# Schemas are synthetic and span several domains (HR, inventory,
# support tickets, sales) so nothing depends on one dataset.

import json

import pytest
from sqlalchemy import text

from backend.text_to_sql import retriever
from backend.text_to_sql.graph_rag import plan_join_paths
from backend.text_to_sql.retriever import retrieve_for_queries, tokenize
from backend.text_to_sql.schema_focus import build_schema_focus, focus_notes, prioritize_schema
from backend.text_to_sql.value_index import match_values


@pytest.fixture(autouse=True)
def lexical_only(monkeypatch):
    monkeypatch.setattr(retriever, "semantic_similarities", lambda query, texts: None)


def col(name, data_type="TEXT", values=None):
    column = {"column_name": name, "data_type": data_type, "description": "", "nullable": True}
    if values is not None:
        column["values"] = values
    return column


def rel(source, source_column, target, target_column, kind="foreign_key", confidence="confirmed"):
    return {
        "source_table": source, "source_column": source_column,
        "target_table": target, "target_column": target_column,
        "type": kind, "confidence": confidence,
    }


def multi_domain_schema(filler_tables: int = 40):
    tables = [
        {"table_name": "employees", "primary_key": ["employee_id"], "columns": [
            col("employee_id", "BIGINT"), col("full_name"), col("department_id", "BIGINT"),
            col("hireDate", "DATE"), col("job_title", values=["Engineer", "Manager", "Analyst"]),
        ]},
        {"table_name": "departments", "primary_key": ["department_id"], "columns": [
            col("department_id", "BIGINT"), col("department_name", values=["Finance", "Research", "Support"]),
        ]},
        {"table_name": "support_tickets", "primary_key": ["ticket_id"], "columns": [
            col("ticket_id", "BIGINT"), col("assignee_id", "BIGINT"), col("customer_id", "BIGINT"),
            col("priority", values=["low", "medium", "high", "urgent"]),
            col("ticket_status", values=["open", "closed", "canceled"]), col("opened_at", "TIMESTAMP"),
        ]},
        {"table_name": "customers", "primary_key": ["customer_id"], "columns": [
            col("customer_id", "BIGINT"), col("city", values=["São Paulo", "Lisboa", "New York", "York"]),
            col("country", values=["Brazil", "Portugal", "USA", "UK"]),
        ]},
        {"table_name": "warehouse_stock", "primary_key": ["sku"], "columns": [
            col("sku"), col("quantity_on_hand", "BIGINT"), col("product_category", values=["Produce", "Dairy"]),
        ]},
    ]
    for number in range(filler_tables):
        tables.append({"table_name": f"audit_log_{number:02d}", "primary_key": [], "columns": [
            col("event_id", "BIGINT"), col("payload"), col("recorded_at", "TIMESTAMP"),
        ]})
    relationships = [
        rel("employees", "department_id", "departments", "department_id"),
        rel("support_tickets", "assignee_id", "employees", "employee_id", "inferred", "medium"),
        rel("support_tickets", "customer_id", "customers", "customer_id", "inferred", "high"),
    ]
    return {"tables": tables, "relationships": relationships}


# ============================================================
# TOKENIZATION AND COLUMN-LEVEL RANKING
# ============================================================

def test_tokenizer_splits_identifiers_and_drops_question_words():
    assert tokenize("hireDate") == ["hire", "date"]
    assert tokenize("How many customers per city?") == ["customer", "city"]
    assert tokenize("shipped_date") == tokenize("shipped") + ["date"]
    # whole tokens, not substrings: "count" is not "country"
    assert "country" not in tokenize("count of rows")


def test_column_match_ranks_the_right_table_among_many():
    schema = multi_domain_schema()
    ranking = retrieve_for_queries(schema, ["urgent tickets by priority"], top_k=3)
    assert ranking[0]["table"] == "support_tickets"
    assert ranking[0]["matched_columns"][0]["column"] == "priority"
    assert ranking[0]["relevance"] == 1.0


def test_relevance_is_relative_and_averaged_over_variants():
    schema = multi_domain_schema(filler_tables=2)
    ranking = retrieve_for_queries(schema, ["department name", "employee job title"], top_k=5)
    by_table = {item["table"]: item for item in ranking}
    assert 0 < by_table["departments"]["relevance"] < 1
    assert by_table["employees"]["matched_queries"] == 2


# ============================================================
# VALUE INDEX
# ============================================================

def hints_for(question, schema=None):
    return [(h["table"], h["column"], h["value"], h["match"]) for h in match_values(schema or multi_domain_schema(2), [question])]


def test_values_match_ignoring_accents_and_case():
    assert ("customers", "city", "São Paulo", "exact") in hints_for("tickets from sao paulo customers")


def test_spelling_variants_match_fuzzily():
    assert ("support_tickets", "ticket_status", "canceled", "fuzzy") in hints_for("How many tickets were cancelled?")


def test_longer_phrase_wins_over_words_inside_it():
    hints = hints_for("customers in New York")
    assert ("customers", "city", "New York", "exact") in hints
    assert not any(value == "York" for _, _, value, _ in hints)


def test_schema_words_are_not_fuzzily_matched_to_values():
    # "product" names a column concept; it must not become 'Produce'
    assert not any(value == "Produce" for _, _, value, _ in hints_for("stock per product category"))
    # an exact mention still matches
    assert ("warehouse_stock", "product_category", "Produce", "exact") in hints_for("stock of produce")


def test_short_and_numeric_values_are_not_indexed():
    schema = {"tables": [{"table_name": "t", "columns": [col("code", values=["UK", "42", "x1"])]}]}
    assert match_values(schema, ["uk 42 x1"]) == []


def test_values_found_in_too_many_columns_are_ignored():
    tables = [{"table_name": f"t{i}", "columns": [col("flag", values=["unknown"])]} for i in range(8)]
    assert match_values({"tables": tables}, ["unknown rows"]) == []


# ============================================================
# JOIN-PATH PLANNING
# ============================================================

def chain_schema():
    names = ["a", "b", "c", "d", "e"]
    return {
        "tables": [{"table_name": name, "columns": [col("id")]} for name in names + ["island"]],
        "relationships": [rel(x, f"{y}_id", y, "id") for x, y in zip(names, names[1:])],
    }


def test_connector_tables_are_added_only_on_the_path():
    plan = plan_join_paths(chain_schema(), ["a", "d"])
    assert plan["tables"] == ["a", "b", "c", "d"]
    assert plan["connectors"] == ["b", "c"]
    assert [join["condition"] for join in plan["joins"]] == ["a.b_id = b.id", "b.c_id = c.id", "c.d_id = d.id"]
    assert "e" not in plan["tables"]


def test_unreachable_terminals_are_kept_and_reported():
    plan = plan_join_paths(chain_schema(), ["a", "island", "b"])
    assert plan["unreachable"] == ["island"]
    assert set(plan["tables"]) == {"a", "island", "b"}


def test_weak_terminals_never_pull_in_connectors():
    plan = plan_join_paths(chain_schema(), ["a", "e", "b"], weak_terminals={"e", "b"})
    assert plan["dropped"] == ["e"]
    assert plan["tables"] == ["a", "b"] and plan["connectors"] == []


def test_declared_foreign_keys_are_preferred_over_inferred_paths():
    schema = {
        "tables": [{"table_name": name, "columns": []} for name in ("x", "fk_hop", "guess_hop", "z")],
        "relationships": [
            rel("x", "g_id", "guess_hop", "id", "inferred", "medium"),
            rel("guess_hop", "z_id", "z", "id", "inferred", "medium"),
            rel("x", "f_id", "fk_hop", "id"),
            rel("fk_hop", "z_id", "z", "id"),
        ],
    }
    assert plan_join_paths(schema, ["x", "z"])["connectors"] == ["fk_hop"]


def test_a_terminal_reached_as_connector_is_not_listed_as_connector():
    plan = plan_join_paths(chain_schema(), ["a", "c", "b"])
    assert plan["connectors"] == []
    assert plan["tables"] == ["a", "b", "c"]


# ============================================================
# FOCUSED SCHEMA
# ============================================================

def test_focus_sends_a_small_connected_slice_of_a_large_schema():
    schema = multi_domain_schema(filler_tables=60)
    question = "Which department handled the most urgent support tickets?"
    focus = build_schema_focus(schema, [question], question, top_k=8)

    context = focus["graph_context"]
    assert {"support_tickets", "departments", "employees"} <= set(context["relevant_tables"])
    assert not any(name.startswith("audit_log") for name in context["relevant_tables"])
    assert "support_tickets.assignee_id = employees.employee_id" in context["join_paths"]
    assert "employees.department_id = departments.department_id" in context["join_paths"]
    assert ("support_tickets", "priority", "urgent") in {(h["table"], h["column"], h["value"]) for h in context["value_hints"]}

    focused = focus["focused_schema"]
    assert "audit_log_00" in focused["other_tables_not_shown"]
    assert len(json.dumps(focused)) < len(json.dumps(schema)) / 3


def test_value_hint_brings_in_the_table_that_stores_the_value():
    schema = multi_domain_schema(filler_tables=5)
    question = "How many tickets came from Lisboa?"
    focus = build_schema_focus(schema, [question], question)
    assert "customers" in focus["graph_context"]["retrieved_tables"]
    assert "support_tickets.customer_id = customers.customer_id" in focus["graph_context"]["join_paths"]


def test_wide_tables_keep_key_join_hint_and_matched_columns():
    columns = [col("order_id", "BIGINT"), col("customer_id", "BIGINT"), col("channel", values=["online", "store"])]
    columns += [col(f"metric_{n:02d}", "NUMERIC") for n in range(60)]
    columns.append(col("discount_rate", "NUMERIC"))
    schema = {
        "tables": [
            {"table_name": "sales", "primary_key": ["order_id"], "columns": columns},
            {"table_name": "customers", "primary_key": ["customer_id"], "columns": [col("customer_id", "BIGINT"), col("segment")]},
        ],
        "relationships": [rel("sales", "customer_id", "customers", "customer_id")],
    }
    question = "average discount rate of online sales per customer segment"
    focus = build_schema_focus(schema, [question], question, wide_table_columns=10)
    sales = next(t for t in focus["focused_schema"]["tables"] if t["table_name"] == "sales")
    kept = {c["column_name"] for c in sales["columns"]}
    assert {"order_id", "customer_id", "channel", "discount_rate"} <= kept
    assert sales["omitted_columns"] == len(columns) - len(kept)


def test_no_match_falls_back_to_the_full_schema():
    schema = multi_domain_schema(filler_tables=1)
    focus = build_schema_focus(schema, ["zzz qqq"], "zzz qqq")
    assert focus["focused_schema"] is schema
    assert focus["graph_context"]["fallback"] == "full_schema"


def test_focus_notes_and_prioritized_schema():
    notes = focus_notes({
        "join_paths": ["a.x = b.y"],
        "value_hints": [{"table": "t", "column": "c", "value": "O'Brien", "matched_text": "o brien", "match": "fuzzy"}],
    })
    assert "- a.x = b.y" in notes
    assert "t.c = 'O''Brien'" in notes
    assert focus_notes({}) == ""

    schema = multi_domain_schema(filler_tables=2)
    ordered = prioritize_schema(schema, ["customers", "departments"])
    assert [t["table_name"] for t in ordered["tables"][:2]] == ["customers", "departments"]
    assert len(ordered["tables"]) == len(schema["tables"])


# ============================================================
# PROFILER: value index and differently named joins (database)
# ============================================================

@pytest.fixture
def rel_schema(engine):
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS itest_rel CASCADE"))
        connection.execute(text("CREATE SCHEMA itest_rel"))
        connection.execute(text(
            "CREATE TABLE itest_rel.carriers AS SELECT g AS carrier_id, 'carrier ' || g AS carrier_name "
            "FROM generate_series(1, 4) g"
        ))
        connection.execute(text(
            "CREATE TABLE itest_rel.shipments AS SELECT g AS shipment_id, 1 + g % 4 AS carried_by, "
            "1 + g % 3 AS box_count, CASE WHEN g % 3 = 0 THEN 'delayed' ELSE 'on time' END AS shipment_state, "
            "'note ' || g AS free_text, '+1-555-000' || (g % 4) AS contact_phone, "
            "'Driver ' || (g % 5) AS driver_full_name FROM generate_series(1, 300) g"
        ))
    yield "itest_rel"
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS itest_rel CASCADE"))


def test_profiler_collects_values_and_infers_differently_named_joins(engine, rel_schema):
    from backend.schema_profiler import get_database_schema
    from backend.text_to_sql.schema_context import build_agent_schema

    profile = get_database_schema(engine, schema_name=rel_schema)
    found = {
        (r["child_table"], r["child_column"], r["parent_table"], r["parent_column"])
        for r in profile["relationships"]
    }
    # "carried_by" shares a word stem with "carriers" and its values are carrier ids
    assert ("shipments", "carried_by", "carriers", "carrier_id") in found
    # numbers that merely look like ids, under an unrelated name, are not linked
    assert not any(child_column == "box_count" for _, child_column, _, _ in found)

    schema = build_agent_schema(profile)
    shipments = next(t for t in schema["tables"] if t["table_name"] == "shipments")
    columns = {c["column_name"]: c for c in shipments["columns"]}
    assert columns["shipment_state"]["values"] == ["on time", "delayed"]  # most frequent first
    assert "values" not in columns["free_text"]  # high cardinality is never collected
    # contact details and personal names are never collected, however few values they have
    assert "values" not in columns["contact_phone"]
    assert "values" not in columns["driver_full_name"]


# ---- big schemas: glued names, database comments, named tables ----

def _table(name, columns, description=""):
    return {"table_name": name, "description": description,
            "columns": [{"column_name": c, "data_type": "integer"} for c in columns]}


def test_glued_names_are_split_into_words():
    splitter = retriever.GluedWordSplitter(retriever.COMMON_SCHEMA_WORDS)
    assert splitter.split("salesorderheader") == ["sales", "order", "header"]
    assert splitter.split("totaldue") == ["total", "due"]
    assert splitter.split("customer") == [] and splitter.split("therapist") == []
    schema = {"tables": [
        _table("salesorderheader", ["salesorderid", "orderdate", "totaldue"]),
        _table("productreview", ["productreviewid", "rating"]),
    ]}
    ranking = retrieve_for_queries(schema, ["total sales per order date"])
    assert ranking[0]["table"] == "salesorderheader"


def test_table_comments_feed_the_meaning_search_only():
    schema = {"tables": [
        _table("t1", ["a", "b"], description="Customer complaints and refunds"),
        _table("t2", ["c", "d"], description="Warehouse stock levels"),
    ]}
    index = retriever.SchemaIndex(schema)
    assert "Customer complaints and refunds" in index.table_texts[0]
    assert index.table_bm25.scores(tokenize("complaints")).max() == 0


def test_a_table_the_question_names_is_kept():
    # many line tables match "unit price", "quantity", "discount"; the
    # small category table must still reach the LLM
    lines = [_table(name, ["unit_price", "quantity", "discount", "revenue"])
             for name in ("order_detail", "invoice_line", "sales_line", "purchase_line", "quote_line")]
    schema = {"tables": lines + [
        _table("products", ["product_id", "category_id", "name"]),
        _table("categories", ["category_id", "category_name"]),
    ]}
    question = "which category earned the most revenue from unit price times quantity minus discount"
    focus = build_schema_focus(schema, [question], question, top_k=3)
    assert "categories" in {table["table_name"] for table in focus["focused_schema"]["tables"]}
