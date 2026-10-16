# Structured schema linking: JSON parsing and deterministic verification
# against the schema (synthetic, multi-domain schemas; no database).

import json

from backend.text_to_sql.schema_linker import parse_linking
from backend.text_to_sql.schema_verifier import render_linked_schema, verify_linking


def col(name, data_type="TEXT", values=None):
    column = {"column_name": name, "data_type": data_type}
    if values is not None:
        column["values"] = values
    return column


def rel(source, source_column, target, target_column, kind="foreign_key"):
    return {"source_table": source, "source_column": source_column,
            "target_table": target, "target_column": target_column,
            "type": kind, "confidence": "confirmed"}


SCHEMA = {
    "tables": [
        {"table_name": "employees", "primary_key": ["employee_id"], "columns": [
            col("employee_id", "BIGINT"), col("full_name"), col("department_id", "BIGINT"), col("salary", "NUMERIC")]},
        {"table_name": "departments", "primary_key": ["department_id"], "columns": [
            col("department_id", "BIGINT"), col("department_name", values=["Finance", "Research", "Customer Support"])]},
        {"table_name": "tickets", "primary_key": ["ticket_id"], "columns": [
            col("ticket_id", "BIGINT"), col("assignee_id", "BIGINT"),
            col("ticket_status", values=["open", "closed", "canceled"]), col("opened_at", "TIMESTAMP")]},
        {"table_name": "sites", "primary_key": ["site_code"], "columns": [col("site_code"), col("city", values=["São Paulo", "Lisboa"])]},
    ],
    "relationships": [
        rel("employees", "department_id", "departments", "department_id"),
        rel("tickets", "assignee_id", "employees", "employee_id", "inferred"),
    ],
}


def verify(parsed, context=None):
    return verify_linking(parsed, SCHEMA, context)


# ============================================================
# PARSING
# ============================================================

def test_parse_linking_tolerates_reasoning_fences_and_prose():
    payload = {"tables": ["tickets"], "columns": []}
    raw = f"<think>tables...{{not json}}</think>Here you go:\n```json\n{json.dumps(payload)}\n```\nDone."
    assert parse_linking(raw) == payload
    assert parse_linking(f"Sure. {json.dumps(payload)} hope it helps") == payload
    assert parse_linking("Final Relevant Schema: tickets") is None


# ============================================================
# TABLES AND COLUMNS
# ============================================================

def test_valid_mapping_is_kept_and_joins_marked_as_relationships():
    linked = verify({
        "tables": ["tickets", "employees"],
        "columns": [{"table": "tickets", "column": "ticket_status", "role": "filter"}],
        "joins": [{"left": "tickets.assignee_id", "right": "employees.employee_id"}],
    })
    assert linked["status"] == "verified"
    assert linked["tables"] == ["tickets", "employees"]
    assert linked["joins"][0]["verified"] == "relationship"
    assert linked["issues"] == [] and linked["corrections"] == []


def test_unknown_tables_are_dropped_and_near_misses_fixed():
    linked = verify({"tables": ["employes", "invoices", "public.departments"]})
    assert linked["tables"][:2] == ["employees", "departments"]
    assert "invoices" not in linked["tables"]
    assert any("employes" in text for text in linked["corrections"])
    assert any("invoices" in text for text in linked["issues"])


def test_columns_are_moved_to_the_table_that_has_them_or_dropped():
    linked = verify({
        "tables": ["employees", "departments"],
        "columns": [
            {"table": "employees", "column": "department_name"},   # lives in departments
            {"table": "employees", "column": "salry"},             # typo
            {"table": "employees", "column": "bonus"},             # does not exist
            {"column": "tickets.ticket_status"},                    # qualified name
        ],
    })
    found = {(item["table"], item["column"]) for item in linked["columns"]}
    assert ("departments", "department_name") in found
    assert ("employees", "salary") in found
    assert ("tickets", "ticket_status") in found and "tickets" in linked["tables"]
    assert not any(column == "bonus" for _, column in found)
    assert any("bonus" in text for text in linked["issues"])


# ============================================================
# JOINS
# ============================================================

def test_invalid_joins_are_dropped_and_unknown_ones_flagged():
    linked = verify({
        "tables": ["tickets", "employees", "sites"],
        "joins": [
            {"left": "tickets.assignee_id", "right": "employees.missing_col"},
            {"left": "employees.full_name", "right": "tickets.ticket_id"},       # text = bigint
            {"left": "sites.site_code", "right": "employees.full_name"},          # exists, no relationship
        ],
    })
    conditions = {join["condition"]: join for join in linked["joins"]}
    assert "sites.site_code = employees.full_name" in conditions
    assert conditions["sites.site_code = employees.full_name"]["verified"] == "unverified"
    assert any("missing_col" in text for text in linked["issues"])
    assert any("different types" in text for text in linked["issues"])


def test_disconnected_tables_are_connected_through_the_relationship_graph():
    linked = verify({"tables": ["tickets", "departments"]})  # needs employees in between
    assert "employees" in linked["tables"]
    conditions = [join["condition"] for join in linked["joins"]]
    assert "tickets.assignee_id = employees.employee_id" in conditions
    assert "employees.department_id = departments.department_id" in conditions
    assert all(join["source"] == "relationship graph" for join in linked["joins"])


# ============================================================
# FILTERS AND METRICS
# ============================================================

def test_filter_values_are_corrected_to_the_stored_spelling():
    linked = verify({
        "tables": ["tickets", "sites"],
        "filters": [
            {"table": "tickets", "column": "ticket_status", "value": "cancelled"},
            {"table": "sites", "column": "city", "value": "sao paulo"},
            {"table": "tickets", "column": "ticket_status", "value": "open"},
            {"table": "tickets", "column": "ticket_status", "value": "escalated"},
        ],
    })
    by_value = {item.get("original_value", item["value"]): item for item in linked["filters"]}
    assert by_value["cancelled"]["value"] == "canceled" and by_value["cancelled"]["check"] == "corrected"
    assert by_value["sao paulo"]["value"] == "São Paulo"
    assert by_value["open"]["check"] == "stored_value"
    assert by_value["escalated"]["check"] == "not_found"


def test_metrics_must_reference_existing_columns():
    linked = verify({
        "tables": ["employees"],
        "metrics": [
            {"name": "payroll", "expression": "SUM(employees.salary)"},
            {"name": "margin", "expression": "SUM(employees.revenue - employees.cost)"},
            {"name": "broken", "expression": "SUM(("},
        ],
    })
    assert [metric["name"] for metric in linked["metrics"]] == ["payroll"]
    assert any("margin" in text for text in linked["issues"])
    assert any("broken" in text for text in linked["issues"])


# ============================================================
# FALLBACK AND RENDERING
# ============================================================

def test_unusable_output_falls_back_to_retrieval():
    context = {"relevant_tables": ["employees", "departments"],
               "join_paths": ["employees.department_id = departments.department_id"]}
    for parsed in (None, {"tables": ["nope"]}):
        linked = verify(parsed, context)
        assert linked["status"] == "fallback"
        assert linked["tables"] == ["employees", "departments"]
        assert linked["joins"][0]["condition"] == "employees.department_id = departments.department_id"


def test_rendered_schema_lists_columns_values_joins_and_warnings():
    linked = verify({
        "tables": ["tickets", "employees"],
        "columns": [{"table": "tickets", "column": "ticket_status", "role": "filter"}],
        "filters": [{"table": "tickets", "column": "ticket_status", "value": "cancelled"}],
        "ambiguities": ["'handled' could mean assigned or closed"],
    })
    context = {"value_hints": [{"table": "employees", "column": "full_name", "value": "O'Neil", "matched_text": "oneil"}]}
    text = render_linked_schema(linked, SCHEMA, context)
    assert "ticket_status TEXT  [linked: filter]  stored values: 'open', 'closed', 'canceled'" in text
    assert "salary NUMERIC" in text  # unlinked columns of linked tables stay visible
    assert "tickets.assignee_id = employees.employee_id   (known relationship)" in text
    assert "tickets.ticket_status = 'canceled'   (corrected from 'cancelled'" in text
    assert "employees.full_name = 'O''Neil'" in text
    assert "'handled' could mean assigned or closed" in text
