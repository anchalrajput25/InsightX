from retriever import retrieve_relevant_schema
from graph_rag import (
    retrieve_relationships,
    expand_related_tables
)


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
                    "data_type": "varchar"
                },
                {
                    "column_name": "email",
                    "data_type": "varchar"
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
                    "column_name": "product_name",
                    "data_type": "varchar"
                },
                {
                    "column_name": "category",
                    "data_type": "varchar"
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
            "source_table": "orders",
            "source_column": "product_id",
            "target_table": "products",
            "target_column": "product_id",
            "type": "inferred",
            "confidence": "High"
        }

    ]
}


query = """
Which customers spent the most money
on products in the last 6 months?
"""


# ---------------------------------------------------------
# Hybrid Retrieval
# ---------------------------------------------------------

retrieved = retrieve_relevant_schema(
    schema,
    query,
    top_k=8
)


print("\n=== HYBRID RETRIEVAL ===")

for item in retrieved:

    print(
        f"\nTable: {item['table']}"
    )

    print(
        f"Keyword Score: "
        f"{item['keyword_score']}"
    )

    print(
        f"Semantic Score: "
        f"{item['semantic_score']}"
    )

    print(
        f"Hybrid Score: "
        f"{item['hybrid_score']}"
    )


# ---------------------------------------------------------
# Graph Expansion
# ---------------------------------------------------------

initial_tables = [
    item["table"]
    for item in retrieved
]


expanded_tables = expand_related_tables(
    schema,
    initial_tables
)


print("\n=== GRAPH EXPANDED TABLES ===")

for table in expanded_tables:
    print(table)


# ---------------------------------------------------------
# Relationship Retrieval
# ---------------------------------------------------------

relationships = retrieve_relationships(
    schema,
    expanded_tables
)


print("\n=== GRAPH RELATIONSHIPS ===")

for relation in relationships:
    print(relation)