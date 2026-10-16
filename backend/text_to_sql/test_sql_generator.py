from query_rewriter import rewrite_query
from query_decomposer import decompose_query
from schema_linker import link_schema
from sql_generator import generate_sql


question = """
Which customers generated the highest revenue growth
in the last year compared with the previous year,
excluding refunded orders, and which product categories
contributed most to that growth?
"""


# ============================================================
# SAMPLE SCHEMA
# ============================================================

schema = {
    "tables": [
        {
            "table_name": "customers",
            "columns": [
                {
                    "column_name": "customer_id",
                    "data_type": "integer"
                },
                {
                    "column_name": "name",
                    "data_type": "text"
                }
            ]
        },

        {
            "table_name": "orders",
            "columns": [
                {
                    "column_name": "order_id",
                    "data_type": "integer"
                },
                {
                    "column_name": "customer_id",
                    "data_type": "integer"
                },
                {
                    "column_name": "order_date",
                    "data_type": "date"
                },
                {
                    "column_name": "amount",
                    "data_type": "numeric"
                },
                {
                    "column_name": "status",
                    "data_type": "text"
                }
            ]
        },

        {
            "table_name": "order_items",
            "columns": [
                {
                    "column_name": "order_item_id",
                    "data_type": "integer"
                },
                {
                    "column_name": "order_id",
                    "data_type": "integer"
                },
                {
                    "column_name": "product_id",
                    "data_type": "integer"
                }
            ]
        },

        {
            "table_name": "products",
            "columns": [
                {
                    "column_name": "product_id",
                    "data_type": "integer"
                },
                {
                    "column_name": "category_id",
                    "data_type": "integer"
                },
                {
                    "column_name": "product_name",
                    "data_type": "text"
                }
            ]
        },

        {
            "table_name": "categories",
            "columns": [
                {
                    "column_name": "category_id",
                    "data_type": "integer"
                },
                {
                    "column_name": "category_name",
                    "data_type": "text"
                }
            ]
        }
    ],

    "relationships": [
        {
            "source_table": "orders",
            "source_column": "customer_id",
            "target_table": "customers",
            "target_column": "customer_id",
            "type": "inferred",
            "confidence": "High"
        },

        {
            "source_table": "order_items",
            "source_column": "order_id",
            "target_table": "orders",
            "target_column": "order_id",
            "type": "inferred",
            "confidence": "High"
        },

        {
            "source_table": "order_items",
            "source_column": "product_id",
            "target_table": "products",
            "target_column": "product_id",
            "type": "inferred",
            "confidence": "High"
        },

        {
            "source_table": "products",
            "source_column": "category_id",
            "target_table": "categories",
            "target_column": "category_id",
            "type": "inferred",
            "confidence": "High"
        }
    ]
}


# ============================================================
# 1. QUERY REWRITING
# ============================================================

print("\nRunning Query Rewriter...")

rewritten = rewrite_query(question)

print("\n" + "=" * 80)
print("QUERY REWRITER")
print("=" * 80)
print(rewritten)


# ============================================================
# 2. QUERY DECOMPOSITION
# ============================================================

print("\nRunning Query Decomposer...")

decomposed = decompose_query(
    question,
    rewritten
)

print("\n" + "=" * 80)
print("QUERY DECOMPOSER")
print("=" * 80)
print(decomposed)


# ============================================================
# 3. SCHEMA LINKING
# ============================================================

print("\nRunning Schema Linker...")

linked_schema = link_schema(
    question=question,
    rewritten_query=rewritten,
    decomposed_query=decomposed,
    schema=schema
)

print("\n" + "=" * 80)
print("SCHEMA LINKER")
print("=" * 80)
print(linked_schema)


# ============================================================
# 4. SQL GENERATION
# ============================================================

print("\nRunning SQL Generator...")

result = generate_sql(
    question=question,
    rewritten_query=rewritten,
    decomposed_query=decomposed,
    linked_schema=linked_schema
)

print("\n" + "=" * 80)
print("SQL GENERATOR")
print("=" * 80)
print(result)