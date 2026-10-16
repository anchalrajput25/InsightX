#Ye connected PostgreSQL database ka dynamic schema fetch karega.
from sqlalchemy import inspect, text

from backend import dialects


def get_database_schema(engine) -> dict:
    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    tables = []
    relationships = []

    for table_name in table_names:
        columns = inspector.get_columns(table_name)
        primary_key = inspector.get_pk_constraint(table_name)
        foreign_keys = inspector.get_foreign_keys(table_name)

        # Table name backend se discover hua hai.
        # Isliye arbitrary table names ke liye quoted identifier use kar rahe hain.
        quoted_table_name = dialects.quote(table_name, engine)

        with engine.connect() as connection:
            row_count = connection.execute(
                text(f"SELECT COUNT(*) FROM {quoted_table_name}")
            ).scalar()

        formatted_columns = [
            {
                "name": column["name"],
                "data_type": str(column["type"]),
                "nullable": column.get("nullable"),
            }
            for column in columns
        ]

        formatted_foreign_keys = []

        for foreign_key in foreign_keys:
            relationship = {
                "table": table_name,
                "columns": foreign_key.get("constrained_columns", []),
                "referred_table": foreign_key.get("referred_table"),
                "referred_columns": foreign_key.get(
                    "referred_columns", []
                ),
            }

            formatted_foreign_keys.append(relationship)
            relationships.append(relationship)

        tables.append(
            {
                "table_name": table_name,
                "row_count": int(row_count or 0),
                "columns": formatted_columns,
                "primary_key": primary_key.get(
                    "constrained_columns", []
                ),
                "foreign_keys": formatted_foreign_keys,
            }
        )

    return {
        "total_tables": len(tables),
        "tables": tables,
        "relationships": relationships,
    }